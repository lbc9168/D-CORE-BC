"""全库重解析回填 CLI：用 v2 确定性解析器重解析 chinese_entities 存量行。

背景：主库 chinese_entities 存量行是旧归一逻辑产物（含 1697 能建国际、121 电建海投讹变名等
错误归属）。本工具用 normalize_entity（entity_normalize.py，读 company_hierarchy_v2.json）
统一重解析并回填 implementing_entity / subsidiary_name / is_central_soe；
en 列一致性规则：CN 变 → en 置 NULL（下次 en_backfill 流水线回填），CN 不变 → en 不动。

用法：uv run entity_reparse_maindb [--db PATH] [--apply] [--out CSV]
  默认 dry-run；--apply 先 .bak 再事务批量 UPDATE。

设计参照：nonenergy_sweep_maindb.py（同目录）。
WAL 备份参照：s02b_entity_normalizer.db_backup.backup_db（跨包导入不便，本地最小化重实现）。
"""
import argparse
import csv
import hashlib
import logging
import os
import sqlite3
from collections import Counter
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# 主工作区主库默认路径：worktree 根上溯到项目根（main project root = parents[7]）
# 文件路径：.../dcore_v2.2/s01b_project_extractor/src/s01b_project_extractor/tools/
# parents[4]=worktree根 → parents[7]=E:/Projects/BRI_RE_Database_v2/
# CLI 调用时建议显式 --db，默认路径仅作文档用途
_DEFAULT_DB = str(
    Path(__file__).resolve().parents[7]
    / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
)


# ── 模块级可注入桩（测试用 monkeypatch）────────────────────────────────────────

def _normalize_entity(name: str) -> tuple[str, str]:
    """包装 entity_normalize.normalize_entity，供测试 monkeypatch。"""
    from s01b_project_extractor.stages.entity_normalize import normalize_entity
    return normalize_entity(name)


def _resolve_soe(entity_cn: str) -> int:
    """包装 project_extractor_tool._resolve_is_central_soe，供测试 monkeypatch。"""
    from s01b_project_extractor.tools.project_extractor_tool import _resolve_is_central_soe
    return _resolve_is_central_soe(entity_cn)


# ── CLI 参数 Pydantic 校验（项目规则4：路径存在性在入口验证）─────────────────

class ReparseArgs(BaseModel):
    """CLI 参数校验模型：路径必须存在，apply 默认 False。"""
    db: str
    apply: bool = False
    out: str | None = None

    @field_validator("db")
    @classmethod
    def db_must_exist(cls, v: str) -> str:
        """数据库文件必须存在。"""
        if not Path(v).exists():
            raise ValueError(f"数据库不存在: {v}")
        return v


# ── 纯函数：源名恢复 ──────────────────────────────────────────────────────────

def _source_name(row: dict) -> str:
    """按不变量恢复实体原文名：subsidiary 非空 → 用 subsidiary；否则用 implementing。"""
    sub = (row.get("subsidiary_name") or "").strip()
    impl = (row.get("implementing_entity") or "").strip()
    return sub if sub else impl


# ── 纯函数：构建变更计划（不写库）────────────────────────────────────────────

