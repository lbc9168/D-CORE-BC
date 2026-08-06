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
    """金标准单个项目：旧评分字段保持兼容，taxonomy 标签均为可选。"""
    model_config = ConfigDict(extra="forbid")
    project_name: Optional[str] = None
    category: Optional[str] = None
    project_stage: Optional[str] = None
    financing_model: Optional[str] = None
    capacity_mw: Optional[str | float | int] = None
    total_investment_usd: Optional[str | float | int] = None
    location: Optional[str] = None
    generation_sources: Optional[list[Literal["光伏", "风电", "水电", "地热及余热", "生物质", "核电", "火电"]]] = None
    storage_status: Optional[Literal["confirmed", "planned", "optional", "unclear", "not_mentioned"]] = None
    storage_power_mw: Optional[float] = None
    storage_energy_mwh: Optional[float] = None
    entities: list[Entity] = Field(default_factory=list)


class GoldRecord(BaseModel):
    """金标准一行（一篇文章）：source_link 必填，status 文章级四选一，projects 列表。"""
    model_config = ConfigDict(extra="forbid")
    source_link: str = Field(min_length=1)
    article_id: Optional[str] = None
    status: Literal["valid", "no_chinese", "irrelevant", "chinese_unnamed"]
    projects: list[Project] = Field(default_factory=list)
    dataset_split: Optional[str] = None
    risk_tags: list[str] = Field(default_factory=list)
    field_eligibility: dict[str, bool] = Field(default_factory=dict)


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
