"""Codex 季度分拣管线的 Pydantic v2 数据契约：队列条目与 verdict 模型。"""
from __future__ import annotations

import logging
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 枚举（Literal 类型别名）
# ──────────────────────────────────────────────

Confidence = Literal["high", "medium", "low"]
T2Action = Literal["keep", "exclude", "downgrade_non_project"]
T3Classification = Literal[
    "new_group",
    "alias_of_existing",
    "subsidiary_of_existing",
    "subsidiary_of_new",
    "not_chinese",
    "not_company",
    "uncertain",
]
TaskName = Literal["T1", "T2", "T3"]

# ──────────────────────────────────────────────
# 队列侧模型
# ──────────────────────────────────────────────

_QUARTER_RE = re.compile(r"^20\d{2}Q[1-4]$")


class EventContext(BaseModel):
    """单条事件的上下文信息，供 Codex 分拣时参考。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    project_stage: str
    capacity_mw: float | None = None
    capacity_mw_raw: str | None = None
    publish_date: str | None = None
    project_summary: str | None = None
    source_links: list[str] = Field(default_factory=list)


class ProjectContext(BaseModel):
    """单个项目的完整上下文，含事件列表与实体名列表。"""

    model_config = ConfigDict(extra="forbid")

    canonical_id: str
    project_name: str
    project_name_en: str | None = None
    country: str
    country_iso: str | None = None
    category: str
    events: list[EventContext]
    entity_names: list[str] = Field(default_factory=list)


class T1QueueItem(BaseModel):
    """T1 漏合并对：两个疑似同一项目的 ProjectContext 对。"""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    similarity: Annotated[float, Field(ge=0.0, le=1.0)]
    both_sides_visible: bool
    a: ProjectContext
    b: ProjectContext


class T2QueueItem(BaseModel):
    """T2 范围复核：需审核是否应排除/降级的单个项目条目。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    project_id: str
    trigger: Literal["fixed_list", "no_chinese_entity_scan", "mf_boundary_scan"]
    audit_note: str | None = None
    project: ProjectContext


class T3QueueItem(BaseModel):
    """T3 实体归属补录：待分拣的原始实体名及其已在册集团参考。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    raw_name: str
    freq: Annotated[int, Field(ge=1)]
    registry_groups: list[str]


class T1QueueFile(BaseModel):
    """T1 队列文件：漏合并对的完整导出，含 quarter 格式校验。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T1"]
    quarter: str
    generated_at: str
    source_db: str | None = None
    items: list[T1QueueItem]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


class T2QueueFile(BaseModel):
    """T2 队列文件：范围复核条目的完整导出，含 quarter 格式校验。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T2"]
    quarter: str
    generated_at: str
    source_db: str | None = None
    items: list[T2QueueItem]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


class T3QueueFile(BaseModel):
    """T3 队列文件：实体归属补录条目的完整导出，含 quarter 格式校验。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T3"]
    quarter: str
    generated_at: str
    source_db: str | None = None
    items: list[T3QueueItem]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


# ──────────────────────────────────────────────
# verdict 侧模型
# ──────────────────────────────────────────────

_T3_REQUIRE_GROUP = frozenset(
    ["new_group", "alias_of_existing", "subsidiary_of_existing", "subsidiary_of_new"]
)


def _nonempty_str(v: str) -> str:
    """校验字符串 strip 后非空。"""
    if not v.strip():
        raise ValueError("字段 strip 后不得为空白字符串")
    return v


class T1Verdict(BaseModel):
    """T1 漏合并对的判定结果；same_project=True 时 survivor_id 必填。"""

    model_config = ConfigDict(extra="forbid")

    pair_id: str
    same_project: bool
    confidence: Confidence
    evidence_quote: Annotated[str, Field(min_length=1)]
    survivor_id: str | None = None
    reason: Annotated[str, Field(min_length=1)]

    @field_validator("evidence_quote")
    @classmethod
    def _evidence_nonempty(cls, v: str) -> str:
        """校验 evidence_quote strip 后非空。"""
        return _nonempty_str(v)

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        """校验 reason strip 后非空。"""
        return _nonempty_str(v)

    @model_validator(mode="after")
    def _survivor_consistency(self) -> T1Verdict:
        """same_project=True 时 survivor_id 必填；False 时必须为 None。"""
        if self.same_project and self.survivor_id is None:
            raise ValueError("same_project=True 时 survivor_id 必填")
        if not self.same_project and self.survivor_id is not None:
            raise ValueError("same_project=False 时 survivor_id 必须为 None")
        return self


class T2Verdict(BaseModel):
    """T2 范围复核的判定结果。"""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    action: T2Action
    reason: Annotated[str, Field(min_length=1)]
    evidence_quote: Annotated[str, Field(min_length=1)]

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        """校验 reason strip 后非空。"""
        return _nonempty_str(v)

    @field_validator("evidence_quote")
    @classmethod
    def _evidence_nonempty(cls, v: str) -> str:
        """校验 evidence_quote strip 后非空。"""
        return _nonempty_str(v)


class T3Verdict(BaseModel):
    """T3 实体归属补录的判定结果；特定分类必须填 canonical_group_cn 和 source_urls。"""

    model_config = ConfigDict(extra="forbid")

    raw_name: str
    classification: T3Classification
    canonical_group_cn: str | None = None
    legal_full_name_cn: str | None = None
    parent_cn: str | None = None
    is_central_soe: bool | None = None
    source_urls: list[str]
    confidence: Confidence
    notes: str

    @model_validator(mode="after")
    def _group_required(self) -> T3Verdict:
        """new_group/alias_of_existing/subsidiary_* 分类须填 canonical_group_cn 且 source_urls≥1。"""
        if self.classification in _T3_REQUIRE_GROUP:
            if not self.canonical_group_cn or not self.canonical_group_cn.strip():
                raise ValueError(
                    f"classification={self.classification!r} 时 canonical_group_cn 必填"
                )
            if not self.source_urls:
                raise ValueError(
                    f"classification={self.classification!r} 时 source_urls 至少 1 条"
                )
        return self


# ──────────────────────────────────────────────
# verdict 文件
# ──────────────────────────────────────────────


class T1VerdictsFile(BaseModel):
    """T1 判定结果文件。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T1"]
    quarter: str
    verdicts: list[T1Verdict]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


class T2VerdictsFile(BaseModel):
    """T2 判定结果文件。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T2"]
    quarter: str
    verdicts: list[T2Verdict]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v


class T3VerdictsFile(BaseModel):
    """T3 判定结果文件。"""

    model_config = ConfigDict(extra="forbid")

    task: Literal["T3"]
    quarter: str
    verdicts: list[T3Verdict]

    @field_validator("quarter")
    @classmethod
    def _check_quarter(cls, v: str) -> str:
        """校验 quarter 格式为 20xxQ[1-4]。"""
        if not _QUARTER_RE.match(v):
            raise ValueError(f"quarter 格式非法，期望 20xxQ[1-4]，收到 {v!r}")
        return v
