"""S4 阶段：抽取某项目的中方实体并归一母子公司，校验 involvement_type 合法。

注意：prompt 中已移除层级表注入（S4 减法）；
实体名由 LLM 逐字照原文抄写，由 normalize_entity 纯查表归一。
"""
import logging
import re
import bri_enums as enums
from s01b_project_extractor.stages.evidence_binding import (
    bound_action_clauses, is_exact_surface_quote, validate_bound_evidence, restore_quote_whitespace,
)
from s01b_project_extractor.stages.entity_normalize import resolve_entity
from s01b_project_extractor.stages.entity_response import parse_entity_response

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

# 参与事实的动作范围宽于角色枚举；“承建”本身不能证明 EPC 全范围。
_PARTICIPATION_RE = re.compile(
    "|".join(f"(?:{pattern.pattern})" for pattern in _ROLE_EVIDENCE_PATTERNS.values())
    + r"|承建|投资建设|签约|签署|签订|建设|开发|合作|参与|承包|分包|中标|"
    r"contracted|signed|built|build|develop|participat|construct", re.IGNORECASE,
)
_NEGATIVE_ACTION_RE = re.compile(r"并非|不是|未曾|未参与|未承建|没有参与|尚未|并未|(?:未|不|没有|不会|不再|并不)(?:安装|采用|使用|配备|搭载)|不参与|不负责|not\s+(?:the\s+)?(?:developer|owner|involved|using|installed)|did not|without", re.I)


def _bound_equipment_use(quote: str, name: str, anchor: str) -> bool:
    """Use of a named maker's equipment proves participation, not its contract role."""
    count = r"(?:[一二两三四五六七八九十百\d]+\s*(?:台|套|组)\s*)?"
    pattern = re.compile(
        r"(?:安装|采用|使用|配备|搭载)(?:了|的是|的)?\s*" + count + re.escape(name)
        + r"(?:的)?(?:[A-Za-z0-9 .\-]{0,50}\s*(?:系列)?)?"
        r"(?:单机容量\s*\d+(?:\.\d+)?\s*(?:MW|兆瓦))?"
        r"(?:风电机组|风力发电机组|风机|机组|主力机型|组件|逆变器|储能系统|储能解决方案|电池)", re.I,
    )
    return any(pattern.search(clause) and not _NEGATIVE_ACTION_RE.search(clause)
               for clause in bound_action_clauses(quote, name, anchor))


def _role_is_bound_in_clause(
    pattern: re.Pattern,
    quote: str,
    entity_name: str,
    project_anchor: str,
) -> bool:
    """角色动作只在已绑定当前企业和项目的分句内生效。"""
    clauses = bound_action_clauses(quote, entity_name, project_anchor)
    return any(
        pattern.search(clause) and not _NEGATIVE_ACTION_RE.search(clause)
        for clause in clauses
    )


def _enforce_legacy_entity_role_evidence(
    entities: list[dict],
    title: str,
    content: str,
    project_name: str = "",
) -> None:
    """逐企业校验目标项目证据，并按该企业已证实的角色纠偏参与方式。"""
    for entity in entities:
        raw_name = str(entity.get("Implementing Chinese Entity", "") or "").strip()
        requested = str(entity.get("Type of involvement of Chinese Entities", "") or "").strip()
        raw_evidence = entity.get("Participation Evidence", []) or []
        reasons: list[str] = []
        if not isinstance(raw_evidence, list):
            logger.warning("Participation Evidence 不是列表: entity=%s", raw_name)
            raw_evidence = []
            reasons.append("Participation Evidence 不是列表")

        supported_roles: set[str] = set()
        participation_verified = False
        for item in raw_evidence:
            if isinstance(item, dict) and isinstance(item.get("quote"), str):
                quote = item["quote"]
                if not is_exact_surface_quote(quote, title, content):
                    original = restore_quote_whitespace(quote, title, content)
                    if original is not None:
                        item["model_quote"] = quote
                        item["quote"] = original
                        item["quote_normalization"] = "unique_original_span_whitespace_only"
            valid, reason = validate_bound_evidence(
                item,
                title,
                content,
                target_project=project_name,
                required_mentions=(raw_name,),
            )
            if not valid:
                logger.warning("参与证据未通过项目—企业绑定校验: entity=%s reason=%s", raw_name, reason)
                reasons.append(reason)
                continue
            role = str(item.get("role", "") or "").strip()
            quote = str(item.get("quote", "") or "").strip()
            anchor = str(item.get("project_anchor", "") or "").strip()
            equipment_use = _bound_equipment_use(quote, raw_name, anchor)
            if not raw_name or not (equipment_use or _role_is_bound_in_clause(_PARTICIPATION_RE, quote, raw_name, anchor)):
                reasons.append("未找到绑定当前企业和项目的肯定参与动作")
                continue
            participation_verified = True
            if equipment_use:
                reasons.append("目标项目使用该企业设备；不据此推定供货合同、安装服务或开发商角色")
                continue
            pattern = _ROLE_EVIDENCE_PATTERNS.get(role)
            if pattern is None or not _role_is_bound_in_clause(pattern, quote, raw_name, anchor):
                logger.warning("参与证据与角色不匹配: entity=%s role=%s", raw_name, role)
                reasons.append(f"参与已确认，具体角色证据不足: {role or '空'}")
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
            "verified" if participation_verified else "missing_or_ambiguous"
        )
        entity["_participation_evidence_reasons"] = reasons or ([] if participation_verified else ["缺少参与证据"])
        if replacement != requested:
            logger.warning(
                "参与方式按项目—企业证据纠偏: entity=%s requested=%s final=%s roles=%s",
                raw_name,
                requested or "空",
                replacement,
                sorted(supported_roles),
            )
        entity["Type of involvement of Chinese Entities"] = replacement


