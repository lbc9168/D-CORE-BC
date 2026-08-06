"""gold_loader：读取并用 Pydantic 校验金标准评测集（文章级 status + projects[]），失败带行号即报错。"""
import json
import logging
from typing import Literal, Optional

try:
    from ._root_modules import bri_enums
except ImportError:  # pragma: no cover - direct script fallback has no package context.
    from _root_modules import bri_enums
from pydantic import (
    BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError,
    field_validator,
)

logger = logging.getLogger(__name__)


class Entity(BaseModel):
    """项目内一家中企：name 必填非空，involvement_type 可空。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    name: StrictStr = Field(min_length=1)
    involvement_type: Optional[StrictStr] = None


class Project(BaseModel):
    """金标准单个项目：旧评分字段保持兼容，taxonomy 标签均为可选。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    project_name: Optional[StrictStr] = None
    country: Optional[StrictStr] = None
    category: Optional[StrictStr] = None
    project_stage: Optional[StrictStr] = None
    financing_model: Optional[StrictStr] = None
    capacity_mw: Optional[StrictStr | StrictFloat | StrictInt] = None
    total_investment_usd: Optional[StrictStr | StrictFloat | StrictInt] = None
    location: Optional[StrictStr] = None
    project_developer: Optional[StrictStr] = None
    planned_start_date: Optional[StrictStr] = None
    planned_complete_date: Optional[StrictStr] = None
    investment_date: Optional[StrictStr] = None
    contract_amount_usd: Optional[StrictStr | StrictFloat | StrictInt] = None
    financing_amount_usd: Optional[StrictStr | StrictFloat | StrictInt] = None
    project_summary: Optional[StrictStr] = None
    generation_sources: Optional[list[Literal["光伏", "风电", "水电", "地热及余热", "生物质", "核电", "火电"]]] = None
    storage_status: Optional[Literal["confirmed", "planned", "optional", "unclear", "not_mentioned"]] = None
    storage_power_mw: Optional[StrictFloat | StrictInt] = None
    storage_energy_mwh: Optional[StrictFloat | StrictInt] = None
    entities: list[Entity] = Field(default_factory=list)
    field_eligibility: dict[StrictStr, StrictBool] = Field(default_factory=dict)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str | None) -> str | None:
        """Reject noncanonical category labels before they can enter the canonical eval projection."""
        if value is not None and value not in bri_enums.valid_categories():
            raise ValueError("category is not a controlled enum")
        return value

    @field_validator("project_stage")
    @classmethod
    def validate_project_stage(cls, value: str | None) -> str | None:
        """Reject noncanonical project-stage labels before deterministic evaluation begins."""
        if value is not None and value not in bri_enums.valid_stages():
            raise ValueError("project_stage is not a controlled enum")
        return value

    @field_validator("financing_model")
    @classmethod
    def validate_financing_model(cls, value: str | None) -> str | None:
        """Reject noncanonical financing labels before deterministic evaluation begins."""
        if value is not None and value not in bri_enums.valid_financing():
            raise ValueError("financing_model is not a controlled enum")
        return value


class SamplingProvenance(BaseModel):
    """发布投影中的显式抽样来源：只接受可审计、不可推导替代的原始分层信息。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    sampling_seed: StrictInt
    inclusion_reasons: list[StrictStr] = Field(default_factory=list)
    original_category_strata: list[StrictStr] = Field(default_factory=list)
    risk_tags: list[StrictStr] = Field(default_factory=list)
    negative_proxy: StrictBool
    verified: Optional[StrictBool] = None
    verification_source: Optional[
        Literal["sample_selection", "operator_review", "legacy_unknown"]
    ] = None


class GoldRecord(BaseModel):
    """金标准一行（一篇文章）：source_link 必填，status 文章级四选一，projects 列表。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    source_link: StrictStr = Field(min_length=1)
    article_id: Optional[StrictStr] = None
    status: Literal["valid", "no_chinese", "irrelevant", "chinese_unnamed"]
    projects: list[Project] = Field(default_factory=list)
    dataset_split: Optional[StrictStr] = None
    risk_tags: list[StrictStr] = Field(default_factory=list)
    difficulty: Optional[dict] = None
    sampling_provenance: Optional[SamplingProvenance] = None


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
