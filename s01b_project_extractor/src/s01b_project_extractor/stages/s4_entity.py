"""S4 阶段：抽取某项目的中方实体并归一母子公司，校验 involvement_type 合法。

注意：prompt 中已移除层级表注入（S4 减法）；
实体名由 LLM 逐字照原文抄写，由 normalize_entity 纯查表归一。
"""
import logging
import re
import bri_enums as enums
from s01b_project_extractor.stages.evidence_binding import validate_bound_evidence
from s01b_project_extractor.stages.entity_normalize import resolve_entity

logger = logging.getLogger(__name__)

_EQUITY_ROLE_EVIDENCE_RE = re.compile(
    r"股权投资|持股|收购[^。；;]{0,30}股权|认购[^。；;]{0,20}股份|资本金出资|"
    r"equity investment|shareholding|acquir(?:e|ed|es|ing)[^.;]{0,40}(?:stake|shares)|"
    r"capital contribution",
    re.IGNORECASE,
)

_EPC_ROLE_EVIDENCE_RE = re.compile(
    r"EPC(?:总承包|合同|工程)?|工程总承包|设计采购施工|"
    r"engineering[,\s]+procurement[,\s]+and[,\s]+construction|EPC contract",
    re.IGNORECASE,
)

_ROLE_EVIDENCE_PATTERNS = {
    "股权投资方": _EQUITY_ROLE_EVIDENCE_RE,
    "EPC总承包": _EPC_ROLE_EVIDENCE_RE,
    "融资方": re.compile(
        r"贷款|信贷|授信|银团|银行财团|买方信贷|出口信贷|担保|赠款|资助|"
        r"\bloan\b|credit facility|bank consortium|guarantee|\bgrant(?:s)?\b",
        re.IGNORECASE,
    ),
    "设备供应商": re.compile(
        r"供应|供货|交付|设备合同|设备采购|supply|deliver|equipment contract",
        re.IGNORECASE,
    ),
    "设计/施工服务方": re.compile(
        r"设计|施工|安装|调试|工程咨询|业主工程师|construction|design|installation|commissioning",
        re.IGNORECASE,
    ),
    "BOT/BOOT": re.compile(r"\bBOT\b|\bBOOT\b|建设[—\-、/]运营[—\-、/]移交", re.IGNORECASE),
    "一体化方案提供商": re.compile(
        r"一体化方案|整体解决方案|系统解决方案|integrated solution|turnkey solution",
        re.IGNORECASE,
    ),
    "Unknown": re.compile(
        r"参与建设|参与开发|合作开发|联合开发|共同开发|中标|承接|负责|"
        r"participat|joint development|co-develop|awarded",
        re.IGNORECASE,
    ),
}


def _role_is_bound_in_clause(
    pattern: re.Pattern,
    quote: str,
    entity_name: str,
    project_anchor: str,
) -> bool:
    """要求企业原文名、项目锚点和角色动作出现在同一自然句或分号分句。"""
    clauses = re.split(r"[。；;\n]", quote)
    return any(
        entity_name in clause and project_anchor in clause and pattern.search(clause)
        for clause in clauses
    )


def enforce_entity_role_evidence(
    entities: list[dict],
    title: str,
    content: str,
    project_name: str = "",
) -> None:
    """逐企业校验目标项目证据，并按该企业已证实的角色纠偏参与方式。"""
    for entity in entities:
        raw_name = str(entity.get("Implementing Chinese Entity", "") or "").strip()
        requested = str(entity.get("Type of involvement of Chinese Entities", "") or "").strip()
        raw_evidence = entity.pop("Participation Evidence", []) or []
        if not isinstance(raw_evidence, list):
            logger.warning("Participation Evidence 不是列表: entity=%s", raw_name)
            raw_evidence = []

        supported_roles: set[str] = set()
        for item in raw_evidence:
            valid, reason = validate_bound_evidence(
                item,
                title,
                content,
                target_project=project_name,
                required_mentions=(raw_name,),
            )
            if not valid:
                logger.warning("参与证据未通过项目—企业绑定校验: entity=%s reason=%s", raw_name, reason)
                continue
            role = str(item.get("role", "") or "").strip()
            quote = str(item.get("quote", "") or "").strip()
            anchor = str(item.get("project_anchor", "") or "").strip()
            pattern = _ROLE_EVIDENCE_PATTERNS.get(role)
            if pattern is None or not _role_is_bound_in_clause(pattern, quote, raw_name, anchor):
                logger.warning("参与证据与角色不匹配: entity=%s role=%s", raw_name, role)
                continue
            supported_roles.add(role)

        if requested == "投融资+EPC":
            has_equity = "股权投资方" in supported_roles
            has_epc = "EPC总承包" in supported_roles
            if has_equity and has_epc:
                replacement = "投融资+EPC"
            elif has_epc:
                replacement = "EPC总承包"
            elif has_equity:
                replacement = "股权投资方"
            else:
                replacement = "Unknown"
        elif requested in supported_roles:
            replacement = requested
        elif len(supported_roles) == 1:
            replacement = next(iter(supported_roles))
        elif supported_roles == {"股权投资方", "EPC总承包"}:
            replacement = "投融资+EPC"
        else:
            replacement = "Unknown"

        entity["_participation_evidence_status"] = (
            "verified" if supported_roles else "missing_or_ambiguous"
        )
        if replacement != requested:
            logger.warning(
                "参与方式按项目—企业证据纠偏: entity=%s requested=%s final=%s roles=%s",
                raw_name,
                requested or "空",
                replacement,
                sorted(supported_roles),
            )
        entity["Type of involvement of Chinese Entities"] = replacement


def run_s4(title: str, content: str, project_name: str, category: str, *, llm,
           technology_tags: tuple[str, ...] = ()) -> list[dict]:
    """跑 S4 抽取并对每个实体做母子公司归一；返回 chinese_entities 列表。"""
    from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
    from s01b_project_extractor.knowledge import main_equipment
    prompt = build_s4_prompt(title, content, project_name, category, main_equipment(category, technology_tags))
    out = llm.complete(prompt, stage="s4", json_mode=True)
    ents = out.get("chinese_entities", []) if isinstance(out, dict) else []
    enforce_entity_role_evidence(ents, title, content, project_name)
    for e in ents:
        raw = e.get("Implementing Chinese Entity", "")
        resolution = resolve_entity(raw)
        e["Implementing Chinese Entity"] = resolution.implementing
        # 无条件用 parser 输出覆盖 LLM 的 Subsidiary Name：
        # LLM 不可信（易幻觉），不变量"subsidiary 非空⇔原文名"只能由 parser 保证。
        e["Subsidiary Name"] = resolution.subsidiary
        e["_entity_verification_status"] = resolution.verification_status
        e["_entity_matched_source"] = resolution.matched_source
    return ents


def validate_s4(entities: list[dict]) -> list[str]:
    """校验：每个实体 involvement_type 合法、Implementing Chinese Entity 非空。"""
    errors = []
    valid_inv = set(enums.valid_involvement())
    for i, e in enumerate(entities):
        inv = str(e.get("Type of involvement of Chinese Entities", "")).strip()
        if inv and inv not in valid_inv:
            errors.append(f"实体#{i} 参与方式不合法: {inv}")
        if not str(e.get("Implementing Chinese Entity", "")).strip():
            errors.append(f"实体#{i} Implementing Chinese Entity 为空")
        if e.get("_participation_evidence_status") != "verified":
            errors.append(f"实体#{i} 缺少目标项目—企业—角色绑定证据")
    return errors