def _normalize_legacy_entities(entities: list[dict], title: str, content: str) -> None:
    """名称规范化与原文身份验证独立；登记册明确外企不能被模型文本覆盖。"""
    for entity in entities:
        raw = str(entity.get("Implementing Chinese Entity", "") or "").strip()
        resolution = resolve_entity(raw)
        entity["_entity_raw_name"] = raw
        entity["Implementing Chinese Entity"] = resolution.implementing
        entity["Subsidiary Name"] = resolution.subsidiary
        entity["_entity_verification_status"] = resolution.verification_status
        entity["_entity_matched_source"] = resolution.matched_source
        for key, attribute in (("_entity_id", "entity_id"), ("_entity_name", "entity_name"),
                               ("_group_entity_id", "group_id"), ("_parent_entity_id", "parent_entity_id")):
            entity[key] = getattr(resolution, attribute, None)
        evidence = entity.get("Chinese Identity Evidence", "") or ""
        items = evidence if isinstance(evidence, list) else [evidence]
        identity_verified = False
        reasons = []
        for item in items:
            quote = item.get("quote", "") if isinstance(item, dict) else item
            if not isinstance(quote, str) or not is_exact_surface_quote(quote, title, content):
                reasons.append("身份证据不是连续原文")
                continue
            name = re.escape(raw)
            patterns = (
                rf"(?:中国(?:的)?(?:企业|公司|央企|国企|民营企业)|中企|中国央企|中国国企)[：:\s]*{name}",
                rf"{name}[（(，,\s]*(?:是|为|系|作为|是一家|为一家|系一家)?[\s]*(?:中国(?:的)?(?:企业|公司|央企|国企|民营企业)|中企)",
                rf"\bChinese\s+(?:company|firm|enterprise|state-owned\s+company)\s+{name}(?![A-Za-z])",
                rf"{name}\s+(?:is|was),?\s+(?:a\s+)?Chinese\s+(?:company|firm|enterprise)",
            )
            if raw and any(re.search(pattern, quote, re.I) for pattern in patterns) and not re.search(
                r"并非|不是|非中国|not\s+(?:a\s+)?Chinese", quote, re.I
            ):
                identity_verified = True
            else:
                reasons.append("原文未明确将当前具名企业认定为中企")
        if resolution.verification_status == "verified_non_chinese":
            identity_verified = False
            reasons.append("登记册已确认非中企，原文冲突需人工复核")
        entity["_identity_evidence_status"] = "verified" if identity_verified else "missing_or_ambiguous"
        entity["_identity_evidence_reasons"] = reasons


SEMANTIC_PARTICIPATION_VERSION = "2026-09-23.4-semantic-participation"


class EntityResults(list):
    """List-compatible standalone S4 result retaining even an empty project's ruling."""
    def __init__(self, entities, project_assessment=None):
        super().__init__(entities)
        self.project_assessment = project_assessment


def enforce_entity_role_evidence(entities: list[dict], title: str, content: str,
                                 project_name: str = "") -> None:
    """Use explicit full-context judgments; quote layout is audit information only.

    Legacy responses cannot acquire an affirmative semantic judgment by default.
    Their previously supported evidence remains readable; otherwise re-extraction
    is required. New responses never pass through the legacy clause/action gate.
    """
    for entity in entities:
        assessment = entity.get("Participation Assessment")
        entity["_participation_policy_version"] = SEMANTIC_PARTICIPATION_VERSION
        if assessment is None:
            _enforce_legacy_entity_role_evidence([entity], title, content, project_name)
            entity["_assessment_format"] = "legacy"
            entity["_requires_reextraction"] = entity.get("_participation_evidence_status") != "verified"
            continue
        entity["_assessment_format"] = "semantic"
        reasons = []
        quotes = assessment["evidence_quotes"]
        # Preserve original snippets and location diagnostics, without turning
        # harmless punctuation, discontinuity or pronouns into a rejection rule.
        entity["_evidence_locations"] = [
            {"quote": q, "located": is_exact_surface_quote(q, title, content)} for q in quotes
        ]
        if any(not item["located"] for item in entity["_evidence_locations"]):
            reasons.append("部分依据片段未逐字定位；保留模型片段及语义说明供审计")
        participated = assessment["participates"]
        supported = bool(assessment["rationale"].strip() and any(q.strip() for q in quotes))
        entity["_participation_evidence_status"] = (
            "verified" if participated is True and supported else
            "not_participating" if participated is False else "missing_or_ambiguous"
        )
        if not supported:
            reasons.append("缺少参与判断说明或依据，需重提取或复核")
        if participated is not True:
            reasons.append(assessment["rationale"] or "目标项目参与未确定")
        role = assessment["role"].strip()
        if role not in enums.valid_involvement() or not assessment["role_basis"].strip():
            role = "Unknown"
        entity["Type of involvement of Chinese Entities"] = role
        entity["_participation_evidence_reasons"] = reasons