def build_update_plan(db_path: str) -> tuple[list[dict], dict]:
    """扫描 chinese_entities，返回 (变更计划列表, 统计 dict)。

    变更计划每条含：id, event_id, old_impl, new_impl, old_sub, new_sub,
                     old_soe, new_soe, impl_changed, sub_changed,
                     impl_en_null, sub_en_null。
    统计 dict：total, changed, skipped_empty, impl_changed, sub_changed, soe_changed, en_nulled。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT id, event_id, implementing_entity, implementing_entity_en,
                   subsidiary_name, subsidiary_name_en, is_central_soe
            FROM chinese_entities
        """).fetchall()
    finally:
        conn.close()

    plan: list[dict] = []
    stats: dict[str, int] = {
        "total": len(rows),
        "changed": 0,
        "skipped_empty": 0,
        "impl_changed": 0,
        "sub_changed": 0,
        "soe_changed": 0,
        "en_nulled": 0,
    }

    for r in rows:
        row = dict(r)
        src = _source_name(row)

        if not src:
            # implementing + subsidiary 均空，跳过
            stats["skipped_empty"] += 1
            logger.debug("跳过两者皆空行 id=%s", row["id"])
            continue

        new_impl, new_sub = _normalize_entity(src)
        new_soe = _resolve_soe(new_impl)

        old_impl = (row["implementing_entity"] or "").strip()
        old_sub = (row["subsidiary_name"] or "").strip()
        old_soe = row["is_central_soe"]

        impl_changed = new_impl != old_impl
        sub_changed = new_sub != old_sub
        soe_changed = new_soe != old_soe

        if not (impl_changed or sub_changed or soe_changed):
            continue  # 无变化，不入计划

        # EN 置 NULL 规则：CN 变 → en 置 NULL（下次 en_backfill 回填）
        impl_en_null = impl_changed
        sub_en_null = sub_changed

        plan.append({
            "id": row["id"],
            "event_id": row["event_id"],
            "old_impl": old_impl,
            "new_impl": new_impl,
            "old_sub": old_sub,
            "new_sub": new_sub,
            "old_soe": old_soe,
            "new_soe": new_soe,
            "impl_changed": impl_changed,
            "sub_changed": sub_changed,
            "soe_changed": soe_changed,
            "impl_en_null": impl_en_null,
            "sub_en_null": sub_en_null,
        })

        stats["changed"] += 1
        if impl_changed:
            stats["impl_changed"] += 1
        if sub_changed:
            stats["sub_changed"] += 1
        if soe_changed:
            stats["soe_changed"] += 1
        if impl_en_null or sub_en_null:
            stats["en_nulled"] += 1

    logger.info(
        "build_update_plan 完成：total=%d changed=%d impl_changed=%d sub_changed=%d soe_changed=%d en_nulled=%d skipped_empty=%d",
        stats["total"], stats["changed"],
        stats["impl_changed"], stats["sub_changed"],
        stats["soe_changed"], stats["en_nulled"],
        stats["skipped_empty"],
    )
    return plan, stats


# ── WAL 安全备份（最小化本地实现，语义参照 s02b_entity_normalizer.db_backup）──

def _sha1_file(path: str) -> str:
    """分块计算文件内容 SHA1（避免整读大文件）。"""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _backup_db(db_path: str) -> str:
    """WAL 安全快照到 <db>.bak.<时间戳>；内容与最新备份一致时去重复用。返回备份路径。"""
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(bak)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # 去重：与最新备份内容一致则复用
    prior = sorted(p for p in glob(db_path + ".bak.*") if p != bak)
    if prior:
        latest = prior[-1]
        if os.path.getsize(latest) == os.path.getsize(bak) and _sha1_file(latest) == _sha1_file(bak):
            os.remove(bak)
            logger.info("库内容与最新备份一致，复用 %s", latest)
            return latest

    logger.info("库已备份 %s", bak)
    return bak


# ── 主入口（dry-run + apply）─────────────────────────────────────────────────

