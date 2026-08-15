"""Define and publish the immutable forty-article Stage 4E expansion cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .pilot_manifest import PILOT_BY_ID
from .single_article import EXTRACTION_DB, MAIN_DB, RAW_DB

COHORT_VERSION = "stage-4e-40-v1"
SCHEMA_VERSION = "gold-standard-cohort-v1"
STRATUM_QUOTAS = {
    "core_balanced": 10,
    "s01b_complex": 8,
    "s02a_family": 8,
    "s02b_s03_normalization": 8,
    "boundary_gate": 6,
}
SPLIT_QUOTAS = {"development": 24, "challenge": 16}


def _item(article_id: int, split: str, stratum: str, risk: int, difficulty: str,
          kind: str, tags: tuple[str, ...], family_id: str | None = None) -> dict[str, Any]:
    """Build one concise static selection record without claiming historical signals are Gold."""
    return {
        "article_id": article_id,
        "split": split,
        "primary_stratum": stratum,
        "risk": risk,
        "difficulty": difficulty,
        "kind": kind,
        "layer_tags": list(tags),
        "family_id": family_id,
        "cohort": COHORT_VERSION,
    }


STAGE4E_ARTICLES: tuple[dict[str, Any], ...] = (
    # Balanced language, category, geography, and stage coverage.
    _item(23627, "development", "core_balanced", 2, "中", "英文光伏合作", ("s01b", "end_to_end_candidate")),
    _item(25106, "development", "core_balanced", 2, "中", "英文光伏供货", ("s01b", "s02b", "end_to_end_candidate")),
    _item(16899, "development", "core_balanced", 3, "高", "英文光储协议", ("s01b", "s02b", "end_to_end_candidate")),
    _item(20872, "development", "core_balanced", 2, "中", "英文火电融资", ("s01b", "end_to_end_candidate")),
    _item(192, "development", "core_balanced", 2, "中", "漂浮式光伏运营", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(260, "development", "core_balanced", 2, "中", "医院光储项目", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(281, "development", "core_balanced", 2, "中", "多条中标短讯", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(166, "challenge", "core_balanced", 3, "高", "燃机与海淡复合项目", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(353, "challenge", "core_balanced", 3, "高", "光伏储能容量语义", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(87, "challenge", "core_balanced", 3, "高", "新能源制造基地", ("s01b", "article_gate", "end_to_end_candidate")),

    # High-complexity article-internal project boundaries.
    _item(14313, "development", "s01b_complex", 4, "很高", "年度多项目回顾", ("s01b", "multi_project", "end_to_end_candidate")),
    _item(9081, "development", "s01b_complex", 4, "很高", "多国多项目中标清单", ("s01b", "multi_project", "end_to_end_candidate")),
    _item(3674, "development", "s01b_complex", 4, "很高", "多国储能订单组合", ("s01b", "multi_project", "end_to_end_candidate")),
    _item(12153, "development", "s01b_complex", 4, "很高", "制造业与历史项目混合", ("s01b", "multi_project", "end_to_end_candidate")),
    _item(4604, "challenge", "s01b_complex", 4, "很高", "十三项目签约清单", ("s01b", "multi_project", "end_to_end_candidate")),
    _item(13911, "challenge", "s01b_complex", 4, "很高", "跨类别并购汇总", ("s01b", "multi_project", "article_gate")),
    _item(13744, "challenge", "s01b_complex", 4, "很高", "市场综述与项目混合", ("s01b", "multi_project", "article_gate")),
    _item(16105, "challenge", "s01b_complex", 4, "很高", "光储与非目标项目清单", ("s01b", "multi_project", "end_to_end_candidate")),

    # Cross-article canonical families, kept wholly inside one split.
    _item(232, "development", "s02a_family", 3, "高", "吉扎克光伏家族 A", ("s01b", "s02a", "s02b", "s03", "end_to_end_candidate"), "UZB-PV-GIZHAK-4E"),
    _item(26667, "development", "s02a_family", 3, "高", "吉扎克光伏家族 B", ("s01b", "s02a", "s02b", "s03", "end_to_end_candidate"), "UZB-PV-GIZHAK-4E"),
    _item(16817, "development", "s02a_family", 3, "高", "门迪储能家族 A", ("s01b", "s02a", "end_to_end_candidate"), "GBR-ES-MENDI-4E"),
    _item(17408, "development", "s02a_family", 3, "高", "门迪储能家族 B", ("s01b", "s02a", "end_to_end_candidate"), "GBR-ES-MENDI-4E"),
    _item(4930, "challenge", "s02a_family", 4, "很高", "佳蒂格德水电家族 A", ("s01b", "s02a", "end_to_end_candidate"), "IDN-HY-JATIGEDE-4E"),
    _item(4952, "challenge", "s02a_family", 4, "很高", "佳蒂格德水电家族 B", ("s01b", "s02a", "end_to_end_candidate"), "IDN-HY-JATIGEDE-4E"),
    _item(526, "challenge", "s02a_family", 4, "很高", "波黑近重复冲突 A", ("s01b", "s02a", "article_gate"), "BIH-BOUNDARY-U-4E"),
    _item(532, "challenge", "s02a_family", 4, "很高", "波黑近重复冲突 B", ("s01b", "s02a", "article_gate"), "BIH-BOUNDARY-U-4E"),

    # Location normalization and admin1 support boundaries.
    _item(32, "development", "s02b_s03_normalization", 3, "高", "矿区光储地点层级", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(75, "development", "s02b_s03_normalization", 3, "高", "城市别名垃圾发电", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(137, "development", "s02b_s03_normalization", 3, "高", "候选区位垃圾发电", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(155, "development", "s02b_s03_normalization", 3, "高", "风电配储行政区", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(432, "development", "s02b_s03_normalization", 3, "高", "拉丁字母行政区", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(465, "development", "s02b_s03_normalization", 3, "高", "省市层级水电", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(483, "challenge", "s02b_s03_normalization", 3, "高", "万象省市歧义", ("s01b", "s02b", "s03", "end_to_end_candidate")),
    _item(635, "challenge", "s02b_s03_normalization", 3, "高", "塔什干州市歧义", ("s01b", "s02b", "s03", "end_to_end_candidate")),

    # Previously unextracted scope and participation boundary candidates.
    _item(1380, "development", "boundary_gate", 3, "高", "中企落榜无参与", ("s01b", "article_gate")),
    _item(3215, "development", "boundary_gate", 3, "高", "外国核电中标", ("s01b", "article_gate")),
    _item(12865, "development", "boundary_gate", 3, "高", "多项目能源管理服务", ("s01b", "article_gate")),
    _item(5093, "challenge", "boundary_gate", 4, "很高", "电池材料制造边界", ("s01b", "article_gate")),
    _item(2756, "challenge", "boundary_gate", 4, "很高", "变电站柴油机采购边界", ("s01b", "article_gate")),
    _item(27434, "challenge", "boundary_gate", 4, "很高", "逆变器供货项目边界", ("s01b", "article_gate")),
)

STAGE4E_BY_ID = {item["article_id"]: item for item in STAGE4E_ARTICLES}


def stage4e_article_ids() -> list[int]:
    """Return Stage 4E article IDs in the fixed human-review order."""
    return [item["article_id"] for item in STAGE4E_ARTICLES]


def validate_selection_contract() -> dict[str, Any]:
    """Fail closed unless static Stage 4E IDs, splits, strata, and families match the plan."""
    ids = stage4e_article_ids()
    errors: list[str] = []
    if len(ids) != 40 or len(set(ids)) != 40:
        errors.append("Stage 4E 必须包含 40 个互异 article ID")
    overlap = sorted(set(ids) & set(PILOT_BY_ID))
    if overlap:
        errors.append(f"Stage 4E 与 Pilot 重叠：{overlap}")
    split_counts = Counter(item["split"] for item in STAGE4E_ARTICLES)
    stratum_counts = Counter(item["primary_stratum"] for item in STAGE4E_ARTICLES)
    if dict(split_counts) != SPLIT_QUOTAS:
        errors.append(f"split 配额不符：{dict(split_counts)}")
    if dict(stratum_counts) != STRATUM_QUOTAS:
        errors.append(f"stratum 配额不符：{dict(stratum_counts)}")
    families: dict[str, list[dict[str, Any]]] = {}
    for item in STAGE4E_ARTICLES:
        if item["family_id"]:
            families.setdefault(item["family_id"], []).append(item)
    for family_id, members in families.items():
        if len(members) != 2 or len({member["split"] for member in members}) != 1:
            errors.append(f"S02A family 必须是同 split 的两篇文章：{family_id}")
    if len(families) != 4:
        errors.append(f"S02A family 数量应为 4，实际为 {len(families)}")
    if errors:
        raise ValueError("；".join(errors))
    return {
        "article_count": len(ids),
        "split_counts": dict(split_counts),
        "stratum_counts": dict(stratum_counts),
        "family_count": len(families),
        "blind_count": 0,
    }


def _sha256(path: Path) -> str:
    """Hash one file in bounded chunks for source and artifact provenance."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    """Open one source SQLite database in URI read-only and query-only mode."""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _article_records() -> list[dict[str, Any]]:
    """Freeze source metadata and historical selection signals for all forty articles."""
    validate_selection_contract()
    ids = stage4e_article_ids()
    marks = ",".join("?" for _ in ids)
    with _connect_readonly(RAW_DB) as connection:
        rows = {
            row["id"]: dict(row)
            for row in connection.execute(
                f"SELECT id,url,title,publish_date,content FROM raw_news WHERE id IN ({marks})", ids
            )
        }
    missing = sorted(set(ids) - set(rows))
    if missing:
        raise ValueError(f"raw_news 缺少 Stage 4E 文章：{missing}")

    records: list[dict[str, Any]] = []
    with _connect_readonly(EXTRACTION_DB) as extraction, _connect_readonly(MAIN_DB) as main:
        for selection in STAGE4E_ARTICLES:
            article = rows[selection["article_id"]]
            content = str(article.get("content") or "")
            if not content.strip():
                raise ValueError(f"文章 {selection['article_id']} 正文为空")
            extraction_rows = extraction.execute(
                "SELECT project_name,category FROM extracted_projects WHERE source_link=? AND COALESCE(excluded,0)=0",
                (article["url"],),
            ).fetchall()
            main_rows = main.execute(
                "SELECT canonical_id FROM project_events WHERE source_links LIKE ? ORDER BY canonical_id",
                (f"%{article['url']}%",),
            ).fetchall()
            project_names = sorted({row["project_name"] for row in extraction_rows if row["project_name"]})
            categories = sorted({row["category"] for row in extraction_rows if row["category"]})
            canonical_ids = sorted({row["canonical_id"] for row in main_rows})
            records.append({
                **selection,
                "title": article["title"],
                "url": article["url"],
                "publish_date": article["publish_date"],
                "article_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "historical_status_hint": "valid" if project_names or canonical_ids else "unreviewed",
                "historical_project_count": len(project_names),
                "historical_categories": categories,
                "historical_canonical_ids": canonical_ids,
                "selection_signal_is_gold": False,
            })
    return records


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write deterministic UTF-8 JSON with a single trailing newline."""
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8")


