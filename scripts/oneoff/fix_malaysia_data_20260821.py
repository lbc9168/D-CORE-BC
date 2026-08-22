"""Apply the evidence-backed Malaysia data corrections identified in the August 2026 audit."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable


WORKSPACE = Path(__file__).resolve().parents[2]
DEFAULT_MAIN_DB = WORKSPACE / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
DEFAULT_SOURCE_DB = WORKSPACE / "s01b_project_extractor" / "data" / "projects.db"
DEFAULT_EXPORT_DB = WORKSPACE / "s03_data_exporter" / "data" / "output" / "bri_re_export.db"
DEFAULT_PROVINCE_CACHE = WORKSPACE / "s03_data_exporter" / "data" / "province_cache.json"
LOG_DIR = Path(__file__).resolve().parent / "logs"

PROVINCE_CORRECTIONS = {
    "MYS-PV-0002": "Selangor",
    "MYS-HB-0002": "Sabah",
    "MYS-PV-0003": "Selangor",
    "MYS-PV-0005": "Penang",
    "MYS-HY-0002": "Sarawak",
    "MYS-PV-0011": "Melaka",
    "MYS-PV-0012": "Selangor",
    "MYS-PV-0013": "Kelantan",
    "MYS-HB-0009": "Selangor",
    "MYS-HB-0010": "Terengganu",
    "MYS-HB-0011": "Terengganu",
    "MYS-HB-0012": "Perak",
    "MYS-HY-0004": "Sabah",
    "MYS-HY-0006": "Sarawak",
    "MYS-HY-0007": "Kelantan",
    "MYS-PV-0024": "Pahang",
    "MYS-MF-0002": "Selangor",
}

EXPECTED_CANONICAL_IDS = {
    "MYS-PV-0001",
    "MYS-PV-0002",
    "MYS-PV-0004",
    "MYS-PV-0020",
    "MYS-BM-0004",
    "MYS-HB-0009",
    "MYS-PV-0024",
    "MYS-ES-0003",
    "MYS-MF-0016",
    "MYS-MF-0001",
    "MYS-MF-0002",
    "MYS-PV-0018",
}

EXPECTED_ENTITY_IDS = {
    69,
    24,
    38,
    39,
    2707,
    2708,
    2876,
    2877,
    2946,
    3846,
    4608,
    6526,
    6527,
    6528,
    6571,
    6572,
}

EXPECTED_SOURCE_IDS = {
    39,
    40,
    56,
    57,
    1391,
    4135,
    4202,
    1689,
    1814,
    3073,
    3074,
    3075,
    3076,
    4216,
    4228,
    4229,
    4326,
    4581,
    4582,
    4706,
    4797,
    6454,
    6455,
    6650,
    7803,
    9415,
    4833,
}


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _validate_input_path(path: Path, suffix: str) -> Path:
    """Resolve and validate an existing input path inside the workspace."""
    resolved = path.resolve()
    if not resolved.is_relative_to(WORKSPACE.resolve()):
        raise ValueError(f"路径必须位于工作区内: {resolved}")
    if not resolved.is_file() or resolved.suffix.lower() != suffix:
        raise ValueError(f"缺少有效的 {suffix} 文件: {resolved}")
    return resolved


def _configure_logging(timestamp: str) -> Path:
    """Configure file logging and return the log path."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"fix_malaysia_data_{timestamp}.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        encoding="utf-8",
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return log_path


def _backup_database(path: Path, timestamp: str) -> Path:
    """Create a consistent SQLite backup beside the source database."""
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.before_malaysia_fix_{timestamp}{path.suffix}"
    with sqlite3.connect(path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
    logging.info("SQLite backup created: %s", backup_path)
    return backup_path


def _backup_file(path: Path, timestamp: str) -> Path:
    """Create a recoverable copy of a non-database data file."""
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.before_malaysia_fix_{timestamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    logging.info("File backup created: %s", backup_path)
    return backup_path


def _checkpoint_wal(path: Path) -> None:
    """Checkpoint and truncate a SQLite WAL so the reported database hash is self-contained."""
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    logging.info("SQLite WAL checkpoint completed: %s", path)


def _require_ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    expected: Iterable[object],
) -> None:
    """Require an exact set of target IDs to exist before mutating data."""
    expected_set = set(expected)
    placeholders = ",".join("?" for _ in expected_set)
    rows = connection.execute(
        f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})",
        tuple(expected_set),
    ).fetchall()
    found = {row[0] for row in rows}
    if found != expected_set:
        raise RuntimeError(
            f"前置校验失败 {table}.{column}: missing={sorted(expected_set - found)}"
        )


