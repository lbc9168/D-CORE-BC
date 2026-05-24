"""csv_to_gold：把人工标注后的多行 draft.csv 按 source_link 聚合成 gold_set.jsonl（correct-only 约定）。

约定：<field> 留空 = 沿用 ai_<field>；填了 = 覆盖；写空标记（- 等）= 置空。
entities 单元格写 名字(角色); 名字(角色)，转成 [{name,involvement_type}]。
status 取该篇各行的非空值；无 status 的文章跳过并告警。
"""
import argparse
import json
import logging
import pathlib
import sys

import pandas as pd
from pydantic import BaseModel, field_validator

_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from eval import gold_loader  # noqa: E402

logger = logging.getLogger(__name__)

_EMPTY_MARKERS = {"-", "—", "空", "<空>", "none", "na", "n/a", "nan"}
_VALUE_FIELDS = ("category", "capacity_mw", "project_stage", "location",
                 "total_investment_usd", "financing_model")


class ConvertArgs(BaseModel):
    """入口参数校验：输入 CSV 必须存在、输出父目录必须存在。"""
    csv_path: pathlib.Path
    out_path: pathlib.Path

    @field_validator("csv_path")
    @classmethod
    def _csv_exists(cls, v):
        if not v.is_file():
            raise ValueError(f"输入 CSV 不存在: {v}")
        return v

    @field_validator("out_path")
    @classmethod
    def _out_parent(cls, v):
        if not v.parent.is_dir():
            raise ValueError(f"输出目录不存在: {v.parent}")
        return v


def _chosen(row, field):
    """字段最终值：<field> 填了用它，否则沿用 ai_<field>。"""
    corrected = str(row.get(field, "")).strip()
    return corrected if corrected else str(row.get(f"ai_{field}", "")).strip()


def _norm_value(raw):
    """单值字段：空标记/空串 → None，否则原串。"""
    return None if (not raw or raw.lower() in _EMPTY_MARKERS) else raw


def _parse_entities(raw):
    """把 名字(角色); 名字(角色) 解析成 [{name,involvement_type}]；空标记/空 → []。
    角色取末尾的括号组（名字本身可含括号，如 中国电建(海外)有限公司）；无尾括号则整串为名字、角色为空。"""
    if not raw or raw.lower() in _EMPTY_MARKERS:
        return []
    out = []
    for part in raw.replace("；", ";").split(";"):
        part = part.strip()
        if not part:
            continue
        name, role = part, ""
        if part[-1] in ")）":                       # 末尾是右括号 → 视为 名字(角色)
            open_idx = max(part.rfind("("), part.rfind("（"))
            if open_idx > 0:                        # 括号前必须有名字
                name = part[:open_idx].strip()
                role = part[open_idx + 1:-1].strip()
        if not name:
            raise ValueError(f"实体格式无法解析（缺名字）: {part!r}")
        out.append({"name": name, "involvement_type": role or None})
    return out


def _row_to_project(row):
    """一行（有项目名/字段）→ 一个 project dict。"""
    proj = {"project_name": _chosen(row, "project_name") or None,
            "entities": _parse_entities(_chosen(row, "entities"))}
    for f in _VALUE_FIELDS:
        proj[f] = _norm_value(_chosen(row, f))
    return proj


def _has_project(row):
    """该行是否承载一个项目（有项目名或任一项目字段或实体）。"""
    if _chosen(row, "project_name"):
        return True
    if _parse_entities(_chosen(row, "entities")):
        return True
    return any(_norm_value(_chosen(row, f)) for f in _VALUE_FIELDS)


def convert(csv_path: pathlib.Path, out_path: pathlib.Path) -> int:
    """读多行标注 CSV → 按 source_link 聚合 → 写 gold_set.jsonl，返回文章数；末尾 gold_loader 校验。"""
    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, pd.errors.ParserError) as e:
        logger.error("读取标注 CSV 失败: %s | %s", csv_path, e)
        raise
    records, skipped = [], 0
    for source_link, group in df.groupby("source_link", sort=False):
        sl = str(source_link).strip()
        if not sl:
            continue
        status = ""
        for _, row in group.iterrows():
            status = _chosen(row, "status")
            if status:
                break
        if not status:
            skipped += 1
            logger.warning("文章 %s 未标注 status，跳过", sl)
            continue
        projects = [_row_to_project(row) for _, row in group.iterrows() if _has_project(row)]
        article_id = ""
        for _, row in group.iterrows():
            article_id = str(row.get("article_id", "")).strip()
            if article_id:
                break
        rec = {"source_link": sl, "status": status, "projects": projects}
        if article_id:
            rec["article_id"] = article_id
        records.append(rec)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("写 gold_set.jsonl 失败: %s | %s", out_path, e)
        raise
    gold_loader.load(out_path)
    logger.info("已写出 %d 篇 gold（跳过未标注 %d 篇）→ %s", len(records), skipped, out_path)
    return len(records)


def _setup_logging():
    """配置 logging：写 eval/csv_to_gold.log + 控制台。"""
    log_path = _root / "eval" / "csv_to_gold.log"
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
                        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()])


def main():
    """CLI：解析参数 → 校验 → convert → 打印。"""
    _setup_logging()
    parser = argparse.ArgumentParser(description="多行 draft.csv → gold_set.jsonl")
    parser.add_argument("--csv", default="eval/draft.csv")
    parser.add_argument("--out", default="eval/gold_set.jsonl")
    ns = parser.parse_args()
    args = ConvertArgs(csv_path=pathlib.Path(ns.csv), out_path=pathlib.Path(ns.out))
    n = convert(args.csv_path, args.out_path)
    print(f"完成：写出 {n} 篇 gold → {args.out_path}")


if __name__ == "__main__":
    main()
