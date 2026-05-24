"""gold_loader：读取并用 Pydantic 校验金标准评测集（gold_set.jsonl），失败即报错（外部数据边界）。"""
import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class Labels(BaseModel):
    """金标准单条记录的扁平标注：status 严格三选一，entities 必为列表，其余值字段宽松可空。"""

    model_config = ConfigDict(extra="forbid")

    status: Literal["valid", "no_chinese", "irrelevant"]
    category: Optional[str] = None
    capacity_mw: Optional[str] = None
    project_stage: Optional[str] = None
    location: Optional[str] = None
    total_investment_usd: Optional[str] = None
    financing_model: Optional[str] = None
    involvement_type: Optional[str] = None
    entities: list[str] = Field(default_factory=list)


class GoldRecord(BaseModel):
    """金标准一行：source_link 必填非空，article_id 可选，labels 走 Labels 校验。"""

    model_config = ConfigDict(extra="forbid")

    source_link: str = Field(min_length=1)
    article_id: Optional[str] = None
    labels: Labels


def load(path) -> list[dict]:
    """逐行读取 gold_set.jsonl 并用 Pydantic 校验，任一行非法立即带行号抛错；返回纯 dict 列表。"""
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error("gold_loader 第 %d 行 JSON 解析失败: %s | 内容: %s", lineno, e, line)
                raise ValueError(f"gold_set 第 {lineno} 行不是合法 JSON: {e} | 内容: {line}") from e
            try:
                rec = GoldRecord.model_validate(obj)
            except ValidationError as e:
                logger.error("gold_loader 第 %d 行校验失败: %s | 内容: %s", lineno, e, line)
                raise ValueError(f"gold_set 第 {lineno} 行校验失败: {e} | 内容: {line}") from e
            records.append(rec.model_dump())
    logger.info("gold_loader 已加载 %d 条金标准记录", len(records))
    return records