def _execute_exact(
    connection: sqlite3.Connection,
    sql: str,
    params: tuple[object, ...],
    expected_rows: int,
    label: str,
) -> None:
    """Execute one mutation and require the expected matched-row count."""
    cursor = connection.execute(sql, params)
    if cursor.rowcount != expected_rows:
        raise RuntimeError(
            f"{label} 影响行数异常: expected={expected_rows}, actual={cursor.rowcount}"
        )
    logging.info("%s: %s row(s)", label, cursor.rowcount)


def _json_union(*raw_values: object) -> str:
    """Combine JSON URL arrays while preserving first-seen order."""
    result: list[str] = []
    for raw in raw_values:
        if not raw:
            continue
        parsed = json.loads(str(raw))
        if not isinstance(parsed, list):
            raise ValueError(f"预期 JSON 数组，实际为: {raw!r}")
        for value in parsed:
            text = str(value).strip()
            if text and text not in result:
                result.append(text)
    return json.dumps(result, ensure_ascii=False)


def _renumber_events(connection: sqlite3.Connection, canonical_id: str) -> None:
    """Renumber a merged project's events chronologically without breaking entity references."""
    event_ids = [
        row[0]
        for row in connection.execute(
            """
            SELECT id
            FROM project_events
            WHERE canonical_id = ?
            ORDER BY COALESCE(publish_date, investment_date, ''), id
            """,
            (canonical_id,),
        )
    ]
    mapping: list[tuple[str, str, str]] = []
    for index, old_id in enumerate(event_ids, start=1):
        temporary_id = f"__MYS_20260821_{canonical_id}_{index:02d}"
        new_id = f"{canonical_id}-{index:02d}"
        mapping.append((old_id, temporary_id, new_id))
    for old_id, temporary_id, _ in mapping:
        connection.execute("UPDATE project_events SET id = ? WHERE id = ?", (temporary_id, old_id))
        connection.execute(
            "UPDATE chinese_entities SET event_id = ? WHERE event_id = ?",
            (temporary_id, old_id),
        )
    for _, temporary_id, new_id in mapping:
        connection.execute("UPDATE project_events SET id = ? WHERE id = ?", (new_id, temporary_id))
        connection.execute(
            "UPDATE chinese_entities SET event_id = ? WHERE event_id = ?",
            (new_id, temporary_id),
        )
    logging.info("Renumbered %s events for %s", len(mapping), canonical_id)


def _merge_canonical(
    connection: sqlite3.Connection,
    target_id: str,
    source_id: str,
) -> None:
    """Move every source event to a retained canonical project and remove the duplicate shell."""
    _execute_exact(
        connection,
        "UPDATE project_events SET canonical_id = ? WHERE canonical_id = ?",
        (target_id, source_id),
        connection.execute(
            "SELECT COUNT(*) FROM project_events WHERE canonical_id = ?", (source_id,)
        ).fetchone()[0],
        f"merge events {source_id} -> {target_id}",
    )
    connection.execute(
        "DELETE FROM project_classification_shadow WHERE canonical_id = ?", (source_id,)
    )
    _execute_exact(
        connection,
        "DELETE FROM canonical_projects WHERE id = ?",
        (source_id,),
        1,
        f"delete duplicate canonical {source_id}",
    )
    _renumber_events(connection, target_id)


