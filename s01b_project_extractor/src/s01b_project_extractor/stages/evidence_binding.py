"""提供项目级连续原文证据的确定性结构与归属校验。"""

import re
from typing import Any


_GENERIC_PROJECT_ANCHORS = {
    "项目",
    "该项目",
    "本项目",
    "工程",
    "该工程",
    "本工程",
    "上述项目",
    "这些项目",
    "项目组合",
    "一批项目",
    "project",
    "the project",
    "this project",
    "the projects",
    "these projects",
    "portfolio",
}

_GENERIC_PROJECT_TERMS_RE = re.compile(
    r"项目公司|项目|工程|电站|光伏|太阳能|风电|风力|水电|储能|新能源|能源|"
    r"project|company|plant|power|solar|wind|hydro|storage|energy",
    re.IGNORECASE,
)
_CAPACITY_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:GW|MW|GWh|MWh|kV)", re.IGNORECASE)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")


def is_exact_surface_quote(quote: str, title: str, content: str) -> bool:
    """判断引文是否完整连续地出现于标题或正文任一 surface。"""
    value = str(quote or "").strip()
    return bool(value) and (value in title or value in content)


def is_specific_project_anchor(anchor: str) -> bool:
    """拒绝仅由“该项目”等泛化指代构成的项目锚点。"""
    value = str(anchor or "").strip()
    return len(value) >= 2 and value.casefold() not in _GENERIC_PROJECT_ANCHORS


def is_anchor_for_target(anchor: str, target_project: str) -> bool:
    """判断原文项目锚点是否与当前目标项目共享专名、场址或容量。"""
    anchor_value = str(anchor or "").strip()
    target_value = str(target_project or "").strip()
    if not target_value:
        return True
    anchor_norm = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", anchor_value).casefold()
    target_norm = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", target_value).casefold()
    if anchor_norm and (anchor_norm in target_norm or target_norm in anchor_norm):
        return True
    anchor_caps = {re.sub(r"\s+", "", token).casefold() for token in _CAPACITY_TOKEN_RE.findall(anchor_value)}
    target_caps = {re.sub(r"\s+", "", token).casefold() for token in _CAPACITY_TOKEN_RE.findall(target_value)}
    if anchor_caps & target_caps:
        return True
    anchor_core = _GENERIC_PROJECT_TERMS_RE.sub("", anchor_norm)
    if len(anchor_core) >= 2 and anchor_core in target_norm:
        return True
    anchor_latin = {token.casefold() for token in _LATIN_TOKEN_RE.findall(anchor_value)}
    target_latin = {token.casefold() for token in _LATIN_TOKEN_RE.findall(target_value)}
    return bool(anchor_latin & target_latin)


def validate_bound_evidence(
    item: Any,
    title: str,
    content: str,
    *,
    target_project: str = "",
    required_mentions: tuple[str, ...] = (),
) -> tuple[bool, str]:
    """校验证据结构、连续引文、非泛化项目锚点及必须出现的实体名称。"""
    if not isinstance(item, dict):
        return False, "证据不是对象"
    quote = str(item.get("quote", "") or "").strip()
    anchor = str(item.get("project_anchor", "") or "").strip()
    if not is_exact_surface_quote(quote, title, content):
        return False, "引文不是标题或正文连续原文"
    if not is_specific_project_anchor(anchor) or anchor not in quote:
        return False, "引文缺少非泛化项目锚点"
    if not is_anchor_for_target(anchor, target_project):
        return False, "项目锚点与当前目标项目不匹配"
    for mention in required_mentions:
        value = str(mention or "").strip()
        if value and value not in quote:
            return False, f"引文缺少实体原文名: {value}"
    return True, ""
