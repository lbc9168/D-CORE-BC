"""能源构成事实、严格储能解析与版本化影子分类策略的单一实现。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

POLICY_VERSION = "energy_taxonomy_v1"
GENERATION_SOURCES = (
    "光伏",
    "风电",
    "水电",
    "地热及余热",
    "生物质",
    "核电",
    "火电",
)
STORAGE_STATUSES = (
    "confirmed",
    "planned",
    "optional",
    "unclear",
    "not_mentioned",
)

_STATUS_RANK = {status: rank for rank, status in enumerate(reversed(STORAGE_STATUSES))}
_SOURCE_RANK = {source: rank for rank, source in enumerate(GENERATION_SOURCES)}
_TAG_ORDER = ("风电", "光伏", "水电", "地热及余热", "生物质", "核电", "火电")
_TAG_SHORT = {
    "光伏": "光",
    "风电": "风",
    "水电": "水",
    "地热及余热": "地热",
    "生物质": "生物质",
    "核电": "核",
    "火电": "火",
}

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
    """表示影子分类策略对一组能源事实给出的类别、标签和复核状态。"""

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


def _confirmed_tags(sources: tuple[str, ...]) -> tuple[str, ...]:
    """为 confirmed 储能事实生成稳定的组合标签和通用标签。"""
    if not sources:
        return ("独立储能", "含储能")
    ordered = [source for source in _TAG_ORDER if source in sources]
    composite = "".join(_TAG_SHORT[source] for source in ordered) + "储"
    return (composite, "含储能")


def classify_energy(
    generation_sources: Iterable[str], storage_status: str
) -> EnergyClassification:
    """依据发电源数量和储能状态推导影子类别，且从不把储能计为发电源。"""
    sources = normalize_generation_sources(generation_sources)
    status = _validate_storage_status(storage_status)
    tags = _confirmed_tags(sources) if status == "confirmed" else ()
    if not sources:
        if status == "confirmed":
            return EnergyClassification("储能", tags, False)
        return EnergyClassification(None, tags, True, "missing_generation_source")
    if len(sources) == 1:
        return EnergyClassification(sources[0], tags, False)
    return EnergyClassification("综合能源", tags, False)


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
        selected.storage_power_mw_raw,
        selected.storage_energy_mwh_raw,
        selected.storage_power_mw,
        selected.storage_energy_mwh,
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


def extract_energy_facts(text: Optional[str]) -> EnergyFacts:
    """从历史文本确定性提取发电源和严格储能事实，供影子回填使用。"""
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
    energy_mwh = None
    if energy_match:
        energy_mwh = float(energy_match.group(1)) * _ENERGY_FACTOR[energy_match.group(2).lower()]
    return EnergyFacts(
        sources,
        status,
        power_raw,
        energy_raw,
        float(power_match.group(1)) if power_match else None,
        energy_mwh,
        value,
    )