def _consolidate_lss5(connection: sqlite3.Connection) -> None:
    """Collapse the two same-day LSS5 BSSB construction records into one evidence-rich event."""
    target = connection.execute(
        "SELECT * FROM project_events WHERE id = 'MYS-PV-0001-01'"
    ).fetchone()
    source = connection.execute(
        "SELECT * FROM project_events WHERE id = 'MYS-PV-0002-01'"
    ).fetchone()
    if target is None or source is None:
        raise RuntimeError("LSS5 合并前置事件缺失")
    source_links = _json_union(target["source_links"], source["source_links"])
    official_links = _json_union(
        target["official_website_links"], source["official_website_links"]
    )
    non_official_links = _json_union(
        target["non_official_website_links"], source["non_official_website_links"]
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET capacity_mw_raw = '300MWac',
            capacity_mw = 300.0,
            pv_capacity_mw = 459.27,
            capacity_details = '300MWac / 459.27MWp',
            project_developer = 'BESTARI SOLAR (BSSB)',
            project_developer_en = 'Bestari Solar Sdn. Bhd. (BSSB)',
            source_links = ?,
            official_website_links = ?,
            non_official_website_links = ?,
            publish_date = '2026-02-14 10:49:03',
            project_summary = '中国能建江苏院承建马来西亚雪兰莪州LSS5 BSSB 300MWac（459.27MWp）漂浮式光伏项目；项目于2026年2月10日开工。'
        WHERE id = 'MYS-PV-0001-01'
        """,
        (source_links, official_links, non_official_links),
        1,
        "consolidate LSS5 event",
    )
    _execute_exact(
        connection,
        "DELETE FROM chinese_entities WHERE id = 38",
        (),
        1,
        "delete duplicate LSS5 parent entity",
    )
    _execute_exact(
        connection,
        """
        UPDATE chinese_entities
        SET subsidiary_name = '中国能源建设集团江苏省电力设计院有限公司',
            subsidiary_name_en = 'CEEC Jiangsu Electric Power Design Institute Co., Ltd.',
            entity_en_quality = 'manual_verified', involvement_type = 'EPC总承包'
        WHERE id = 24
        """,
        (),
        1,
        "enrich retained LSS5 Jiangsu Institute entity",
    )
    _execute_exact(
        connection,
        "DELETE FROM chinese_entities WHERE id = 39",
        (),
        1,
        "delete second duplicate LSS5 parent entity",
    )
    _execute_exact(
        connection,
        "DELETE FROM project_events WHERE id = 'MYS-PV-0002-01'",
        (),
        1,
        "delete duplicate LSS5 event",
    )
    connection.execute(
        "DELETE FROM project_classification_shadow WHERE canonical_id = 'MYS-PV-0002'"
    )
    _execute_exact(
        connection,
        "DELETE FROM canonical_projects WHERE id = 'MYS-PV-0002'",
        (),
        1,
        "delete duplicate LSS5 canonical",
    )
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET project_name = '马来西亚雪兰莪LSS5 BSSB 300MWac（459.27MWp）漂浮式光伏项目',
            project_name_en = 'Malaysia Selangor LSS5 BSSB 300MWac/459.27MWp Floating Solar PV Project',
            name_en_quality = 'manual_verified',
            location = '雪兰莪州',
            location_norm = '雪兰莪州'
        WHERE id = 'MYS-PV-0001'
        """,
        (),
        1,
        "standardize retained LSS5 canonical",
    )