def reparse_maindb(db_path: str, apply: bool = False, out_csv: str | None = None) -> dict:
    """重解析 chinese_entities 存量行；apply=True 时先备份再事务批量 UPDATE。

    返回 dict 包含：changed, total, skipped_empty, bak_path(apply 时), plan(行列表)。
    """
    plan, stats = build_update_plan(db_path)

    # 输出 CSV（dry-run 也写，供报告使用）
    if out_csv and plan:
        fields = ["id", "event_id", "old_impl", "new_impl", "old_sub", "new_sub",
                  "old_soe", "new_soe", "impl_changed", "sub_changed", "soe_changed",
                  "impl_en_null", "sub_en_null"]
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(plan)
        logger.info("变更计划 CSV 已写 %s (%d 行)", out_csv, len(plan))

    result: dict[str, Any] = {**stats, "plan": plan, "bak_path": None}

    if not apply:
        logger.info("dry-run 完成，未写库。changed=%d", stats.get("changed", 0))
        return result

    if not plan:
        logger.info("apply 无需执行（0 变更）")
        return result

    # 备份
    bak = _backup_db(db_path)
    result["bak_path"] = bak

    # 事务批量 UPDATE（项目规则3：批量写库须用事务 + executemany）
    conn = sqlite3.connect(db_path)
    try:
        params = []
        for row in plan:
            new_impl_en = None if row["impl_en_null"] else "__KEEP__"
            new_sub_en = None if row["sub_en_null"] else "__KEEP__"
            params.append((
                row["new_impl"],
                row["new_sub"],
                row["new_soe"],
                row["impl_en_null"],
                row["sub_en_null"],
                row["id"],
            ))

        # 分两批：impl_en 置 NULL 和 不置 NULL 的行分别处理
        impl_null_params = [
            (p[0], p[1], p[2], p[5])
            for p in params if p[3]  # impl_en_null
        ]
        impl_keep_sub_null_params = [
            (p[0], p[1], p[2], p[5])
            for p in params if not p[3] and p[4]  # not impl_en_null but sub_en_null
        ]
        impl_keep_sub_keep_params = [
            (p[0], p[1], p[2], p[5])
            for p in params if not p[3] and not p[4]  # neither null
        ]

        if impl_null_params:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?,
                       implementing_entity_en=NULL, subsidiary_name_en=NULL
                   WHERE id=?""",
                impl_null_params,
            )
        if impl_keep_sub_null_params:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?,
                       subsidiary_name_en=NULL
                   WHERE id=?""",
                impl_keep_sub_null_params,
            )
        if impl_keep_sub_keep_params:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?
                   WHERE id=?""",
                impl_keep_sub_keep_params,
            )

        conn.commit()
        logger.info("apply 完成：%d 行已更新（备份 %s）", len(plan), bak)
    except sqlite3.Error as e:
        logger.error("apply 失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()

    return result


# ── 不变量健诊（统计，不阻塞）────────────────────────────────────────────────

def _health_check(db_path: str) -> dict:
    """统计不变量健诊：subsidiary 非空行数、两者皆空行数。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(CASE WHEN subsidiary_name IS NOT NULL AND subsidiary_name != '' THEN 1 END) as with_sub,
                COUNT(CASE WHEN (implementing_entity IS NULL OR implementing_entity = '')
                           AND (subsidiary_name IS NULL OR subsidiary_name = '') THEN 1 END) as both_empty
            FROM chinese_entities
        """).fetchone()
        return dict(r)
    finally:
        conn.close()


# ── 两个 cohort 专项抽查 SQL ──────────────────────────────────────────────────

def _cohort_check(db_path: str) -> dict:
    """验证两个专项 cohort 是否正确归位（各取前 3 行）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Cohort 1：能建国际 → 应归 implementing=中国能源建设集团有限公司
        c1 = conn.execute("""
            SELECT id, implementing_entity, subsidiary_name, is_central_soe
            FROM chinese_entities
            WHERE subsidiary_name = '中国能建国际集团'
               OR implementing_entity LIKE '%能建国际%'
            LIMIT 3
        """).fetchall()

        # Cohort 2：能源海外投资 → 应归 implementing=中国电力建设集团有限公司
        c2 = conn.execute("""
            SELECT id, implementing_entity, subsidiary_name, is_central_soe
            FROM chinese_entities
            WHERE subsidiary_name = '中国能源海外投资有限公司'
               OR implementing_entity LIKE '%能源海外投资%'
            LIMIT 3
        """).fetchall()

        return {
            "cohort1": [dict(r) for r in c1],
            "cohort2": [dict(r) for r in c2],
        }
    finally:
        conn.close()


# ── 未命中 Top 清单（供季度补录）─────────────────────────────────────────────

def _collect_misses(plan: list[dict]) -> list[dict]:
    """从变更计划中收集"透传"行（new_impl == old source_name → 未命中）Top50。

    判据：new_sub 为空 且 new_impl == source_name（透传原文）。
    由于 plan 里不直接存 source_name，透传特征是 new_impl 与 old_impl 相同（未变）
    且未被归入任何子公司（new_sub 空）。实际上透传行因 old_impl==new_impl 不会进入 plan，
    所以这里统计 plan 以外的透传——直接扫 DB 收集 normalize 未命中行。
    注：此函数在 main() 中调用，扫描所有行（不只 plan 里的）。
    """
    # 透传行：normalize 后 new_impl 就是 source_name（保持原文），new_sub 为空
    # 这些行因 impl/sub/soe 可能都没变而不在 plan 中，需单独收集
    miss_counter: Counter = Counter()
    return []  # 实现在 _collect_all_misses_from_db 中


