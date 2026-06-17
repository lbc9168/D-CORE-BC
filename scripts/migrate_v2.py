"""scripts/migrate_v2.py — v2 S2b 一次性迁移 bri_re_database.db。

内容：schema 升级（数值列拆 *_raw+REAL、加储能/来源/excluded）→ 数值回填
→ 储能正则提取 → 脏枚举清洗 → 非可再生能源项目排除 → 规范集断言。

安全：默认 dry-run（整个迁移在一个事务里执行后 ROLLBACK，仅打印将发生的改动）；
`--apply` 才提交，且提交前自动 shutil.copy 出带时间戳的 .bak 备份。

用法：
    uv run python scripts/migrate_v2.py --db <path>            # dry-run
    uv run python scripts/migrate_v2.py --db <path> --apply    # 落库
"""
import argparse
import logging
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── 枚举/解析单一可信源：上溯找仓库根 ──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums
import bri_parse

# ── 复用既有清洗映射（一次性脚本已归档至 scripts/oneoff/）──
sys.path.insert(0, str(Path(__file__).resolve().parent / "oneoff"))
from clean_historical_data import STAGE_MAP, INVOLVEMENT_MAP

from pydantic import BaseModel, ConfigDict, FilePath

logger = logging.getLogger("migrate_v2")

TABLES = ("canonical_projects", "project_events", "chinese_entities", "non_project_records")

# ── 枚举映射（CATEGORY 仅保留合法重分类；非 RE 走排除而非 →Unknown）──
STAGE_MAP_FULL = {**STAGE_MAP, "": "Unknown"}
INVOLVEMENT_MAP_FULL = INVOLVEMENT_MAP  # 已含 "" -> Unknown
CATEGORY_RECLASS = {
    "光热": "综合能源",      # 太阳能热发电
    "抽水蓄能": "水电",
    "水利水渠": "水电",
    "垃圾发电": "生物质",
    "新能源": "综合能源",
}
FINANCING_MAP = {
    "PPP": "其他",
    "BOT": "其他",
    "政府和社会资本合作（PPP）": "其他",
    "公私合作伙伴关系": "其他",
    "业主自筹": "其他",
    "": "Unknown",
}
NON_RE = {
    "石油", "石油化工", "石油开采", "石油开采与处理", "石油和天然气",
    "油气", "油气开发", "油气处理", "油气服务", "煤炭",
    "矿产开发", "矿产资源", "矿产资源开发", "矿产开采", "矿业开发",
    "工业园区", "工业安装", "基础设施建设", "农业灌溉", "房建",
}

# ── 储能正则：需储能关键词在数值前，避免把发电容量误当储能 ──
_PWR = re.compile(r"(?:储能|bess|储电)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*mw(?!h)", re.I)
_ENG = re.compile(r"(\d+(?:\.\d+)?)\s*mwh", re.I)


def extract_storage(details):
    """从 capacity_details 尽力提取 (储能功率MW, 储能容量MWh)，提不出留 None。"""
    if not details:
        return (None, None)
    p = _PWR.search(details)
    e = _ENG.search(details)
    return (float(p.group(1)) if p else None, float(e.group(1)) if e else None)


def _has_col(conn, table, col):
    """表中是否存在某列。"""
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def upgrade_schema(conn):
    """幂等升级三表数值列（RENAME→_raw + ADD REAL）、储能/来源、canonical excluded。"""
    pe, ce, npr, cp = TABLES[1], TABLES[2], TABLES[3], TABLES[0]
    if not _has_col(conn, pe, "capacity_mw_raw"):
        conn.execute(f"ALTER TABLE {pe} RENAME COLUMN capacity_mw TO capacity_mw_raw")
        conn.execute(f"ALTER TABLE {pe} ADD COLUMN capacity_mw REAL")
    if not _has_col(conn, pe, "total_investment_usd_raw"):
        conn.execute(f"ALTER TABLE {pe} RENAME COLUMN total_investment_usd TO total_investment_usd_raw")
        conn.execute(f"ALTER TABLE {pe} ADD COLUMN total_investment_usd REAL")
    for c, t in (("storage_power_mw", "REAL"), ("storage_energy_mwh", "REAL"), ("capacity_source", "TEXT")):
        if not _has_col(conn, pe, c):
            conn.execute(f"ALTER TABLE {pe} ADD COLUMN {c} {t}")
    if not _has_col(conn, ce, "subcontract_amount_raw"):
        conn.execute(f"ALTER TABLE {ce} RENAME COLUMN subcontract_amount TO subcontract_amount_raw")
        conn.execute(f"ALTER TABLE {ce} ADD COLUMN subcontract_amount REAL")
    if not _has_col(conn, npr, "capacity_mw_raw"):
        conn.execute(f"ALTER TABLE {npr} RENAME COLUMN capacity_mw TO capacity_mw_raw")
        conn.execute(f"ALTER TABLE {npr} ADD COLUMN capacity_mw REAL")
    if not _has_col(conn, npr, "subcontract_amount_raw"):
        conn.execute(f"ALTER TABLE {npr} RENAME COLUMN subcontract_amount TO subcontract_amount_raw")
        conn.execute(f"ALTER TABLE {npr} ADD COLUMN subcontract_amount REAL")
    for c, decl in (("excluded", "INTEGER DEFAULT 0"), ("excluded_reason", "TEXT")):
        if not _has_col(conn, cp, c):
            conn.execute(f"ALTER TABLE {cp} ADD COLUMN {c} {decl}")
    logger.info("schema 升级完成")


