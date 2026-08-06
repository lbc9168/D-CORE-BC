"""Model strict rich gold, evidence, candidates, difficulty, and sampling provenance for Studio."""

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, model_validator

from gold_standard_studio.field_registry import FIELD_REGISTRY, validate_entity_role, validate_field_value


FieldState = Literal[
    "unreviewed", "present", "not_mentioned", "not_applicable", "uncertain", "needs_adjudication"
]
ArticleReviewStatus = Literal["unreviewed", "reviewed"]
ArticleGoldStatus = Literal["unreviewed", "valid", "no_chinese", "irrelevant", "chinese_unnamed"]
CandidateStatus = Literal["ok", "error"]


class StrictContractModel(BaseModel):
    """Forbid undeclared keys at every persisted or public Pydantic contract boundary."""

    model_config = ConfigDict(extra="forbid")


class EvidenceSpan(StrictContractModel):
    """Capture a quoted span from the immutable source article."""

    quote: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["article"] = "article"

    @model_validator(mode="after")
    def validate_offsets(self):
        """Ensure the evidence end offset follows its start offset."""
        if self.end <= self.start:
            raise ValueError("evidence end must be greater than start")
        return self


class GoldField(StrictContractModel):
    """Represent one formal field with its evidence-backed semantic state."""

    state: FieldState
    value: JsonValue | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    evidence_required: bool = False

    @model_validator(mode="after")
    def validate_state(self):
        """Enforce that only present fields carry values and critical values carry evidence."""
        if self.state == "present" and self.value is None:
            raise ValueError("present field requires value")
        if self.state != "present" and self.value is not None:
            raise ValueError("non-present field cannot carry value")
        if self.state == "present" and self.evidence_required and not self.evidence:
            raise ValueError("present critical field requires evidence")
        return self


class GoldEntity(StrictContractModel):
    """Represent one role-specific project entity and the evidence supporting that role."""

    role: str = Field(min_length=1)
    name: str = Field(min_length=1)
    normalized_name: str | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)


class GoldProject(StrictContractModel):
    """Group stable project identity, formal fields, and role-specific entities for an article."""

    gold_project_id: str = Field(min_length=1)
    fields: dict[str, GoldField] = Field(default_factory=dict)
    entities: list[GoldEntity] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_known_fields(self):
        """Reject arbitrary project keys while allowing source candidates to retain dirty typed values for review."""
        unknown = sorted(set(self.fields).difference(FIELD_REGISTRY))
        if unknown:
            raise ValueError(f"project contains unknown registry fields: {unknown}")
        return self


class DifficultyScore(StrictContractModel):
    """Store explainable review difficulty and risk signals for an article."""

    score: float = Field(ge=0)
    reason_codes: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)


class CandidateValue(StrictContractModel):
    """Store one candidate source's structured project interpretation or a structured failure."""

    source_name: str = Field(min_length=1)
    source_version: str = ""
    status: CandidateStatus = "ok"
    projects: list[GoldProject] = Field(default_factory=list)
    error_code: str | None = None
    error_detail: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class CandidateBundle(StrictContractModel):
    """Collect all independently produced candidate values for one immutable article snapshot."""

    sample_id: str = Field(min_length=1)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources: dict[str, CandidateValue] = Field(default_factory=dict)


class DatasetManifest(StrictContractModel):
    """Describe the deterministic sample and immutable source hashes behind a gold dataset."""

    dataset_version: str = "draft"
    seed: int | None = None
    sample_ids: list[str] = Field(default_factory=list)
    source_hashes: dict[str, str] = Field(default_factory=dict)


class SamplingProvenance(StrictContractModel):
    """Preserve the explicit, publishable sampling decision that selected one article."""

    sampling_seed: int
    inclusion_reasons: list[str] = Field(default_factory=list)
    original_category_strata: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    negative_proxy: bool


class RichGoldRecord(StrictContractModel):
    """Represent the complete evidence-preserving gold record for one sampled article."""

    sample_id: str = Field(min_length=1)
    article_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_link: str | None = None
    article_status: ArticleGoldStatus = "unreviewed"
    article_review_status: ArticleReviewStatus = Field(
        default="unreviewed",
        validation_alias=AliasChoices("article_review_status", "review_status"),
    )
    projects: list[GoldProject] = Field(default_factory=list)
    difficulty: DifficultyScore | None = None
    risk_tags: list[str] = Field(default_factory=list)
    dataset_split: str | None = None
    sampling_provenance: SamplingProvenance | None = None
    audit_metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def review_status(self) -> ArticleReviewStatus:
        """Expose the article review status under the concise review-store name."""
        return self.article_review_status

    def assert_publishable(self):
        """Raise when an article is unreviewed or a field still needs adjudication."""
        if self.article_review_status != "reviewed":
            raise ValueError("record is not publishable until the article is reviewed")
        if self.article_status == "unreviewed":
            raise ValueError("record is not publishable until article gold status is decided")
        if self.article_status == "valid" and not self.projects:
            raise ValueError("valid article requires at least one project")
        if self.article_status != "valid" and self.projects:
            raise ValueError("negative article status cannot carry projects")
        for project in self.projects:
            missing = sorted(set(FIELD_REGISTRY).difference(project.fields))
            extra = sorted(set(project.fields).difference(FIELD_REGISTRY))
            if missing or extra:
                raise ValueError(f"project field registry mismatch: missing={missing}; extra={extra}")
            for field_name, field in project.fields.items():
                if field.state in {"unreviewed", "needs_adjudication"}:
                    raise ValueError("record is not publishable with incomplete fields")
                validate_field_value(field_name, field.state, field.value)
            for entity in project.entities:
                validate_entity_role(entity.role)
                if not entity.evidence:
                    raise ValueError("record is not publishable without entity/role evidence")
