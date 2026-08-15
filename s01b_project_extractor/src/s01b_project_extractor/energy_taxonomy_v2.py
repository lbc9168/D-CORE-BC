"""能源构成事实、严格储能解析与 energy_taxonomy_v2.1 确定性分类的单一实现。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

import bri_enums as enums

POLICY_VERSION = "energy_taxonomy_v2.1"
GENERATION_SOURCES = tuple(enums.valid_generation_sources())
STORAGE_STATUSES = tuple(enums.valid_storage_statuses())

_STATUS_RANK = {status: enums.storage_status_priority(status) for status in STORAGE_STATUSES}
_SOURCE_RANK = {source: rank for rank, source in enumerate(GENERATION_SOURCES)}

_SOURCE_PATTERNS = {
    "光伏": re.compile(r"光伏|太阳能发电|photovoltaic|\bpv\b", re.I),
    "风电": re.compile(r"风电|风力发电|wind power|wind farm", re.I),
    "水电": re.compile(r"水电|水力发电|hydropower", re.I),
    "地热及余热": re.compile(r"地热|余热发电|geothermal|waste heat", re.I),
    "生物质": re.compile(r"生物质|垃圾发电|biomass|waste-to-energy", re.I),
    "核电": re.compile(r"核电|核能发电|nuclear power", re.I),
    "火电": re.compile(r"火电|煤电|燃气发电|柴油发电|thermal power|gas-fired|diesel generator", re.I),
}
_STORAGE = re.compile(
    r"储能|储电|(?<![a-z])bess(?![a-z])|energy storage|(?<![a-z])battery(?![a-z])",
    re.I,
)
_PLANNED = re.compile(r"预留|拟配储|计划配储|未来.{0,12}(?:配储|储能)|planned storage", re.I)
_OPTIONAL = re.compile(r"可选配|储能选项|optional storage|storage option", re.I)
_FACTORY_OUTPUT = re.compile(
    r"(?:电池|储能).{0,12}(?:工厂|产线|产能|年产)|(?:工厂|产线|产能|年产).{0,12}(?:gwh|吉瓦时)",
    re.I,
)
_EQUIPMENT_EXPORT = re.compile(r"(?:出口|供货|供应).{0,20}(?:battery|电池|储能设备)", re.I)
_ANNUAL_GENERATION = re.compile(r"年(?:发电量|上网电量)|annual generation", re.I)
_POWER = re.compile(r"(\d+(?:\.\d+)?)\s*(mw|兆瓦)(?!h|时)", re.I)
_ENERGY = re.compile(r"(\d+(?:\.\d+)?)\s*(mwh|gwh|兆瓦时|吉瓦时)", re.I)
_ENERGY_FACTOR = {"mwh": 1.0, "兆瓦时": 1.0, "gwh": 1000.0, "吉瓦时": 1000.0}


@dataclass(frozen=True)
class EnergyFacts:
    """表示单个事件抽取出的发电源、储能状态、容量和证据。"""

    generation_sources: tuple[str, ...] = ()
    storage_status: str = "not_mentioned"
    storage_power_mw_raw: Optional[str] = None
    storage_energy_mwh_raw: Optional[str] = None
    storage_power_mw: Optional[float] = None
    storage_energy_mwh: Optional[float] = None
    evidence: str = ""


@dataclass(frozen=True)
class EnergyClassification:
    """表示 v2 策略对一组能源事实给出的正式类别、技术标签和复核状态。"""

    proposed_category: Optional[str]
    technology_tags: tuple[str, ...]
    needs_review: bool
    review_reason: str = ""
    policy_version: str = POLICY_VERSION


def normalize_generation_sources(sources: Iterable[str]) -> tuple[str, ...]:
    """校验发电源并按稳定枚举顺序去重返回。"""
    values = {str(source).strip() for source in sources if str(source).strip()}
    invalid = values.difference(GENERATION_SOURCES)
    if invalid:
        raise ValueError(f"invalid generation source: {sorted(invalid)}")
    return tuple(sorted(values, key=_SOURCE_RANK.__getitem__))


def _validate_storage_status(storage_status: str) -> str:
    """校验并返回受控储能状态。"""
    status = str(storage_status or "").strip()
    if status not in STORAGE_STATUSES:
        raise ValueError(f"invalid storage status: {status}")
    return status


def _category_sources(sources: tuple[str, ...]) -> tuple[str, ...]:
    """把火电视为备用源：存在非火电发电源时，分类只使用非火电源。"""
    non_thermal = tuple(source for source in sources if source != "火电")
    return non_thermal or sources


def _technology_tags(
    category: Optional[str], effective_sources: tuple[str, ...], storage_status: str
) -> tuple[str, ...]:
    """在正式类别确定后仅生成四种受控条件标签，其他能源组合返回空标签。"""
    has_storage = storage_status == "confirmed"
    if category == "光伏" and effective_sources == ("光伏",) and has_storage:
        return ("光储",)
    if category == "风电" and effective_sources == ("风电",) and has_storage:
        return ("风储",)
    if category == "综合能源" and set(effective_sources) == {"光伏", "风电"}:
        return ("风光储" if has_storage else "风光",)
    return ()


def validate_technology_tags(category: str, tags: Iterable[str]) -> tuple[str, ...]:
    """校验标签受控值和父类约束，并按输入顺序去重返回。"""
    normalized = tuple(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip()))
    invalid = [
        tag for tag in normalized
        if tag not in enums.valid_technology_tags() or not enums.technology_tag_allowed(category, tag)
    ]
    if invalid:
        raise ValueError(f"invalid technology tags for {category}: {invalid}")
    return normalized


def classify_energy(
    generation_sources: Iterable[str], storage_status: str
) -> EnergyClassification:
    """依据发电源数量和储能状态推导正式类别，且从不把储能计为发电源。"""
    sources = normalize_generation_sources(generation_sources)
    status = _validate_storage_status(storage_status)
    category_sources = _category_sources(sources)
    if not category_sources:
        if status == "confirmed":
            return EnergyClassification("储能", (), False)
        return EnergyClassification(None, (), True, "missing_generation_source")
    if len(category_sources) == 1:
        category = category_sources[0]
    else:
        category = "综合能源"
    tags = validate_technology_tags(category, _technology_tags(category, category_sources, status))
    return EnergyClassification(category, tags, False)


def aggregate_energy_facts(events: Iterable[EnergyFacts]) -> EnergyFacts:
    """按发电源并集和储能状态优先级聚合多个事件事实。"""
    rows = list(events)
    sources = normalize_generation_sources(
        source for row in rows for source in row.generation_sources
    )
    statuses = [_validate_storage_status(row.storage_status) for row in rows]
    status = max(statuses, key=_STATUS_RANK.__getitem__) if statuses else "not_mentioned"
    evidence = "\n".join(dict.fromkeys(row.evidence for row in rows if row.evidence))
    same_status = [row for row in rows if row.storage_status == status]
    selected = max(
        same_status,
        key=lambda row: sum(value is not None for value in (
            row.storage_power_mw, row.storage_energy_mwh,
            row.storage_power_mw_raw, row.storage_energy_mwh_raw,
        )),
        default=EnergyFacts(),
    )
    return EnergyFacts(
        sources,
        status,
        selected.storage_power_mw_raw if status == "confirmed" else None,
        selected.storage_energy_mwh_raw if status == "confirmed" else None,
        selected.storage_power_mw if status == "confirmed" else None,
        selected.storage_energy_mwh if status == "confirmed" else None,
        evidence,
    )


def _storage_status(text: str) -> str:
    """根据储能语境区分 confirmed、计划、可选、含糊和未提及。"""
    if not _STORAGE.search(text):
        return "not_mentioned"
    if _ANNUAL_GENERATION.search(text) and not re.search(r"储能|bess|energy storage", text, re.I):
        return "not_mentioned"
    if _FACTORY_OUTPUT.search(text):
        return "not_mentioned"
    if _PLANNED.search(text):
        return "planned"
    if _OPTIONAL.search(text):
        return "optional"
    if _EQUIPMENT_EXPORT.search(text) or not re.search(r"储能|储电|bess|energy storage", text, re.I):
        return "unclear"
    return "confirmed"


def _nearest_measure(pattern: re.Pattern, text: str, anchor: int) -> Optional[re.Match]:
    """返回距离储能关键词最近的容量短语，避免误取同段发电装机。"""
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    following = [match for match in matches if anchor <= match.start() <= anchor + 24]
    if following:
        return min(following, key=lambda match: match.start())
    preceding = [match for match in matches if match.end() <= anchor]
    if preceding:
        return max(preceding, key=lambda match: match.end())
    return min(matches, key=lambda match: abs(match.start() - anchor))


def parse_storage_measurements(
    storage_power_raw: Optional[str], storage_energy_raw: Optional[str]
) -> tuple[Optional[float], Optional[float]]:
    """分别解析储能功率和能量原文，避免把发电装机或两种单位互相污染。"""
    power_match = _POWER.search(str(storage_power_raw or ""))
    energy_match = _ENERGY.search(str(storage_energy_raw or ""))
    power = float(power_match.group(1)) if power_match else None
    energy = None
    if energy_match:
        energy = float(energy_match.group(1)) * _ENERGY_FACTOR[energy_match.group(2).lower()]
    return power, energy


def extract_energy_facts(text: Optional[str]) -> EnergyFacts:
    """从历史文本确定性提取发电源和严格储能事实，供兼容回填使用。"""
    value = str(text or "").strip()
    sources = normalize_generation_sources(
        source for source, pattern in _SOURCE_PATTERNS.items() if pattern.search(value)
    )
    status = _storage_status(value)
    if status != "confirmed":
        return EnergyFacts(sources, status, evidence=value)

    storage_match = re.search(r"储能|储电|bess|energy storage", value, re.I)
    anchor = storage_match.start() if storage_match else 0
    power_match = _nearest_measure(_POWER, value, anchor)
    energy_match = _nearest_measure(_ENERGY, value, anchor)
    power_raw = re.sub(r"\s+", "", power_match.group(0)) if power_match else None
    energy_raw = re.sub(r"\s+", "", energy_match.group(0)) if energy_match else None
    power, energy = parse_storage_measurements(power_raw, energy_raw)
    return EnergyFacts(sources, status, power_raw, energy_raw, power, energy, value)


__all__ = [
    "POLICY_VERSION", "GENERATION_SOURCES", "STORAGE_STATUSES",
    "EnergyFacts", "EnergyClassification", "normalize_generation_sources",
    "classify_energy", "validate_technology_tags", "aggregate_energy_facts", "parse_storage_measurements",
    "extract_energy_facts",
]