def _apply_main_corrections(connection: sqlite3.Connection) -> None:
    """Apply all evidence-backed corrections to the canonical database."""
    connection.row_factory = sqlite3.Row
    _require_ids(connection, "canonical_projects", "id", EXPECTED_CANONICAL_IDS)
    _require_ids(connection, "chinese_entities", "id", EXPECTED_ENTITY_IDS)

    _consolidate_lss5(connection)

    _merge_canonical(connection, "MYS-PV-0004", "MYS-PV-0020")
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET project_name = '马来西亚砂拉越巴塘艾一期50MWac（69.3MWp）漂浮式光伏项目',
            project_name_en = 'Malaysia Sarawak Batang Ai Phase I 50MWac/69.3MWp Floating Solar PV Project',
            name_en_quality = 'manual_verified',
            location = '砂拉越州巴塘艾水库',
            location_norm = '砂拉越州'
        WHERE id = 'MYS-PV-0004'
        """,
        (),
        1,
        "standardize retained Batang Ai canonical",
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET capacity_mw_raw = '50MWac', capacity_mw = 50.0, pv_capacity_mw = 69.3,
            capacity_details = '50MWac / 69.3MWp'
        WHERE canonical_id = 'MYS-PV-0004'
        """,
        (),
        5,
        "standardize Batang Ai AC/DC capacity",
    )
    _execute_exact(
        connection,
        """
        UPDATE chinese_entities
        SET implementing_entity = '国家电力投资集团有限公司', implementing_entity_en = 'SPIC',
            subsidiary_name = '中国电力国际发展有限公司',
            subsidiary_name_en = 'China Power International Development Limited',
            entity_en_quality = 'manual_verified', involvement_type = '股权投资方',
            is_central_soe = 1
        WHERE id IN (2707, 2876)
        """,
        (),
        2,
        "map China Power International to SPIC",
    )
    _execute_exact(
        connection,
        """
        UPDATE chinese_entities
        SET implementing_entity = '天合光能股份有限公司', implementing_entity_en = 'Trina Solar',
            subsidiary_name = '', subsidiary_name_en = '', entity_en_quality = 'manual_verified',
            involvement_type = '股权投资方', is_central_soe = 0
        WHERE id = 2708
        """,
        (),
        1,
        "replace false PowerChina equity entity with Trina Solar",
    )
    _execute_exact(
        connection,
        "UPDATE chinese_entities SET entity_en_quality = 'manual_verified' WHERE id = 2877",
        (),
        1,
        "mark Trina equity entity manually verified",
    )
    _execute_exact(
        connection,
        """
        UPDATE chinese_entities
        SET implementing_entity = '国家电力投资集团有限公司', implementing_entity_en = 'SPIC',
            subsidiary_name = '中国电力国际发展有限公司',
            subsidiary_name_en = 'China Power International Development Limited',
            entity_en_quality = 'manual_verified', involvement_type = '股权投资方',
            is_central_soe = 1
        WHERE id = 69
        """,
        (),
        1,
        "surface verified SPIC equity in the retained Batang Ai project",
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET project_summary = REPLACE(
            project_summary,
            '中国华能集团有限公司（通过其子公司中电国际）',
            '中国电力国际发展有限公司（国家电投所属）'
        )
        WHERE canonical_id = 'MYS-PV-0004' AND source_links LIKE '%60591.html%'
        """,
        (),
        1,
        "correct Batang Ai ownership summary",
    )

    _merge_canonical(connection, "MYS-BM-0004", "MYS-HB-0009")
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET project_name = '马来西亚雪兰莪州51MW垃圾焚烧发电项目',
            project_name_en = 'Malaysia Selangor 51MW Waste-to-Energy Power Plant Project',
            name_en_quality = 'manual_verified', location = '雪兰莪州', location_norm = '雪兰莪州',
            category = '生物质'
        WHERE id = 'MYS-BM-0004'
        """,
        (),
        1,
        "standardize retained Selangor WTE canonical",
    )

    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET excluded = 1, excluded_reason = 'no_chinese_entity_citaglobal_and_masdar_are_non_chinese'
        WHERE id = 'MYS-PV-0024'
        """,
        (),
        1,
        "exclude Pahang Citaglobal-Masdar project",
    )
    _execute_exact(
        connection,
        "DELETE FROM chinese_entities WHERE id = 2946",
        (),
        1,
        "remove false Citaglobal Chinese entity",
    )
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET excluded = 1, excluded_reason = 'manufacturing_facility_not_grid_storage_asset'
        WHERE id = 'MYS-ES-0003'
        """,
        (),
        1,
        "exclude manufacturing facility misclassified as storage",
    )
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET excluded = 1,
            excluded_reason = 'conflated_duplicate_of_MYS-MF-0006_and_MYS-MF-0013'
        WHERE id = 'MYS-MF-0016'
        """,
        (),
        1,
        "exclude conflated battery-manufacturing duplicate",
    )
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET excluded = 1,
            excluded_reason = 'no_chinese_entity_bgmc_pekat_and_sand_town_are_malaysian'
        WHERE id = 'MYS-PV-0018'
        """,
        (),
        1,
        "exclude false BGMC/Pekat Malaysia project",
    )

    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET financing_model = 'Unknown', financing_model_details = ''
        WHERE id = 'MYS-HY-0005-01'
        """,
        (),
        1,
        "remove unsupported Sabah hydro blended financing",
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET financing_model = '信贷融资',
            financing_model_details = '联合国际性银行财团解决业主95%资金'
        WHERE id = 'MYS-HY-0001-03'
        """,
        (),
        1,
        "classify explicit Perak hydro bank financing",
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET financing_model = 'Unknown', financing_model_details = ''
        WHERE id = 'MYS-HB-0012-01'
        """,
        (),
        1,
        "remove unsupported Capital Ventures credit classification",
    )
    _execute_exact(
        connection,
        "UPDATE chinese_entities SET involvement_type = 'Unknown' WHERE id IN (3846, 6526, 6527, 6528, 6571, 6572)",
        (),
        6,
        "downgrade unsupported joint-development equity roles",
    )
    _execute_exact(
        connection,
        """
        UPDATE chinese_entities
        SET implementing_entity = '中国长江三峡集团有限公司', implementing_entity_en = 'CTG',
            subsidiary_name = '三峡发展（马）公司',
            subsidiary_name_en = 'Three Gorges Development (Malaysia) Corporation',
            entity_en_quality = 'manual_verified', involvement_type = 'EPC总承包',
            is_central_soe = 1
        WHERE id = 4608
        """,
        (),
        1,
        "map Three Gorges Development Malaysia to CTG",
    )
    _execute_exact(
        connection,
        """
        UPDATE project_events
        SET project_developer = 'SEB', project_developer_en = 'Sarawak Energy Berhad (SEB)'
        WHERE id = 'MYS-HY-0009-01'
        """,
        (),
        1,
        "separate Murum owner from Chinese main contractor",
    )

    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET project_name_en = 'Haisida Malaysia 2.5GWh Cylindrical Power Battery Production Base Project',
            name_en_quality = 'manual_verified'
        WHERE id = 'MYS-MF-0001'
        """,
        (),
        1,
        "correct Haisida English project name",
    )
    _execute_exact(
        connection,
        """
        UPDATE canonical_projects
        SET project_name = '马来西亚雪兰莪州天鹏电源圆柱电池工厂',
            project_name_en = 'Tianpeng Power Malaysia Selangor Cylindrical Battery Factory',
            name_en_quality = 'manual_verified', location = '雪兰莪州', location_norm = '雪兰莪州'
        WHERE id = 'MYS-MF-0002'
        """,
        (),
        1,
        "correct Tianpeng project names and location spelling",
    )


