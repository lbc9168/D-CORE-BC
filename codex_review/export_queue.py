"""Codex 季度分拣队列导出 CLI：从各数据源生成 queue_T*.json + guide_T*.md。

用法：
    uv run python -m codex_review.export_queue --task T3 --quarter 2026Q3 [选项]

T1/T2 暂未实现，后续任务补充。
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from codex_review.ledger import is_settled, load_ledger, t3_key
from codex_review.schemas import T3QueueFile, T3QueueItem

# ──────────────────────────────────────────────
# 常量与默认路径
# ──────────────────────────────────────────────

_MODULE_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _MODULE_DIR.parent

_DEFAULT_MISSES_CSV = (
    _WORKTREE_ROOT / "supporting_docs" / "entity_reparse_misses_20260706_023835.csv"
)
_DEFAULT_REGISTRY = _WORKTREE_ROOT / "supporting_docs" / "company_hierarchy_v2.json"
_DEFAULT_OUT_DIR = _MODULE_DIR / "runs"
_DEFAULT_LEDGER = _MODULE_DIR / "ledger.json"
_GUIDES_DIR = _MODULE_DIR / "guides"
_LOGS_DIR = _MODULE_DIR / "logs"

_QUARTER_RE = re.compile(r"^20\d{2}Q[1-4]$")

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 入口参数校验模型（项目规则 4）
# ──────────────────────────────────────────────


class T3ExportParams(BaseModel):
    """T3 导出的入口参数，含格式与路径存在性校验。"""

    quarter: str
    misses_csv: Path
    registry: Path
    out_dir: Path
    ledger_path: Path

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v

    @field_validator("misses_csv")
    @classmethod
    def _check_misses_csv(cls, v: Path) -> Path:
        """校验 misses_csv 路径存在。"""
        if not v.exists():
            raise ValueError(f"misses_csv 路径不存在：{v}")
        return v

    @field_validator("registry")
    @classmethod
    def _check_registry(cls, v: Path) -> Path:
        """校验 registry JSON 路径存在。"""
        if not v.exists():
            raise ValueError(f"registry 路径不存在：{v}")
        return v


# ──────────────────────────────────────────────
# registry 覆盖集合构建
# ──────────────────────────────────────────────


def _build_covered_set(groups: list[dict]) -> set[str]:
    """从 registry groups 列表构建所有已覆盖名称的精确匹配集合（strip 后）。"""
    covered: set[str] = set()
    for g in groups:
        name_cn = g.get("canonical_name_cn", "")
        if name_cn:
            covered.add(name_cn.strip())
        for alias in g.get("aliases", []):
            if alias:
                covered.add(alias.strip())
        for sub in g.get("subsidiaries", []):
            for field in ("name_cn", "legal_full_name_cn"):
                val = sub.get(field, "")
                if val:
                    covered.add(val.strip())
    return covered


# ──────────────────────────────────────────────
# 日志初始化
# ──────────────────────────────────────────────


def _setup_logging(task: str) -> None:
    """初始化 logging：同时输出 console 和带时间戳的日志文件。"""
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = _LOGS_DIR / f"export_{task}_{timestamp}.log"

    fmt = "%(asctime)s [%(name)s] %(levelname)s %(message)s"
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
    logger.info("日志文件：%s", log_file)


# ──────────────────────────────────────────────
# T3 核心导出逻辑
# ──────────────────────────────────────────────


def export_t3(
    *,
    quarter: str,
    misses_csv: Path,
    registry: Path,
    out_dir: Path,
    ledger_path: Path,
) -> None:
    """导出 T3 实体归属补录队列到 <out_dir>/<quarter>/queue_T3.json + guide_T3.md。"""
    # 入口参数校验（项目规则 4）
    params = T3ExportParams(
        quarter=quarter,
        misses_csv=Path(misses_csv),
        registry=Path(registry),
        out_dir=Path(out_dir),
        ledger_path=Path(ledger_path),
    )

    # 加载台账（只读，不写）
    ledger = load_ledger(params.ledger_path)

    # 加载 registry，构建覆盖集合与 registry_groups 列表
    try:
        with open(params.registry, "r", encoding="utf-8") as f:
            reg_data: dict[str, Any] = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("registry JSON 解析失败：%s", e)
        raise ValueError(f"registry JSON 解析失败：{params.registry}") from e

    groups: list[dict] = reg_data.get("groups", [])
    covered_set = _build_covered_set(groups)
    registry_groups = [g["canonical_name_cn"] for g in groups if g.get("canonical_name_cn")]
    logger.info("registry 加载完成：%d 组，覆盖名称 %d 个", len(groups), len(covered_set))

    # 读 misses CSV（utf-8-sig 容错 BOM）
    rows: list[tuple[str, int]] = []
    try:
        with open(params.misses_csv, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                count_str = (row.get("count") or "").strip()
                if not name or not count_str:
                    continue
                try:
                    count = int(count_str)
                except ValueError as e:
                    logger.error("CSV count 解析失败，行=%r，错误=%s", row, e)
                    raise
                rows.append((name, count))
    except OSError as e:
        logger.error("读 misses CSV 失败：%s", e)
        raise

    logger.info("misses CSV 读入 %d 行", len(rows))

    # 逐行过滤：registry 覆盖 + 台账去重
    items: list[T3QueueItem] = []
    skipped_covered = 0
    skipped_settled = 0

    for name, count in rows:
        if name.strip() in covered_set:
            skipped_covered += 1
            logger.debug("registry 已覆盖，跳过：%s", name)
            continue
        key = t3_key(name)
        if is_settled(ledger, key):
            skipped_settled += 1
            logger.debug("台账已定案，跳过：%s", name)
            continue
        items.append(
            T3QueueItem(
                item_id=key,
                raw_name=name,
                freq=count,
                registry_groups=registry_groups,
            )
        )

    # freq 降序排序
    items.sort(key=lambda x: x.freq, reverse=True)

    logger.info(
        "统计：读入 %d 条，registry 已覆盖跳过 %d 条，台账已定案跳过 %d 条，入队 %d 条",
        len(rows),
        skipped_covered,
        skipped_settled,
        len(items),
    )
    print(
        f"T3 导出：读入 {len(rows)} 条，"
        f"registry 已覆盖跳过 {skipped_covered} 条，"
        f"台账已定案跳过 {skipped_settled} 条，"
        f"入队 {len(items)} 条"
    )

    # 组装 T3QueueFile 并 Pydantic 校验（先校验再落盘）
    queue_file = T3QueueFile(
        task="T3",
        quarter=params.quarter,
        generated_at=datetime.now(timezone.utc).isoformat(),
        items=items,
    )

    # 写出文件
    quarter_dir = Path(params.out_dir) / params.quarter
    quarter_dir.mkdir(parents=True, exist_ok=True)

    out_json = quarter_dir / "queue_T3.json"
    out_json.write_text(
        json.dumps(queue_file.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("队列文件已写出：%s", out_json)

    # 复制分拣指南
    guide_src = _GUIDES_DIR / "guide_T3.md"
    guide_dst = quarter_dir / "guide_T3.md"
    if guide_src.exists():
        shutil.copy2(guide_src, guide_dst)
        logger.info("分拣指南已复制：%s → %s", guide_src, guide_dst)
    else:
        logger.warning("分拣指南模板不存在，跳过复制：%s", guide_src)

    print(f"输出目录：{quarter_dir}")


# ──────────────────────────────────────────────
# T1/T2 占位（后续任务实现）
# ──────────────────────────────────────────────

_NOT_IMPL = {"T1": "T1 漏合并检测尚未实现", "T2": "T2 范围复核尚未实现"}


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。"""
    p = argparse.ArgumentParser(
        description="Codex 季度分拣队列导出工具",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--task", choices=["T1", "T2", "T3"], required=True, help="任务类型")
    p.add_argument("--quarter", required=True, help="季度，格式 20xxQ[1-4]，例如 2026Q3")
    p.add_argument(
        "--misses-csv",
        default=str(_DEFAULT_MISSES_CSV),
        help="实体解析未命中 CSV（列 name,count）",
    )
    p.add_argument(
        "--registry",
        default=str(_DEFAULT_REGISTRY),
        help="集团层级 JSON（company_hierarchy_v2.json）",
    )
    p.add_argument("--out-dir", default=str(_DEFAULT_OUT_DIR), help="输出根目录")
    p.add_argument("--ledger", default=str(_DEFAULT_LEDGER), help="台账 JSON 路径")
    return p


def main() -> None:
    """CLI 主入口：解析参数并 dispatch 到对应任务。"""
    parser = _build_parser()
    args = parser.parse_args()

    _setup_logging(args.task)

    # dispatch 表：T1/T2 暂未实现，T3 已实现
    _dispatch: dict[str, Any] = {
        "T3": lambda: export_t3(
            quarter=args.quarter,
            misses_csv=Path(args.misses_csv),
            registry=Path(args.registry),
            out_dir=Path(args.out_dir),
            ledger_path=Path(args.ledger),
        ),
    }

    if args.task in _NOT_IMPL:
        print(f"[ERROR] {_NOT_IMPL[args.task]}，请等待后续任务实现。", file=sys.stderr)
        sys.exit(1)

    try:
        _dispatch[args.task]()
    except (ValueError, FileNotFoundError) as e:
        logger.error("导出失败：%s", e)
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