def verify_stage4e_cohort(cohort_dir: Path, verify_sources: bool = True) -> dict[str, Any]:
    """Re-read a published cohort and verify counts, hashes, and optional source snapshot provenance."""
    manifest = json.loads((cohort_dir / "manifest.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (cohort_dir / "articles.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if manifest.get("cohort_version") != COHORT_VERSION or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Stage 4E cohort 版本或 schema 不匹配")
    if _sha256(cohort_dir / "articles.jsonl") != manifest["artifacts"]["articles.jsonl"]:
        raise ValueError("articles.jsonl hash 不匹配")
    if [record["article_id"] for record in records] != stage4e_article_ids():
        raise ValueError("articles.jsonl ID 或顺序不匹配固定清单")
    contract = validate_selection_contract()
    if manifest.get("counts") != contract:
        raise ValueError("manifest 配额统计不匹配")
    if verify_sources:
        for name, path in {"raw": RAW_DB, "extraction": EXTRACTION_DB, "main": MAIN_DB}.items():
            if _sha256(path) != manifest["source_databases"][name]["sha256"]:
                raise ValueError(f"{name} 数据库 hash 已变化")
    return manifest


def build_stage4e_cohort(output_root: Path) -> Path:
    """Atomically publish the real forty-article cohort without overwriting an existing version."""
    target = output_root / COHORT_VERSION
    if target.exists():
        raise FileExistsError(f"cohort 已存在，拒绝覆盖：{target}")
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{COHORT_VERSION}-", dir=output_root))
    try:
        records = _article_records()
        articles_path = temporary / "articles.jsonl"
        articles_path.write_text(
            "".join(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    for record in records),
            encoding="utf-8",
        )
        contract = validate_selection_contract()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "cohort_version": COHORT_VERSION,
            "immutable": True,
            "counts": contract,
            "pilot_exclusion_ids": sorted(PILOT_BY_ID),
            "selection_policy": {
                "kind": "deterministic_stratified_purposive_expansion",
                "historical_signals_are_gold": False,
                "pilot_is_development_only": True,
                "blind_holdout_included": False,
            },
            "source_databases": {
                name: {"path": str(path), "sha256": _sha256(path)}
                for name, path in {"raw": RAW_DB, "extraction": EXTRACTION_DB, "main": MAIN_DB}.items()
            },
            "implementation_sha256": _sha256(Path(__file__)),
            "artifacts": {"articles.jsonl": _sha256(articles_path)},
        }
        _write_json(temporary / "manifest.json", manifest)
        verify_stage4e_cohort(temporary)
        temporary.rename(target)
        return target
    except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main() -> None:
    """Provide build and verify commands for the immutable Stage 4E cohort."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--output-root", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("cohort_dir", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        print(build_stage4e_cohort(args.output_root))
    else:
        print(json.dumps(verify_stage4e_cohort(args.cohort_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
