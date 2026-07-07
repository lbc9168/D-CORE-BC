"""developer_seed_export.py —— 从主库统计高频 developer 部件、排除已可解名，产出人工核实 campaign 种子 CSV。

流程：project_events.project_developer 拆部件 → 频次≥min_freq → 用 company_lookup
排除 registry/hierarchy 已可解的部件 → 逐部件取最多3个样例项目 + 缓存现值 → 写 CSV。

用法（s02b_entity_normalizer/ 目录下）：
    uv run python -m s02b_entity_normalizer.tools.developer_seed_export
    uv run python -m s02b_entity_normalizer.tools.developer_seed_export --db PATH --min-freq 5 --out CSV
"""
import argparse
import csv
import json
import logging
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from s02b_entity_normalizer.company_lookup import LookupIndex, build_index, lookup
from s02b_entity_normalizer.developer_normalizer import DEV_JUNK_VALUES, split_developer, unify_junk
from s02b_entity_normalizer.en_backfill import DEFAULT_CACHE, _cache_en
from s02b_entity_normalizer.entity_normalizer import DEFAULT_DB

logger = logging.getLogger(__name__)

CSV_FIELDS = ["part", "freq", "sample_projects", "current_en_cache", "is_ascii", "suggested_action"]
_CJK_RE = re.compile(r"[一-鿿]")


class _ExportSeedArgs(BaseModel):
    """export_seed 入口参数校验：db 必须存在、min_freq 必须为正整数。"""
    db_path: str
    min_freq: int

    @field_validator("db_path")
    @classmethod
    def _db_exists(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"数据库不存在: {v}")
        return v

    @field_validator("min_freq")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"min_freq 必须为正整数: {v}")
        return v


def _load_cache(cache_path: str) -> Dict[str, dict]:
    """载入英文名缓存（跳过下划线开头的元信息键）。"""
    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _collect_part_stats(conn: sqlite3.Connection) -> Dict[str, dict]:
    """扫描 project_events×canonical_projects，按部件聚合频次与样例项目（最多3个，按出现顺序）。"""
    rows = conn.execute(
        "SELECT pe.canonical_id, cp.project_name, pe.project_developer "
        "FROM project_events pe JOIN canonical_projects cp ON cp.id = pe.canonical_id "
        "WHERE pe.project_developer IS NOT NULL AND TRIM(pe.project_developer) <> ''"
    ).fetchall()
    freq: Counter = Counter()
    samples: Dict[str, List[tuple]] = defaultdict(list)
    seen_per_part: Dict[str, set] = defaultdict(set)
    for canonical_id, project_name, dev_value in rows:
        if unify_junk(dev_value):
            continue
        for part in split_developer(dev_value):
            if part.lower() in DEV_JUNK_VALUES:
                continue
            freq[part] += 1
            if canonical_id not in seen_per_part[part] and len(samples[part]) < 3:
                seen_per_part[part].add(canonical_id)
                samples[part].append((canonical_id, project_name))
    return {"freq": freq, "samples": samples}


def export_seed(db_path: str, min_freq: int = 5, out_csv: Optional[str] = None,
                index: Optional[LookupIndex] = None) -> List[dict]:
    """统计高频 developer 部件、排除 registry/hierarchy 已可解者，写种子 CSV 并返回种子行列表。

    index=None 时缺省 build_index() 读真实登记册（CLI 行为）；测试可注入夹具 index 解耦真实文件。
    """
    args = _ExportSeedArgs(db_path=db_path, min_freq=min_freq)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = out_csv or str(Path(__file__).resolve().parents[4] / "supporting_docs" / f"developer_seed_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    conn = sqlite3.connect(f"file:{Path(args.db_path).as_posix()}?mode=ro", uri=True)
    try:
        stats = _collect_part_stats(conn)
    finally:
        conn.close()

    if index is None:
        index = build_index()
    cache = _load_cache(DEFAULT_CACHE)

    seed_rows: List[dict] = []
    for part, freq in stats["freq"].most_common():
        if freq < args.min_freq:
            continue
        if lookup(part, index).matched:
            continue  # registry/hierarchy 已可解，不浪费 campaign 名额
        seed_rows.append({
            "part": part,
            "freq": freq,
            "sample_projects": stats["samples"].get(part, []),
            "current_en_cache": _cache_en(cache, part),
            "is_ascii": not bool(_CJK_RE.search(part)),
            "suggested_action": "",
        })

    with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in seed_rows:
            sample_str = "; ".join(f"{cid}:{name}" for cid, name in r["sample_projects"])
            w.writerow([r["part"], r["freq"], sample_str, r["current_en_cache"],
                        r["is_ascii"], r["suggested_action"]])
    logger.info("种子导出完成：%d 个部件（min_freq=%d），CSV=%s", len(seed_rows), args.min_freq, out_csv)
    print(f">>> 种子导出 {len(seed_rows)} 个部件（min_freq={args.min_freq}）：{out_csv}")
    return seed_rows


def main() -> None:
    """CLI 入口：--db 默认真库绝对路径 / --min-freq 5 / --out 默认时间戳种子 CSV。"""
    parser = argparse.ArgumentParser(description="developer 种子导出 CLI（campaign 输入）")
    parser.add_argument("--db", type=str, default=str(DEFAULT_DB))
    parser.add_argument("--min-freq", type=int, default=5)
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()
    export_seed(args.db, min_freq=args.min_freq, out_csv=args.out)


if __name__ == "__main__":
    main()