def _apply_source_corrections(connection: sqlite3.Connection) -> None:
    """Correct the upstream extracted rows that would otherwise recreate the audited errors."""
    _require_ids(connection, "source_db.extracted_projects", "id", EXPECTED_SOURCE_IDS)

    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET excluded = 1,
            exclude_reason = 'Citaglobal和Masdar均非中国企业，项目不满足中企参与范围',
            exclude_source = 'manual_audit_2026-08-21', involvement_type = 'Unknown'
        WHERE id = 4706
        """,
        (),
        1,
        "exclude upstream Citaglobal row",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET excluded = 1,
            exclude_reason = '该条为电池制造设施，不是电网侧或电站储能资产；制造业记录另行保留',
            exclude_source = 'manual_audit_2026-08-21'
        WHERE id = 4326
        """,
        (),
        1,
        "exclude upstream storage misclassification",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET excluded = 1,
            exclude_reason = 'BGMC、Pekat及Sand Town均为马来西亚企业，相关项目不满足中企参与范围',
            exclude_source = 'manual_audit_2026-08-21', involvement_type = 'Unknown'
        WHERE id IN (4135, 4833, 9415)
        """,
        (),
        3,
        "exclude upstream non-Chinese BGMC/Pekat rows",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET financing_model = 'Unknown', financing_model_details = ''
        WHERE id = 4797
        """,
        (),
        1,
        "remove upstream unsupported Sabah hydro blended financing",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET financing_model = '信贷融资',
            financing_model_details = '联合国际性银行财团解决业主95%资金'
        WHERE id IN (3073, 3074, 3075, 3076)
        """,
        (),
        4,
        "correct upstream Perak hydro financing",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET financing_model = 'Unknown', financing_model_details = ''
        WHERE id = 4202
        """,
        (),
        1,
        "remove unsupported upstream Capital Ventures credit classification",
    )
    _execute_exact(
        connection,
        "UPDATE source_db.extracted_projects SET financing_model = 'Unknown', financing_model_details = '' WHERE id = 6650",
        (),
        1,
        "remove unsupported upstream Batang Ai project financing",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET involvement_type = 'Unknown'
        WHERE id IN (6454, 6455)
        """,
        (),
        2,
        "downgrade upstream joint-development equity roles",
    )

    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET implementing_entity = '国家电力投资集团有限公司', implementing_entity_en = 'SPIC',
            subsidiary_name = '中国电力国际发展有限公司',
            subsidiary_name_en = 'China Power International Development Limited',
            name_en_quality = 'manual_verified', involvement_type = '股权投资方', is_central_soe = 1
        WHERE id IN (4216, 4228, 4581)
        """,
        (),
        3,
        "map upstream China Power International to SPIC",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET implementing_entity = '天合光能股份有限公司', implementing_entity_en = 'Trina Solar',
            subsidiary_name = '', subsidiary_name_en = '', name_en_quality = 'manual_verified',
            involvement_type = '股权投资方', is_central_soe = 0
        WHERE id IN (4229, 4582)
        """,
        (),
        2,
        "replace upstream false parent mappings with Trina Solar",
    )
    _execute_exact(
        connection,
        "UPDATE source_db.extracted_projects SET involvement_type = 'EPC总承包' WHERE id = 7803",
        (),
        1,
        "correct upstream Three Gorges Malaysia contractor role",
    )

    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET capacity_mw = '300', capacity_mw_raw = '300MWac',
            capacity_details = '300MWac / 459.27MWp'
        WHERE id IN (39, 40, 56, 57)
        """,
        (),
        4,
        "standardize upstream LSS5 AC/DC capacity",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET capacity_mw = '50', capacity_mw_raw = '50MWac',
            capacity_details = '50MWac / 69.3MWp'
        WHERE source_link LIKE '%/45942.html' OR source_link LIKE '%/56447.html'
           OR source_link LIKE '%/60442.html' OR source_link LIKE '%/60591.html'
        """,
        (),
        7,
        "standardize upstream Batang Ai AC/DC capacity",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET category = '生物质'
        WHERE source_link LIKE '%/39679.html' OR source_link LIKE '%/41464.html'
           OR source_link LIKE '%/44833.html' OR source_link LIKE '%/75769.html'
        """,
        (),
        4,
        "standardize upstream Selangor WTE category",
    )

    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET project_name_en = 'Haisida Malaysia 2.5GWh Cylindrical Power Battery Production Base Project',
            name_en_quality = 'manual_verified'
        WHERE id = 1689
        """,
        (),
        1,
        "correct upstream Haisida English name",
    )
    _execute_exact(
        connection,
        """
        UPDATE source_db.extracted_projects
        SET project_name = '马来西亚雪兰莪州天鹏电源圆柱电池工厂',
            project_name_en = 'Tianpeng Power Malaysia Selangor Cylindrical Battery Factory',
            name_en_quality = 'manual_verified', location = '雪兰莪州'
        WHERE id = 1814
        """,
        (),
        1,
        "correct upstream Tianpeng names",
    )


def _apply_export_seed_corrections(connection: sqlite3.Connection) -> None:
    """Correct the reviewed province values used as the next offline export seed."""
    for project_id, province in PROVINCE_CORRECTIONS.items():
        _execute_exact(
            connection,
            "UPDATE export_db.projects_flat SET province = ? WHERE project_id = ?",
            (province, project_id),
            1,
            f"correct export province {project_id}",
        )


def _write_province_cache(path: Path) -> None:
    """Persist the audited Malaysia province corrections in the durable cache."""
    with path.open("r", encoding="utf-8") as handle:
        mapping = json.load(handle)
    if not isinstance(mapping, dict):
        raise ValueError("province_cache.json 顶层必须为对象")
    missing = sorted(set(PROVINCE_CORRECTIONS) - set(mapping))
    if missing:
        raise RuntimeError(f"province cache 缺少预期项目: {missing}")
    mapping.update(PROVINCE_CORRECTIONS)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(mapping, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary_path.replace(path)
    logging.info("Province cache updated: %s", path)


def _preflight(
    main_db: Path,
    source_db: Path,
    export_db: Path,
    province_cache: Path,
) -> dict[str, object]:
    """Validate all expected targets and return the read-only baseline report."""
    with sqlite3.connect(main_db) as connection:
        _require_ids(connection, "canonical_projects", "id", EXPECTED_CANONICAL_IDS)
        _require_ids(connection, "chinese_entities", "id", EXPECTED_ENTITY_IDS)
        main_counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("canonical_projects", "project_events", "chinese_entities")
        }
    with sqlite3.connect(source_db) as connection:
        _require_ids(connection, "extracted_projects", "id", EXPECTED_SOURCE_IDS)
        source_count = connection.execute("SELECT COUNT(*) FROM extracted_projects").fetchone()[0]
    with sqlite3.connect(export_db) as connection:
        exported_ids = {
            row[0]
            for row in connection.execute(
                f"SELECT project_id FROM projects_flat WHERE project_id IN ({','.join('?' for _ in PROVINCE_CORRECTIONS)})",
                tuple(PROVINCE_CORRECTIONS),
            )
        }
        if exported_ids != set(PROVINCE_CORRECTIONS):
            raise RuntimeError(
                f"导出省份种子缺少预期项目: {sorted(set(PROVINCE_CORRECTIONS) - exported_ids)}"
            )
    with province_cache.open("r", encoding="utf-8") as handle:
        cache = json.load(handle)
    missing_cache_ids = sorted(set(PROVINCE_CORRECTIONS) - set(cache))
    if missing_cache_ids:
        raise RuntimeError(f"province cache 缺少预期项目: {missing_cache_ids}")
    return {
        "main_counts": main_counts,
        "source_count": source_count,
        "main_sha256": _sha256(main_db),
        "source_sha256": _sha256(source_db),
        "export_sha256": _sha256(export_db),
        "province_cache_sha256": _sha256(province_cache),
    }


def _verify_applied(main_db: Path, source_db: Path, province_cache: Path) -> dict[str, object]:
    """Verify corrected values, referential integrity, and exact canonical-ID removals."""
    with sqlite3.connect(main_db) as connection:
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        removed_ids = {"MYS-PV-0002", "MYS-PV-0020", "MYS-HB-0009"}
        remaining_removed = {
            row[0]
            for row in connection.execute(
                "SELECT id FROM canonical_projects WHERE id IN ('MYS-PV-0002','MYS-PV-0020','MYS-HB-0009')"
            )
        }
        if remaining_removed:
            raise RuntimeError(f"重复 canonical 未清除: {sorted(remaining_removed)}")
        if foreign_key_errors or integrity != "ok":
            raise RuntimeError(
                f"主库完整性失败: integrity={integrity}, fk_errors={foreign_key_errors[:5]}"
            )
        checks = {
            "active_pv0024": connection.execute(
                "SELECT COUNT(*) FROM canonical_projects WHERE id='MYS-PV-0024' AND excluded=0"
            ).fetchone()[0],
            "active_pv0018": connection.execute(
                "SELECT COUNT(*) FROM canonical_projects WHERE id='MYS-PV-0018' AND excluded=0"
            ).fetchone()[0],
            "citaglobal_entities": connection.execute(
                "SELECT COUNT(*) FROM chinese_entities WHERE implementing_entity_en='Citaglobal'"
            ).fetchone()[0],
            "wrong_batang_ai_parents": connection.execute(
                """
                SELECT COUNT(*) FROM chinese_entities ce JOIN project_events pe ON pe.id=ce.event_id
                WHERE pe.canonical_id='MYS-PV-0004'
                  AND ce.implementing_entity IN ('中国华能集团有限公司','中国电力建设集团有限公司')
                  AND ce.involvement_type='股权投资方'
                """
            ).fetchone()[0],
            "missing_batang_ai_spic_equity": connection.execute(
                """
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM chinese_entities ce JOIN project_events pe ON pe.id=ce.event_id
                    WHERE pe.canonical_id='MYS-PV-0004'
                      AND ce.implementing_entity='国家电力投资集团有限公司'
                      AND ce.subsidiary_name='中国电力国际发展有限公司'
                      AND ce.involvement_type='股权投资方'
                ) THEN 0 ELSE 1 END
                """
            ).fetchone()[0],
            "unsupported_financing": connection.execute(
                "SELECT COUNT(*) FROM project_events WHERE id='MYS-HY-0005-01' AND financing_model!='Unknown'"
            ).fetchone()[0],
            "wrong_murum_equity_role": connection.execute(
                """
                SELECT COUNT(*) FROM chinese_entities ce JOIN project_events pe ON pe.id=ce.event_id
                WHERE pe.canonical_id='MYS-HY-0009' AND ce.involvement_type='股权投资方'
                """
            ).fetchone()[0],
            "duplicate_target_events": connection.execute(
                """
                SELECT COUNT(*) FROM project_events
                WHERE canonical_id IN ('MYS-PV-0001','MYS-PV-0004','MYS-BM-0004')
                """
            ).fetchone()[0],
        }
        if any(checks[key] for key in checks if key != "duplicate_target_events"):
            raise RuntimeError(f"主库业务校验失败: {checks}")
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("canonical_projects", "project_events", "chinese_entities")
        }
    with sqlite3.connect(source_db) as connection:
        source_errors = connection.execute(
            """
            SELECT COUNT(*) FROM extracted_projects
            WHERE id=4706 AND excluded!=1
               OR id=4797 AND financing_model!='Unknown'
               OR id IN (3073,3074,3075,3076) AND financing_model!='信贷融资'
               OR id IN (4216,4228,4581) AND implementing_entity!='国家电力投资集团有限公司'
            """
        ).fetchone()[0]
        if source_errors:
            raise RuntimeError(f"上游修正校验失败: {source_errors} row(s)")
    with province_cache.open("r", encoding="utf-8") as handle:
        cache = json.load(handle)
    wrong_provinces = {
        key: cache.get(key)
        for key, expected in PROVINCE_CORRECTIONS.items()
        if cache.get(key) != expected
    }
    if wrong_provinces:
        raise RuntimeError(f"省份缓存校验失败: {wrong_provinces}")
    return {
        "counts": counts,
        "removed_duplicate_ids": sorted(removed_ids),
        "integrity_check": integrity,
        "foreign_key_errors": len(foreign_key_errors),
        "main_sha256": _sha256(main_db),
        "source_sha256": _sha256(source_db),
        "province_cache_sha256": _sha256(province_cache),
    }


def apply_corrections(
    main_db: Path,
    source_db: Path,
    export_db: Path,
    province_cache: Path,
    timestamp: str,
) -> dict[str, object]:
    """Back up, transactionally update, and verify all audited data stores."""
    backups = {
        "main_db": str(_backup_database(main_db, timestamp)),
        "source_db": str(_backup_database(source_db, timestamp)),
        "export_db": str(_backup_database(export_db, timestamp)),
        "province_cache": str(_backup_file(province_cache, timestamp)),
    }
    connection = sqlite3.connect(main_db)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("ATTACH DATABASE ? AS source_db", (str(source_db),))
        connection.execute("ATTACH DATABASE ? AS export_db", (str(export_db),))
        connection.execute("BEGIN IMMEDIATE")
        _apply_main_corrections(connection)
        _apply_source_corrections(connection)
        _apply_export_seed_corrections(connection)
        foreign_key_errors = connection.execute("PRAGMA main.foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError(f"提交前外键校验失败: {foreign_key_errors[:5]}")
        connection.commit()
    except (sqlite3.Error, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        connection.rollback()
        logging.error("Correction transaction failed: %s", exc, exc_info=True)
        raise
    finally:
        connection.close()
    _checkpoint_wal(source_db)
    _checkpoint_wal(export_db)
    _write_province_cache(province_cache)
    verification = _verify_applied(main_db, source_db, province_cache)
    verification["backups"] = backups
    return verification


def main() -> None:
    """Parse validated paths and run in read-only dry-run mode unless --apply is supplied."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入；缺省仅做只读前置校验")
    parser.add_argument("--main-db", type=Path, default=DEFAULT_MAIN_DB)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--export-db", type=Path, default=DEFAULT_EXPORT_DB)
    parser.add_argument("--province-cache", type=Path, default=DEFAULT_PROVINCE_CACHE)
    args = parser.parse_args()

    main_db = _validate_input_path(args.main_db, ".db")
    source_db = _validate_input_path(args.source_db, ".db")
    export_db = _validate_input_path(args.export_db, ".db")
    province_cache = _validate_input_path(args.province_cache, ".json")
    baseline = _preflight(main_db, source_db, export_db, province_cache)
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "baseline": baseline}, ensure_ascii=False, indent=2))
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _configure_logging(timestamp)
    logging.info("Baseline: %s", baseline)
    result = apply_corrections(main_db, source_db, export_db, province_cache, timestamp)
    print(
        json.dumps(
            {"mode": "apply", "baseline": baseline, "result": result, "log": str(log_path)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