def normalize_and_verify_entities(entities: list[dict], title: str, content: str) -> None:
    """Standardization may enrich names/IDs, but cannot veto reasonable Chinese participation."""
    for entity in entities:
        assessment = entity.get("Participation Assessment")
        if assessment is None:
            _normalize_legacy_entities([entity], title, content)
            if entity.get("_entity_verification_status") == "unmatched_needs_review" and entity.get("_identity_evidence_status") != "verified":
                entity["_requires_reextraction"] = True
            continue
        raw = entity["Implementing Chinese Entity"].strip()
        resolution = resolve_entity(raw)
        entity["_entity_raw_name"] = raw
        entity["Implementing Chinese Entity"] = resolution.implementing
        entity["Subsidiary Name"] = resolution.subsidiary
        entity["_entity_matched_source"] = resolution.matched_source
        entity["_identity_judgment_source"] = assessment["identity_basis"]
        for key, attr in (("_entity_id", "entity_id"), ("_entity_name", "entity_name"),
                          ("_group_entity_id", "group_id"), ("_parent_entity_id", "parent_entity_id")):
            entity[key] = getattr(resolution, attr)
        reasons = []
        status = resolution.verification_status
        chinese = assessment["is_chinese"]
        if status == "verified_non_chinese":
            reasons.append("登记册明确外企；模型相反结论不能覆盖")
            identity_status = "conflict" if chinese is True else "not_chinese"
        elif chinese is False:
            # A registry match can be a foreign namesake in this article.
            status, identity_status = "verified_non_chinese", "model_non_chinese"
        elif chinese is None or assessment["identity_basis"] == "uncertain":
            status, identity_status = "unmatched_needs_review", "missing_or_ambiguous"
            reasons.append("全文语义中的中企身份存在实际歧义")
        elif not assessment["rationale"].strip():
            status, identity_status = "unmatched_needs_review", "missing_or_ambiguous"
            reasons.append("中企判断缺少依据说明")
        else:
            identity_status = "model_supported"
            if status != "verified_chinese":
                status = "model_supported_chinese"
            if not resolution.entity_id:
                entity["_identity_standardization_pending"] = True
        entity["_entity_verification_status"] = status
        entity["_identity_evidence_status"] = identity_status
        entity["_identity_evidence_reasons"] = reasons


def run_s4(title: str, content: str, project_name: str, category: str, *, llm,
           technology_tags: tuple[str, ...] = (), retry_feedback: list[str] | None = None,
           project_context: dict | None = None) -> list[dict]:
    """跑 S4 抽取并对每个实体做母子公司归一；返回 chinese_entities 列表。"""
    from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
    from s01b_project_extractor.knowledge import main_equipment
    prompt = build_s4_prompt(title, content, project_name, category, main_equipment(category, technology_tags), project_context=project_context)
    if retry_feedback:
        prompt += "\n上次输出校验失败，请对照原文纠正；不得虚构引文或静默删除候选：\n" + "\n".join(retry_feedback)
    out = parse_entity_response(llm.complete(prompt, stage="s4", json_mode=True), stage="s4")
    from s01b_project_extractor.stages.s3_finance import bind_subcontract_evidence
    bind_subcontract_evidence(out)
    ents = out["chinese_entities"]
    enforce_entity_role_evidence(ents, title, content, project_name)
    normalize_and_verify_entities(ents, title, content)
    from s01b_project_extractor.stages.s3_finance import enforce_amount_evidence
    for entity in ents:
        enforce_amount_evidence(entity, title, content, entity=True)
        if out.get("Project Assessment") is not None:
            entity["_project_assessment"] = out["Project Assessment"]
    return EntityResults(ents, out.get("Project Assessment"))


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
        if e.get("_assessment_format") != "semantic" and e.get("_participation_evidence_status") != "verified":
            errors.append(f"实体#{i} {e.get('_entity_raw_name', '')} 缺少目标项目—企业参与证据: {e.get('_participation_evidence_reasons', [])}")
    return errors