def _is_in_hierarchy(name: str) -> bool:
    """检查 name 是否在 v2 索引中（精确或去尾缀）；在 = 命中，不在 = 透传未命中。"""
    from s01b_project_extractor.stages.entity_normalize import (
        _hierarchy_index_v2, _strip_suffix, _stripped_index_cache,
    )
    # 确保索引已初始化（同时填充去尾缀索引）
    idx = _hierarchy_index_v2()
    if name in idx:
        return True
    stripped = _strip_suffix(name)
    if stripped != name and stripped in _stripped_index_cache:
        return True
    return False


def _collect_all_misses_from_db(db_path: str) -> list[dict]:
    """扫描 DB 收集 normalize 真正未命中行（透传频次 Top50，供季度补录）。

    真正未命中（路径3）特征：source_name 既不在精确索引也不在去尾缀索引，
    即 _is_in_hierarchy 返回 False。不包括已正确识别为集团 canonical 的行。
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT implementing_entity, subsidiary_name FROM chinese_entities"
        ).fetchall()
    finally:
        conn.close()

    miss_counter: Counter = Counter()
    for r in rows:
        row = dict(r)
        src = _source_name(row)
        if not src or src == "Unknown":
            continue
        # 真正未命中：src 不在索引中（normalize 路径3会透传）
        if not _is_in_hierarchy(src):
            miss_counter[src] += 1

    top50 = [{"name": k, "count": v} for k, v in miss_counter.most_common(50)]
    return top50


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI 入口：重解析主库 chinese_entities（默认 dry-run；--apply 先备份再落库）。"""
    parser = argparse.ArgumentParser(
        description="主库 chinese_entities 实体重解析回填（v2 解析器）"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="主库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="变更计划 CSV 输出路径")
    args = parser.parse_args()

    # 路径存在性校验（项目规则4）
    try:
        validated = ReparseArgs(db=args.db, apply=args.apply, out=args.out)
    except Exception as e:
        parser.error(str(e))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = validated.out or str(
        Path(validated.db).parent / f"entity_reparse_{ts}.csv"
    )
    log_path = str(Path(out_csv).with_suffix(".log"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )

    # 健诊
    health = _health_check(validated.db)
    logger.info(
        "健诊：total=%d with_sub=%d both_empty=%d",
        health["total"], health["with_sub"], health["both_empty"],
    )

    result = reparse_maindb(validated.db, apply=validated.apply, out_csv=out_csv)

    # 未命中 Top50（supporting_docs 在 worktree 根，即 parents[4]）
    miss_csv_path = Path(__file__).resolve().parents[4] / "supporting_docs" / "entity_reparse_misses_2026-07-06.csv"
    misses = _collect_all_misses_from_db(validated.db)
    if misses:
        with open(str(miss_csv_path), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["name", "count"])
            w.writeheader()
            w.writerows(misses)
        logger.info("未命中 Top%d 已写 %s", len(misses), miss_csv_path)

    # 事后 cohort 抽查（apply 后）
    if validated.apply:
        cohorts = _cohort_check(validated.db)
        logger.info("Cohort1 抽查（能建国际）: %s", cohorts["cohort1"])
        logger.info("Cohort2 抽查（能源海外投资）: %s", cohorts["cohort2"])

    mode = "已落库" if validated.apply else "dry-run，未写库"
    print(
        f">>> entity_reparse_maindb {mode}：changed={result.get('changed', 0)} / "
        f"total={result.get('total', 0)}，"
        f"impl_changed={result.get('impl_changed', 0)}，"
        f"sub_changed={result.get('sub_changed', 0)}，"
        f"soe_changed={result.get('soe_changed', 0)}，"
        f"en_nulled={result.get('en_nulled', 0)}，"
        f"CSV: {out_csv}"
    )
    if validated.apply and result.get("bak_path"):
        print(f">>> 备份: {result['bak_path']}")


if __name__ == "__main__":
    main()