def backfill_numeric(conn):
    """各 *_raw 经 bri_parse 回填 REAL；project_events 容量来源标 legacy。返回统计。"""
    # project_events: capacity_mw + total_investment_usd + capacity_source
    rows = conn.execute(
        "SELECT rowid, capacity_mw_raw, total_investment_usd_raw FROM project_events"
    ).fetchall()
    pe_upd = []
    cap_ok = cap_fail = inv_ok = inv_fail = 0
    for rid, craw, iraw in rows:
        cv = bri_parse.parse_number(craw)
        iv = bri_parse.parse_number(iraw)
        pe_upd.append((cv, "legacy" if cv is not None else None, iv, rid))
        if craw not in (None, ""):
            cap_ok += cv is not None
            cap_fail += cv is None
        if iraw not in (None, ""):
            inv_ok += iv is not None
            inv_fail += iv is None
    conn.executemany(
        "UPDATE project_events SET capacity_mw=?, capacity_source=?, total_investment_usd=? WHERE rowid=?",
        pe_upd,
    )
    # chinese_entities: subcontract_amount
    rows = conn.execute("SELECT rowid, subcontract_amount_raw FROM chinese_entities").fetchall()
    conn.executemany(
        "UPDATE chinese_entities SET subcontract_amount=? WHERE rowid=?",
        [(bri_parse.parse_number(r[1]), r[0]) for r in rows],
    )
    # non_project_records: capacity_mw + subcontract_amount
    rows = conn.execute(
        "SELECT rowid, capacity_mw_raw, subcontract_amount_raw FROM non_project_records"
    ).fetchall()
    conn.executemany(
        "UPDATE non_project_records SET capacity_mw=?, subcontract_amount=? WHERE rowid=?",
        [(bri_parse.parse_number(r[1]), bri_parse.parse_number(r[2]), r[0]) for r in rows],
    )
    stats = {"cap_ok": cap_ok, "cap_fail": cap_fail, "inv_ok": inv_ok, "inv_fail": inv_fail}
    logger.info("数值回填: 容量 %d 成功/%d 失败；投资额 %d 成功/%d 失败",
                cap_ok, cap_fail, inv_ok, inv_fail)
    return stats


def backfill_storage(conn):
    """project_events.capacity_details 正则提取储能功率/容量。返回命中计数。"""
    rows = conn.execute("SELECT rowid, capacity_details FROM project_events").fetchall()
    upd = []
    n_pwr = n_eng = 0
    for rid, d in rows:
        p, e = extract_storage(d)
        if p is not None:
            n_pwr += 1
        if e is not None:
            n_eng += 1
        if p is not None or e is not None:
            upd.append((p, e, rid))
    conn.executemany(
        "UPDATE project_events SET storage_power_mw=?, storage_energy_mwh=? WHERE rowid=?", upd
    )
    logger.info("储能提取: 功率 %d 条、容量 %d 条（共 %d 行命中）", n_pwr, n_eng, len(upd))
    return {"storage_power": n_pwr, "storage_energy": n_eng}


def clean_category(conn):
    """canonical_projects.category：合法重分类 + 垃圾→Unknown；非 RE 保留原值（后续排除）。"""
    valid = set(enums.valid_categories())
    distinct = [r[0] for r in conn.execute("SELECT DISTINCT category FROM canonical_projects")]
    n = 0
    for v in distinct:
        if v in valid or v in NON_RE:
            continue
        new = CATEGORY_RECLASS.get(v, "Unknown")
        cnt = conn.execute("SELECT COUNT(*) FROM canonical_projects WHERE category IS ?", (v,)).fetchone()[0]
        conn.execute("UPDATE canonical_projects SET category=? WHERE category IS ?", (new, v))
        logger.info("  category: %r -> %r (%d)", v, new, cnt)
        n += cnt
    return n


def clean_enum(conn, table, col, mapping, valid):
    """通用枚举清洗：仅对映射表覆盖的非法值做 UPDATE，未覆盖留待断言报告。"""
    valid = set(valid)
    distinct = [r[0] for r in conn.execute(f"SELECT DISTINCT {col} FROM {table}")]
    n = 0
    for v in distinct:
        if v in valid:
            continue
        if v in mapping:
            new = mapping[v]
            cnt = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IS ?", (v,)).fetchone()[0]
            conn.execute(f"UPDATE {table} SET {col}=? WHERE {col} IS ?", (new, v))
            logger.info("  %s.%s: %r -> %r (%d)", table, col, v, new, cnt)
            n += cnt
    return n


