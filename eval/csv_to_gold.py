"""csv_to_gold：把人工标注后的 draft.csv 转成 gold_set.jsonl（"只改错的"约定 + Pydantic 校验）。

标注约定（correct-only）：
- 每个字段 <field> 列留空 = "AI 的 ai_<field> 值是对的"，转换时沿用 ai_<field>。
- <field> 列填了值 = 用你填的值覆盖。
- 真实值确为"空/无"时，在 <field> 列写一个空标记（- 或 — 或 空 或 none/na），转换为 None / 空实体集。
- status 没有 ai 兜底语义之外的特殊处理：final = status(填了) 否则 ai_status；为空则该行视为未标注，跳过并告警。
- entities 用分号(; 或 ；)分隔，转成字符串列表。
"""
import argparse
import json
import logging
import pathlib
import sys

import pandas as pd
from pydantic import BaseModel, field_validator

# 健壮上溯找仓库根（含 bri_enums.py），与 eval 其余模块一致
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from eval import gold_loader  # noqa: E402  校验输出用

logger = logging.getLogger(__name__)

# 人工填写表示"真实为空"的标记（大小写不敏感），转换为 None / 空列表
_EMPTY_MARKERS = {"-", "—", "空", "<空>", "none", "na", "n/a", "nan"}

# status 之外、entities 之前的 7 个值字段（每个有 ai_<f> 兜底 + <f> 修正两列）
_VALUE_FIELDS = (
    "category", "capacity_mw", "project_stage", "location",
    "total_investment_usd", "financing_model", "involvement_type",
)


class ConvertArgs(BaseModel):
    """csv_to_gold 入口参数校验：输入 CSV 必须存在，输出父目录必须存在。"""

    csv_path: pathlib.Path
    out_path: pathlib.Path

    @field_validator("csv_path")
    @classmethod
    def _csv_exists(cls, v: pathlib.Path) -> pathlib.Path:
        """输入 CSV 不存在立即报错。"""
        if not v.is_file():
            raise ValueError(f"输入 CSV 不存在: {v}")
        return v

    @field_validator("out_path")
    @classmethod
    def _out_parent_exists(cls, v: pathlib.Path) -> pathlib.Path:
        """输出路径父目录不存在立即报错。"""
        if not v.parent.is_dir():
            raise ValueError(f"输出目录不存在: {v.parent}")
        return v


def _chosen(row: "pd.Series", field: str) -> str:
    """取某字段的最终值：<field> 填了用它，否则沿用 ai_<field>（均已 strip）。"""
    corrected = str(row.get(field, "")).strip()
    if corrected:
        return corrected
    return str(row.get(f"ai_{field}", "")).strip()


def _normalize_value(raw: str):
    """把单值字段的字符串转为 gold 值：空标记 → None，否则原字符串。"""
    if not raw or raw.lower() in _EMPTY_MARKERS:
        return None
    return raw


def _normalize_entities(raw: str) -> list[str]:
    """把实体字段字符串按 ; / ； 分隔成列表；空标记或空串 → 空列表。"""
    if not raw or raw.lower() in _EMPTY_MARKERS:
        return []
    parts = raw.replace("；", ";").split(";")
    return [p.strip() for p in parts if p.strip()]


def _row_to_record(row: "pd.Series") -> dict | None:
    """把一行（correct-only 约定）转成 gold 记录 dict；status 为空（未标注）则返回 None。"""
    source_link = str(row.get("source_link", "")).strip()
    if not source_link:
        return None
    status = _chosen(row, "status")
    if not status:
        return None  # 未标注（如负样本行未填 status），交由上层跳过+告警
    labels = {"status": status, "entities": _normalize_entities(_chosen(row, "entities"))}
    for f in _VALUE_FIELDS:
        val = _normalize_value(_chosen(row, f))
        if val is not None:
            labels[f] = val
    article_id = str(row.get("article_id", "")).strip()
    rec = {"source_link": source_link, "labels": labels}
    if article_id:
        rec["article_id"] = article_id
    return rec


def convert(csv_path: pathlib.Path, out_path: pathlib.Path) -> int:
    """读人工标注 CSV → 写 gold_set.jsonl，返回写出条数；末尾用 gold_loader 校验产物。"""
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, pd.errors.ParserError) as e:
        logger.error("读取标注 CSV 失败: %s | %s", csv_path, e)
        raise
    records: list[dict] = []
    skipped = 0
    for idx, row in df.iterrows():
        rec = _row_to_record(row)
        if rec is None:
            skipped += 1
            logger.warning("第 %s 行未标注 status（或缺 source_link），跳过", idx + 2)  # +2: 表头占1行、0基索引
            continue
        records.append(rec)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("写 gold_set.jsonl 失败: %s | %s", out_path, e)
        raise
    # 立即用 gold_loader 校验产物，任何非法行会带行号抛错（外部数据边界）
    gold_loader.load(out_path)
    logger.info("已写出 %d 条 gold（跳过未标注 %d 条）→ %s", len(records), skipped, out_path)
    return len(records)


def _setup_logging() -> None:
    """配置 logging：写文件 eval/csv_to_gold.log + 控制台，含时间戳/模块/级别。"""
    log_path = _root / "eval" / "csv_to_gold.log"
    handlers = [logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    """CLI 入口：解析参数 → Pydantic 校验 → convert → 打印结果。"""
    _setup_logging()
    parser = argparse.ArgumentParser(description="把人工标注的 draft.csv 转成 gold_set.jsonl")
    parser.add_argument("--csv", default="eval/draft.csv", help="标注后的 CSV 路径")
    parser.add_argument("--out", default="eval/gold_set.jsonl", help="输出 gold_set.jsonl 路径")
    ns = parser.parse_args()
    args = ConvertArgs(csv_path=pathlib.Path(ns.csv), out_path=pathlib.Path(ns.out))
    n = convert(args.csv_path, args.out_path)
    print(f"完成：写出 {n} 条 gold → {args.out_path}")


if __name__ == "__main__":
    main()
