"""developer_normalizer.py — 业主(project_events.project_developer)归一化。

流程：整值垃圾占位统一 Unknown → 拆串取部件（带频次）→ 复用 entity_normalizer 三信号
候选 + LLM 批量确认 + Union-Find 映射 → 保守重建每个业主串（部件有变化才改）。
developer_en 不在此处理——由 en_backfill 在归一后统一重算。

用法（s02b_entity_normalizer/ 目录下）：
    uv run normalize_developers --preview        # 只看候选与 would-change，不写库
    uv run normalize_developers                  # LLM 确认后写库（.bak 先备份）
    uv run normalize_developers --db PATH --out CSV
"""
import argparse
import csv
import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from s02b_entity_normalizer.entity_normalizer import (
    BATCH_SIZE, DEFAULT_DB, _build_mapping, _find_candidates, _llm_confirm_batch,
)

logger = logging.getLogger(__name__)
_S02B_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_S02B_ROOT / ".env")
load_dotenv()

DEV_SEP_RE = re.compile(r"[;；、，,/]+")
# 整值等于这些（大小写无关）→ 统一 'Unknown'（"业主"是占位词不是公司名）
DEV_JUNK_VALUES = {"unknown", "未知", "不详", "不明", "暂无", "n/a", "na", "-", "业主", "业主方"}


def split_developer(value) -> List[str]:
    """按分隔符拆业主串为公司名列表（去空白、去空段）。"""
    s = str(value or "").strip()
    if not s:
        return []
    return [p.strip() for p in DEV_SEP_RE.split(s) if p.strip()]


def unify_junk(value) -> Optional[str]:
    """整值为垃圾占位（业主/未知/N/A 等）→ 'Unknown'；否则 None（不改）。"""
    s = str(value or "").strip()
    return "Unknown" if s.lower() in DEV_JUNK_VALUES else None


def rebuild(value: str, part_mapping: Dict[str, str]) -> str:
    """按部件映射重建业主串：任一部件命中映射才改写（'; ' 拼回）；否则原样返回（含分隔符风格）。"""
    parts = split_developer(value)
    if not parts:
        return value
    mapped = [part_mapping.get(p, p) for p in parts]
    if mapped == parts:
        return value
    return "; ".join(mapped) if len(mapped) > 1 else mapped[0]


def plan_value_changes(conn, part_mapping: Dict[str, str]) -> List[Tuple[str, str]]:
    """扫描 distinct 业主值 → (old, new) 变更对（垃圾值统一 + 部件映射重建，只列有变化的）。"""
    rows = conn.execute(
        "SELECT project_developer FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    pairs: List[Tuple[str, str]] = []
    for (v,) in rows:
        new = unify_junk(v) or rebuild(v, part_mapping)
        if new != v:
            pairs.append((v, new))
    return pairs


def apply_value_changes(db_path: str, pairs: List[Tuple[str, str]]) -> int:
    """先备份 .bak，再事务 executemany 改写 project_developer；返回受影响行数。"""
    if not pairs:
        return 0
    bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(db_path, bak)
    logger.info("主库已备份 %s", bak)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.executemany(
            "UPDATE project_events SET project_developer = ? WHERE project_developer = ?",
            [(new, old) for old, new in pairs],
        )
        conn.commit()
        logger.info("project_developer 归一落库 %d 行（%d 个不重复值）", cur.rowcount, len(pairs))
        return cur.rowcount
    except sqlite3.Error as e:
        logger.error("落库失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def _collect_parts(conn) -> List[Tuple[str, int]]:
    """拆串统计部件频次（按值出现行数加权），排除 Unknown 与纯 ASCII 部件。"""
    rows = conn.execute(
        "SELECT project_developer, COUNT(*) FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    counter: Counter = Counter()
    for v, n in rows:
        if unify_junk(v):
            continue
        for p in split_developer(v):
            if p.lower() in DEV_JUNK_VALUES or not re.search(r"[一-鿿]", p):
                continue
            counter[p] += n
    return counter.most_common()


def run(db_path: Optional[str] = None, preview: bool = False) -> None:
    """主流程：候选筛查 →（非 preview）LLM 确认 → 映射 → would-change CSV →（非 preview）落库。"""
    if db_path is None and preview is False:
        parser = argparse.ArgumentParser(description="业主(project_developer)归一化")
        parser.add_argument("--preview", action="store_true", help="只看候选与 would-change，不写库")
        parser.add_argument("--db", type=str, default=None)
        parser.add_argument("--out", type=str, default=None, help="would-change CSV 路径")
        args, _ = parser.parse_known_args()
        preview = args.preview
        db_path = args.db
        out_csv = args.out
    else:
        out_csv = None

    if db_path is None:
        db_path = str(DEFAULT_DB)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = out_csv or str(Path(db_path).parent / f"developer_normalize_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    conn = sqlite3.connect(db_path)
    try:
        parts = _collect_parts(conn)
        print(f">>> 业主部件（中文、去垃圾）：{len(parts)} 个")
        candidates = _find_candidates(parts)
        print(f">>> 三信号候选对：{len(candidates)}")

        if preview:
            pairs = plan_value_changes(conn, {})
            _write_csv(out_csv, candidates, pairs)
            print(f">>> [preview] 垃圾值统一 would-change {len(pairs)} 个值；候选对已写 {out_csv}（LLM 未调用）")
            return

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未找到 GEMINI_API_KEY（.env）")
        from google import genai
        client = genai.Client(api_key=api_key)
        confirmed = []
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            print(f">>> LLM 确认批次 {i // BATCH_SIZE + 1}/{(len(candidates) - 1) // BATCH_SIZE + 1}")
            confirmed.extend(_llm_confirm_batch(client, batch))
            time.sleep(0.5)
        mapping = _build_mapping(parts, confirmed)
        print(f">>> 部件映射 {len(mapping)} 条")
        pairs = plan_value_changes(conn, mapping)
        _write_csv(out_csv, candidates, pairs, mapping)
        print(f">>> would-change {len(pairs)} 个不重复值，明细 {out_csv}")
    finally:
        conn.close()

    if not preview and pairs:
        n = apply_value_changes(db_path, pairs)
        print(f">>> 已落库 {n} 行。注意：developer_en 需随后跑 backfill_entity_en 重算。")


def _write_csv(path: str, candidates: List[dict], pairs: List[Tuple[str, str]],
               mapping: Optional[Dict[str, str]] = None) -> None:
    """把候选对 / 部件映射 / would-change 值三段写入一个报告 CSV。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "a", "b", "note"])
        for c in candidates:
            w.writerow(["candidate", c["name_a"], c["name_b"], c["signals"]])
        for alias, canon in sorted((mapping or {}).items()):
            w.writerow(["part_mapping", alias, canon, ""])
        for old, new in pairs:
            w.writerow(["value_change", old, new, ""])


if __name__ == "__main__":
    run()
