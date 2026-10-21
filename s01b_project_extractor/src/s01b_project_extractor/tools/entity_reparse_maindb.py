"""全库重解析回填 CLI：用 v2 确定性解析器重解析 chinese_entities 存量行。

背景：主库 chinese_entities 存量行是旧归一逻辑产物（含 1697 能建国际、121 电建海投讹变名等
错误归属）。本工具用 normalize_entity（entity_normalize.py，读当前企业登记册，默认 v4）
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
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# 主库默认路径：向上找含 s02a_duplicate_merger 的第一个父目录作为项目根
# （不假设 worktree/主工作区嵌套深度——旧 parents[7] 在主工作区导入即 IndexError）。
# 找不到时 _DEFAULT_DB=None，CLI 必须显式 --db。建议 CLI 调用时始终显式 --db。


def _find_project_root(start: Path | None = None) -> Path | None:
    """从 start（默认本文件）向上找第一个含 s02a_duplicate_merger 的父目录；找不到返回 None。"""
    base = (start or Path(__file__)).resolve()
    try:
        return next(p for p in base.parents if (p / "s02a_duplicate_merger").exists())
    except StopIteration:
        return None
    except OSError as e:
        # 文件系统异常（权限/挂载等）兜底：模块导入不因此失败，退化为要求显式 --db
        logging.error("_find_project_root 文件系统异常，回退 None: %s", e)
        return None


_PROJECT_ROOT = _find_project_root()
_DEFAULT_DB = (
    str(_PROJECT_ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db")
    if _PROJECT_ROOT is not None
    else None
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
    """WAL 安全快照到 <db>.bak.<时间戳>；备份后断言文件非空且可只读打开。返回备份路径。

    注意：移除了"内容相同则复用"的去重逻辑——该逻辑在先创建空 bak 文件后、
    backup() 返回前若发生任何情况，会导致 0 字节备份文件残留，并引发误导性报告。
    每次 apply 强制创建独立时间戳备份，确保可审计性。
    """
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

    # 断言备份有效：文件必须非空且能只读打开
    bak_size = os.path.getsize(bak)
    if bak_size == 0:
        os.remove(bak)
        raise RuntimeError(f"备份失败：{bak} 为 0 字节，已删除，主库未改动")

    # 验证备份文件可只读打开并查询行数
    try:
        verify_conn = sqlite3.connect(f"file:{bak}?mode=ro", uri=True)
        try:
            bak_count = verify_conn.execute(
                "SELECT COUNT(*) FROM chinese_entities"
            ).fetchone()[0]
        finally:
            verify_conn.close()
    except sqlite3.Error as e:
        os.remove(bak)
        raise RuntimeError(f"备份文件无法只读打开（{e}），已删除，主库未改动") from e

    # 验证备份行数与源库一致
    src_conn = sqlite3.connect(db_path)
    try:
        src_count = src_conn.execute(
            "SELECT COUNT(*) FROM chinese_entities"
        ).fetchone()[0]
    finally:
        src_conn.close()

    if bak_count != src_count:
        os.remove(bak)
        raise RuntimeError(
            f"备份行数不一致（备份={bak_count}，源库={src_count}），已删除，主库未改动"
        )

    logger.info("库已备份 %s（%d 字节，%d 行）", bak, bak_size, bak_count)
    return bak


# ── 主入口（dry-run + apply）─────────────────────────────────────────────────

def reparse_maindb(db_path: str, apply: bool = False, out_csv: str | None = None) -> dict:
    """重解析 chinese_entities 存量行；apply=True 时先备份再事务批量 UPDATE。

    返回 dict 包含：changed, total, skipped_empty, bak_path(apply 时), plan(行列表)。
    """
    if apply:
        # 旧工具只更新显示名称，不能让新主体ID/集团ID/证据与名称分离。
        connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(chinese_entities)")}
            if "entity_id" in columns:
                raise ValueError(
                    "主库已采用企业主体ID；请用 entity_intake refresh 定向更新提取行后经 S02A 重放，"
                    "旧名称全表回填不能同步主体ID与证据。"
                )
        finally:
            connection.close()
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
        # 四路分批 UPDATE（项目规则3：事务 + executemany；
        # 按 impl_en_null/sub_en_null 四组分别拼 SQL，确保只 NULL 实际变化的 en 列）
        batch_both_null = [(r["new_impl"], r["new_sub"], r["new_soe"], r["id"])
                          for r in plan if r["impl_en_null"] and r["sub_en_null"]]
        batch_impl_null = [(r["new_impl"], r["new_sub"], r["new_soe"], r["id"])
                          for r in plan if r["impl_en_null"] and not r["sub_en_null"]]
        batch_sub_null  = [(r["new_impl"], r["new_sub"], r["new_soe"], r["id"])
                          for r in plan if not r["impl_en_null"] and r["sub_en_null"]]
        batch_no_null   = [(r["new_impl"], r["new_sub"], r["new_soe"], r["id"])
                          for r in plan if not r["impl_en_null"] and not r["sub_en_null"]]

        if batch_both_null:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?,
                       implementing_entity_en=NULL, subsidiary_name_en=NULL
                   WHERE id=?""",
                batch_both_null,
            )
        if batch_impl_null:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?,
                       implementing_entity_en=NULL
                   WHERE id=?""",
                batch_impl_null,
            )
        if batch_sub_null:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?,
                       subsidiary_name_en=NULL
                   WHERE id=?""",
                batch_sub_null,
            )
        if batch_no_null:
            conn.executemany(
                """UPDATE chinese_entities
                   SET implementing_entity=?, subsidiary_name=?, is_central_soe=?
                   WHERE id=?""",
                batch_no_null,
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


# ── subsidiary_name_en 外科恢复（一次性，从有效备份恢复被过度置 NULL 的 en 值）────

def restore_sub_en_from_backup(db_path: str, bak_path: str, apply: bool = False) -> dict:
    """从有效备份恢复被过度置 NULL 的 subsidiary_name_en 字段。

    恢复条件（全部满足才恢复该行）：
      1. 现库该 id 的 subsidiary_name 非空且与 bak 中 subsidiary_name 完全相同；
      2. 现库 subsidiary_name_en IS NULL；
      3. bak 中 subsidiary_name_en 非空。

    apply=False 时只统计，不写库。apply=True 时先 .bak 备份再事务批量恢复。
    返回：{candidates, restored, bak_path}
    """
    # 读备份中有 sub_en 的行
    bak_conn = sqlite3.connect(f"file:{bak_path}?mode=ro", uri=True)
    bak_conn.row_factory = sqlite3.Row
    try:
        bak_rows = bak_conn.execute("""
            SELECT id, subsidiary_name, subsidiary_name_en
            FROM chinese_entities
            WHERE subsidiary_name IS NOT NULL AND subsidiary_name != ''
              AND subsidiary_name_en IS NOT NULL AND subsidiary_name_en != ''
        """).fetchall()
    finally:
        bak_conn.close()

    bak_index: dict[int, dict] = {int(r["id"]): dict(r) for r in bak_rows}
    logger.info("备份中 sub_en 非空行：%d 条", len(bak_index))

    # 读现库中 sub_en 为 NULL 但 sub_cn 非空的行
    cur_conn = sqlite3.connect(db_path)
    cur_conn.row_factory = sqlite3.Row
    try:
        cur_rows = cur_conn.execute("""
            SELECT id, subsidiary_name, subsidiary_name_en
            FROM chinese_entities
            WHERE subsidiary_name IS NOT NULL AND subsidiary_name != ''
              AND (subsidiary_name_en IS NULL OR subsidiary_name_en = '')
        """).fetchall()
    finally:
        cur_conn.close()

    # 筛选满足条件的恢复行
    restore_pairs: list[tuple[str, int]] = []  # (sub_en, id)
    for r in cur_rows:
        row_id = int(r["id"])
        if row_id not in bak_index:
            continue
        bak_row = bak_index[row_id]
        # 条件1：sub_cn 完全相同
        if r["subsidiary_name"] != bak_row["subsidiary_name"]:
            continue
        # 条件3 已由 bak_index 过滤（bak sub_en 非空），条件2 已由现库查询保证
        restore_pairs.append((bak_row["subsidiary_name_en"], row_id))

    logger.info(
        "外科恢复候选行：%d 条（现库 sub_cn 非空 sub_en NULL: %d 条）",
        len(restore_pairs), len(cur_rows),
    )

    result: dict[str, Any] = {
        "candidates": len(restore_pairs),
        "restored": 0,
        "bak_path": None,
    }

    if not apply:
        logger.info("dry-run：未写库，候选=%d", len(restore_pairs))
        return result

    if not restore_pairs:
        logger.info("无需恢复（0 候选行）")
        return result

    # 先备份
    new_bak = _backup_db(db_path)
    result["bak_path"] = new_bak

    # 事务批量恢复（项目规则3：批量写库必须用事务 + executemany）
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(
            "UPDATE chinese_entities SET subsidiary_name_en=? WHERE id=?",
            restore_pairs,
        )
        conn.commit()
        result["restored"] = len(restore_pairs)
        logger.info("外科恢复完成：%d 行 subsidiary_name_en 已恢复（备份 %s）", len(restore_pairs), new_bak)
    except sqlite3.Error as e:
        logger.error("外科恢复失败（备份在 %s）: %s", new_bak, e)
        raise
    finally:
        conn.close()

    return result


# ── CLI 入口 ──────────────────────────────────────────────────────────────────

def main() -> None:
    """CLI 入口：重解析主库 chinese_entities（默认 dry-run；--apply 先备份再落库）。

    附加子命令 --restore-sub-en <bak_path>：从有效备份恢复被过度置 NULL 的 subsidiary_name_en。
    """
    parser = argparse.ArgumentParser(
        description="主库 chinese_entities 实体重解析回填（v2 解析器）"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="主库路径")
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="变更计划 CSV 输出路径")
    parser.add_argument(
        "--restore-sub-en", metavar="BAK_PATH", default=None,
        help="外科恢复模式：从指定备份文件恢复被过度置 NULL 的 subsidiary_name_en（搭配 --apply 落库）",
    )
    args = parser.parse_args()

    # 项目根定位失败（_DEFAULT_DB=None）且未显式传 --db → 立即报错
    if not args.db:
        parser.error("--db 必填：未能自动定位项目根（向上未找到 s02a_duplicate_merger 目录）")

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

    # ── 外科恢复模式（--restore-sub-en）──────────────────────────────────────
    if args.restore_sub_en:
        bak_path = args.restore_sub_en
        if not Path(bak_path).exists():
            parser.error(f"备份文件不存在: {bak_path}")
        restore_result = restore_sub_en_from_backup(
            validated.db, bak_path, apply=validated.apply
        )
        mode = "已恢复落库" if validated.apply else "dry-run"
        print(
            f">>> restore_sub_en {mode}：candidates={restore_result['candidates']}，"
            f"restored={restore_result['restored']}"
        )
        if validated.apply and restore_result.get("bak_path"):
            print(f">>> 备份: {restore_result['bak_path']}")
        return

    # ── 常规重解析回填模式 ────────────────────────────────────────────────────

    # 健诊
    health = _health_check(validated.db)
    logger.info(
        "健诊：total=%d with_sub=%d both_empty=%d",
        health["total"], health["with_sub"], health["both_empty"],
    )

    result = reparse_maindb(validated.db, apply=validated.apply, out_csv=out_csv)

    # 未命中 Top50：写 supporting_docs（项目根健壮上溯；找不到根则落在 out_csv 同目录）；
    # 文件名带时间戳，避免重跑覆盖历史报告
    miss_dir = (_PROJECT_ROOT / "supporting_docs") if _PROJECT_ROOT else Path(out_csv).parent
    miss_csv_path = miss_dir / f"entity_reparse_misses_{ts}.csv"
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
