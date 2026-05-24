"""gold_loader：读取并用 Pydantic 校验金标准评测集（文章级 status + projects[]），失败带行号即报错。"""
import json
import logging
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


class Entity(BaseModel):
    """项目内一家中企：name 必填非空，involvement_type 可空。"""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    involvement_type: Optional[str] = None


class Project(BaseModel):
    """金标准单个项目：project_name 仅对齐用，5 个评分字段 + location 可空，entities 列表。"""
    model_config = ConfigDict(extra="forbid")
    project_name: Optional[str] = None
    category: Optional[str] = None
    project_stage: Optional[str] = None
    financing_model: Optional[str] = None
    capacity_mw: Optional[str] = None
    total_investment_usd: Optional[str] = None
    location: Optional[str] = None
    entities: list[Entity] = Field(default_factory=list)


class GoldRecord(BaseModel):
    """金标准一行（一篇文章）：source_link 必填，status 文章级三选一，projects 列表。"""
    model_config = ConfigDict(extra="forbid")
    source_link: str = Field(min_length=1)
    article_id: Optional[str] = None
    status: Literal["valid", "no_chinese", "irrelevant"]
    projects: list[Project] = Field(default_factory=list)


def load(path) -> list[dict]:
    """逐行读取 gold_set.jsonl 并校验，任一行非法立即带行号抛错；返回纯 dict 列表。"""
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
