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
_CAPACITY_TOKEN_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:GWh|MWh|GW|MW|kW|kV|万千瓦|千瓦|兆瓦|吉瓦|千伏)(?:时)?",
    re.IGNORECASE,
)
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")


def is_exact_surface_quote(quote: str, title: str, content: str) -> bool:
    """判断引文是否完整连续地出现于标题或正文任一 surface。"""
    value = str(quote or "").strip()
    return bool(value) and (value in title or value in content)


def restore_quote_whitespace(quote: str, title: str, content: str) -> str | None:
    """Recover a unique continuous original span when only whitespace was changed.

    Never drops punctuation, words, or intervening sentences. The caller retains the
    model quotation alongside the restored original so the normalization is auditable.
    """
    needle = re.sub(r"\s", "", quote)
    if not needle:
        return None
    matches = []
    for surface in (title, content):
        positions = [i for i, char in enumerate(surface) if not char.isspace()]
        compact = "".join(surface[i] for i in positions)
        start = compact.find(needle)
        if start >= 0:
            if compact.find(needle, start + 1) >= 0:
                return None
            matches.append(surface[positions[start]:positions[start + len(needle) - 1] + 1])
    return matches[0] if len(matches) == 1 else None


def is_specific_project_anchor(anchor: str) -> bool:
    """拒绝仅由“该项目”等泛化指代构成的项目锚点。"""
    value = str(anchor or "").strip()
    return len(value) >= 2 and value.casefold() not in _GENERIC_PROJECT_ANCHORS


def is_anchor_for_target(anchor: str, target_project: str) -> bool:
    """判断原文项目锚点是否与当前目标项目共享专名或场址；容量不能单独绑定。"""
    anchor_value = str(anchor or "").strip()
    target_value = str(target_project or "").strip()
    if not target_value:
        return True
    anchor_norm = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", anchor_value).casefold()
    target_norm = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", target_value).casefold()
    # 必须先拒绝纯容量/技术泛称，再检查子串，否则“100MW光伏项目”
    # 会同时匹配布哈拉和纳沃伊的100MW项目。
    anchor_core = _GENERIC_PROJECT_TERMS_RE.sub("", _CAPACITY_TOKEN_RE.sub("", anchor_norm))
    if not re.search(r"[a-z\u4e00-\u9fff]", anchor_core, re.I):
        return False
    if anchor_norm and (anchor_norm in target_norm or target_norm in anchor_norm):
        return True
    # 同容量的不同电站不能互相提供证据；容量仅随专名一起匹配。
    if len(anchor_core) >= 2 and anchor_core in target_norm:
        return True
    anchor_latin = {token.casefold() for token in _LATIN_TOKEN_RE.findall(_CAPACITY_TOKEN_RE.sub("", anchor_value))
                    if not _GENERIC_PROJECT_TERMS_RE.fullmatch(token)}
    target_latin = {token.casefold() for token in _LATIN_TOKEN_RE.findall(_CAPACITY_TOKEN_RE.sub("", target_value))
                    if not _GENERIC_PROJECT_TERMS_RE.fullmatch(token)}
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


def bound_action_clauses(quote: str, entity_name: str, project_anchor: str) -> list[str]:
    """返回同时绑定主体和项目的短分句；只接受明确回指的相邻句。

    不把整段新闻作为角色作用域。跨句仅支持“项目介绍。企业…该项目…”
    的显式项目回指，不解析可能有多个先行词的“该公司”，不跨分号列表。
    """
    sentences = [s.strip() for s in re.split(r"[。；;\n]|(?<=[a-z0-9])[.]\s+", quote) if s.strip()]
    result: list[str] = []
    # Equipment reports often put the unique project introduction in the preceding
    # paragraph. Only the first, explicit site-reference sentence may inherit it.
    paragraphs = [p.strip() for p in quote.split("\n") if p.strip()]
    for previous, current in zip(paragraphs, paragraphs[1:]):
        current = re.sub(r"^根据(?:协议|合同)(?:规划|安排)?[，,]\s*", "", current)
        first = re.split(r"[。；;，,]", current, maxsplit=1)[0]
        remainder = previous.replace(project_anchor, "")
        remainder = re.sub(r"该项目|本项目|项目(?:最大的亮点|主要特点|核心价值)", "", remainder)
        if (project_anchor in previous and entity_name in first
                and re.match(r"(?:该项目|本项目|项目现场)(?:现场)?(?:将)?(?:安装|采用|使用|配备)", first)
                and ("将" not in first or re.search(r"协议|合同", quote))
                and not re.search(r"另|其他项目|项目|电站|\bproject\b|\bplant\b", remainder, re.I)):
            result.append(first)
    for index, sentence in enumerate(sentences):
        if entity_name in sentence and project_anchor in sentence:
            # 逗号后切换主体，不能沿用逗号前另一企业的动作。
            pieces = re.split(r"[，,]|并(?=[^，。；;]*(?:项目|电站))|而|同时", sentence)
            for left, right in zip(pieces, pieces[1:]):
                # A project-subject clause followed directly by equipment use keeps
                # that project as subject, provided neither clause switches projects.
                if (project_anchor in left and entity_name in right
                        and re.match(r"\s*(?:安装|采用|使用|配备|搭载)", right)
                        and not re.search(r"项目|电站|另一|其他", left.replace(project_anchor, ""))
                        and not re.search(r"项目|电站|另一|其他", right)):
                    result.append(right)
                # 新闻常用同位语：由A承建的…典范工程，<具体项目名>…。
                # 只继承左侧明确主体动作；不把右侧其他企业/融资动作借给A。
                descriptive = re.search(
                    r"由" + re.escape(entity_name)
                    + r"(?:承建|承包|开发|建设)的[^，。；;项目电站]{0,24}(?:典范|示范|重点)工程$",
                    left.strip(),
                )
                prefix = right.split(project_anchor, 1)[0] if project_anchor in right else None
                if descriptive and prefix is not None and len(prefix.strip()) <= 12 and not re.search(
                    r"另一|此外|其中|公司|项目|电站|承建|参与|而", prefix,
                ):
                    result.append(left + "，" + prefix + project_anchor)
            for piece in pieces:
                if entity_name in piece and project_anchor in piece:
                    result.append(piece)
                elif entity_name in piece and re.search(r"该项目|本项目|该电站|the project|this project", piece, re.I) and not re.search(
                    r"项目|电站|\bproject\b|\bplant\b",
                    re.sub(r"该项目|本项目|该电站|the project|this project", "", sentence.replace(project_anchor, ""), flags=re.I), re.I,
                ):
                    result.append(piece)
            continue
        if index == 0:
            continue
        previous = sentences[index - 1]
        gap_start = quote.find(previous) + len(previous)
        gap_end = quote.find(sentence, gap_start)
        if re.search(r"[；;]", quote[gap_start:gap_end]):
            continue
        if re.search(r"项目|电站|\bproject\b|\bplant\b", previous.replace(project_anchor, "", 1), re.I):
            continue
        if project_anchor in previous and entity_name in sentence:
            # 下句必须确实回指项目，且不得同时引入另一个具名项目。
            for piece in re.split(r"[，,]", sentence):
                reference = r"该项目|本项目|该电站|项目现场|the project|this project"
                if (entity_name in piece and re.search(reference, piece, re.I)
                        and not re.search(r"项目|电站|\bproject\b|\bplant\b",
                                          re.sub(reference, "", piece, flags=re.I), re.I)):
                    result.append(piece)
    return result