def mark_non_re(conn):
    """非可再生能源等类别标 excluded=1，保留原 category 可追溯。返回被排除项目清单。"""
    nonre = sorted(NON_RE)
    ph = ",".join("?" * len(nonre))
    rows = conn.execute(
        f"SELECT id, project_name, category FROM canonical_projects WHERE category IN ({ph})", nonre
    ).fetchall()
    conn.execute(
        f"UPDATE canonical_projects SET excluded=1, excluded_reason='non_renewable' "
        f"WHERE category IN ({ph})",
        nonre,
    )
    logger.info("非 RE 排除: %d 个项目", len(rows))
    return rows


def assert_enums(conn):
    """断言四个枚举列 distinct ⊆ 规范集（category 仅查 excluded=0）。"""
    checks = [
        ("canonical_projects", "category",
         "SELECT DISTINCT category FROM canonical_projects WHERE excluded=0", enums.valid_categories()),
        ("project_events", "project_stage",
         "SELECT DISTINCT project_stage FROM project_events", enums.valid_stages()),
        ("chinese_entities", "involvement_type",
         "SELECT DISTINCT involvement_type FROM chinese_entities", enums.valid_involvement()),
        ("project_events", "financing_model",
         "SELECT DISTINCT financing_model FROM project_events", enums.valid_financing()),
    ]
    ok = True
    for table, col, q, valid in checks:
        vals = [r[0] for r in conn.execute(q)]
        bad = [v for v in vals if v not in set(valid)]
        if bad:
            ok = False
            logger.error("断言失败 %s.%s 未覆盖值: %r", table, col, bad)
        else:
            logger.info("断言通过 %s.%s ⊆ 规范集 (%d distinct)", table, col, len(vals))
    return ok


def _sample(conn, label, sql, n=20):
    """打印抽样供人工核查。"""
    logger.info("--- 抽样 %s（前 %d）---", label, n)
    for row in conn.execute(sql).fetchmany(n):
        logger.info("    %s", row)


def migrate(db_path: str, apply: bool) -> bool:
    """执行迁移；dry-run 在事务内 ROLLBACK，apply 先备份再 COMMIT。"""
    if apply:
        bak = f"{db_path}.bak.{datetime.now():%Y%m%d_%H%M%S}"
        shutil.copy(db_path, bak)
        logger.info("已备份主库 → %s", bak)

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # 手动管理事务，保证 dry-run 可整体回滚
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    logger.info("迁移前行数: %s", before)

    conn.execute("BEGIN")
    try:
        upgrade_schema(conn)
        backfill_numeric(conn)
        backfill_storage(conn)
        logger.info("─ 枚举清洗 ─")
        clean_category(conn)
        clean_enum(conn, "project_events", "project_stage", STAGE_MAP_FULL, enums.valid_stages())
        clean_enum(conn, "chinese_entities", "involvement_type", INVOLVEMENT_MAP_FULL, enums.valid_involvement())
        clean_enum(conn, "project_events", "financing_model", FINANCING_MAP, enums.valid_financing())
        excluded = mark_non_re(conn)
        logger.info("将排除的非 RE 项目清单:")
        for pid, name, cat in excluded:
            logger.info("    %s  [%s]  %s", pid, cat, name)

        ok = assert_enums(conn)

        after = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
        if after != before:
            ok = False
            logger.error("行数变化（应不变）: %s -> %s", before, after)
        else:
            logger.info("行数不变，无记录丢失 ✓")

        _sample(conn,
                "capacity 原文→解析",
                "SELECT capacity_mw_raw, capacity_mw FROM project_events "
                "WHERE capacity_mw_raw IS NOT NULL AND capacity_mw_raw != '' LIMIT 20")
        _sample(conn,
                "storage 提取",
                "SELECT capacity_details, storage_power_mw, storage_energy_mwh FROM project_events "
                "WHERE storage_power_mw IS NOT NULL OR storage_energy_mwh IS NOT NULL LIMIT 20")

        if not ok:
            conn.execute("ROLLBACK")
            logger.error("校验未通过，已回滚（未改动数据库）")
            return False

        if apply:
            conn.execute("COMMIT")
            logger.info("✅ 已提交到主库")
        else:
            conn.execute("ROLLBACK")
            logger.info("DRY-RUN 完成，已回滚（未改动数据库）。加 --apply 落库。")
        return True
    except Exception:
        conn.execute("ROLLBACK")
        logger.exception("迁移异常，已回滚")
        raise
    finally:
        conn.close()


class MigrateArgs(BaseModel):
    """CLI 参数校验：db 路径必须存在。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    db_path: FilePath
    apply: bool = False


def main():
    parser = argparse.ArgumentParser(description="v2 S2b 迁移 bri_re_database.db")
    parser.add_argument("--db", required=True, help="目标数据库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    args = parser.parse_args()

    cfg = MigrateArgs(db_path=args.db, apply=args.apply)

    log_path = Path(cfg.db_path).resolve().parent / "migrate_v2.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    logger.info("=== migrate_v2 %s | db=%s ===",
                "APPLY" if cfg.apply else "DRY-RUN", cfg.db_path)
    ok = migrate(str(cfg.db_path), cfg.apply)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
