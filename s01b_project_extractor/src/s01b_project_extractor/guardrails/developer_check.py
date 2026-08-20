"""Layer 0: 确定性业主守卫——拒绝无证据/候选名单，并按实体角色清理 developer。

背景：2026-07-04 验证抽样（supporting_docs/developer_sampling_verdicts_20260704.csv）证实
「重名-纯EPC」组 6/6 全错、「重名-设备商」组多数错（例外：新能源制造业厂主=设备商自建、
PPA 卖电方自持电站）。prompt 规则管增量质量，本守卫兜住 LLM 不遵守规则的确定性错误。
守卫保守：S4 角色标注本身可能错（抽样见 PPA 卖方被标设备供应商），故只剔高置信度组合，
投资类角色一票保留，融资方/一体化方案提供商等不剔。
"""
import re

# 与 project_extractor_tool._DEV_SEP_RE / developer_normalizer.DEV_SEP_RE 同一分隔符约定
_DEV_SEP_RE = re.compile(r"[;；、，,/]+")

# 互含匹配最短长度（防「东方」这类过短简称琐碎误杀）
_MIN_MATCH_LEN = 3

# 投资类角色：与 developer 重名属合法（入股≈业主）——一票保留
_INVEST_ROLES = {"股权投资方", "投融资+EPC", "BOT/BOOT"}
# 纯承包角色：重名且无投资信号 → developer 几乎必错（抽样 6/6）
_CONTRACTOR_ROLES = {"EPC总承包", "设计/施工服务方"}
# 设备供应商：重名几乎必错，但豁免新能源制造业（厂主=设备商本人投资建厂）
_EQUIPMENT_ROLE = "设备供应商"
_EQUIPMENT_EXEMPT_CATEGORY = "新能源制造业"

# 投标资格、入围或候选身份不等于最终开发权；若同一短证据还明确最终授标/持有则放行。
_CANDIDATE_ONLY_MARKERS = (
    "合格投标方", "合格投标人", "合格开发商", "入围开发商", "入围名单",
    "短名单", "候选人", "候选方", "资格预审", "预审通过", "参与竞标",
    "qualified bidder", "qualified developer", "shortlist", "prequalified",
    "eligible bidder", "candidate bidder",
)
_FINAL_AWARD_MARKERS = (
    "最终中标", "成功中标", "中标开发商", "正式授标", "授标给", "授予项目",
    "授予合同", "授予特许权", "获得开发权", "被选为中标方",
    "签署购电协议", "签订购电协议", "签署ppa", "签订ppa", "全资持有",
    "持有项目", "组成的联合体开发", "project is developed by",
    "awarded to", "signed the ppa", "wholly owned by",
)


def _split(value: str) -> list[str]:
    """按统一分隔符拆业主串（去空段）。"""
    s = str(value or "").strip()
    if not s:
        return []
    return [p.strip() for p in _DEV_SEP_RE.split(s) if p.strip()]


def _name_overlap(a: str, b: str) -> bool:
    """两公司名互含即视为重名（双向 substring，最短限 _MIN_MATCH_LEN）。"""
    a, b = (a or "").strip(), (b or "").strip()
    if len(a) < _MIN_MATCH_LEN or len(b) < _MIN_MATCH_LEN:
        return False
    return a in b or b in a


def _candidate_only_evidence(evidence: str) -> bool:
    """证据只表明投标资格/候选身份且无最终授标或持有信号时返回 True。"""
    normalized = str(evidence or "").strip().lower()
    return (
        any(marker in normalized for marker in _CANDIDATE_ONLY_MARKERS)
        and not any(marker in normalized for marker in _FINAL_AWARD_MARKERS)
    )


def clean_developer(
    developer: str,
    entities: list[dict],
    category: str,
    evidence: str | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """按证据与实体角色清理 developer；返回 (清洗后值, [(被剔部件, 理由)])。

    无部件被剔时原样返回（含原分隔符风格）；全部被剔返回空串。
    evidence=None 仅供旧调用兼容；生产入口显式传入 evidence，空证据即拒绝 developer。
    entities 形状与 row 组装处一致：Implementing Chinese Entity / Subsidiary Name /
    Type of involvement of Chinese Entities。
    """
    parts = _split(developer)
    if not parts:
        return str(developer or ""), []
    if evidence is not None and not str(evidence).strip():
        return "", [(part, "缺少 Project Developer Evidence") for part in parts]
    if evidence is not None and _candidate_only_evidence(evidence):
        return "", [(part, "证据仅表明入围/投标候选资格") for part in parts]
    if not entities:
        return str(developer or ""), []

    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for part in parts:
        roles = set()
        for e in entities:
            names = (e.get("Implementing Chinese Entity", ""), e.get("Subsidiary Name", ""))
            if any(_name_overlap(part, n) for n in names):
                roles.add(str(e.get("Type of involvement of Chinese Entities", "") or ""))
        if not roles or roles & _INVEST_ROLES:
            kept.append(part)
        elif _EQUIPMENT_ROLE in roles and category != _EQUIPMENT_EXEMPT_CATEGORY:
            dropped.append((part, f"同事件角色={_EQUIPMENT_ROLE}"))
        elif roles <= _CONTRACTOR_ROLES:
            dropped.append((part, f"同事件角色={'/'.join(sorted(roles))}"))
        else:
            kept.append(part)  # 融资方/一体化方案提供商/Unknown 等：无抽样证据，保守保留

    if not dropped:
        return str(developer or ""), []
    return "; ".join(kept), dropped
