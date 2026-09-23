"""Conservative source-bound capacity correction; never infer battery size from turbines."""
import re
import json
import math

from s01b_project_extractor.capacity_parser import parse_capacity_mw
from s01b_project_extractor.energy_taxonomy_v2 import parse_storage_measurements
from s01b_project_extractor.stages.evidence_binding import validate_bound_evidence

CAPACITY_EVIDENCE_VERSION = "2026-09-23.6-nullable-rationale"
_MEASURE = r"\d+(?:\.\d+)?\s*(?:MWp|MW(?!h)|兆瓦(?!时)|kW(?!h)|千瓦(?!时)|GW(?!h))"
_UNIT = re.compile(r"(?P<count>\d+|[一二两三四五六七八九十]+)台[^。；;，,\n]{0,24}?(?:单机(?:容量)?|每台(?:容量)?|每台额定功率)\s*(?P<power>" + _MEASURE + r")\s*(?:的)?(?:风电)?机组", re.I)
_TOTAL = re.compile(r"(?:整体规划装机规模|总装机(?:容量)?|总容量|装机总容量)\s*(?:为|达|约|是|达到)?\s*(?P<power>" + _MEASURE + r")", re.I)
_PARTIAL = re.compile(r"其中|部分|首批|首台|新增|增设|扩建|尚未|未安装|未采用|计划安装|拟安装|仅安装|样机|测试机组")
_STORAGE = re.compile(r"储能|储电|电池|BESS|battery|energy storage", re.I)
_NEGATIVE = re.compile(r"未披露|未公布|不详|未配置|不含|不包括|没有|尚未|拟|计划|可选|not\b|without\b", re.I)


def apply_capacity_evidence(ci: dict, title: str, content: str) -> None:
    """Validate dimensions/arithmetic after explicit semantic project attribution.

    New semantic scope is not inferred from missing legacy fields. Missing or bad
    optional capacity is cleared locally, never used to reject Chinese participation.
    """
    evidence = ci.get("Capacity Evidence")
    context = ci.get("Project Context")
    storage_text = str(ci.get("Energy Composition Evidence", "") or "")
    # S2's explicit storage status and source evidence already describe the target
    # storage asset. Pure storage has no generation Capacity Evidence by design.
    # Use that separate field, recording its provenance rather than inventing a
    # missing model Capacity Evidence object in the saved response.
    storage_only = not evidence and isinstance(context, dict) and ci.get("Category") == "储能" and bool(storage_text)
    if storage_only:
        evidence = {"scope": "project", "rationale": context.get("context", ""), "quote": "", "evidence_quotes": []}
        ci["capacity_check_source"] = "explicit_target_storage_status_and_energy_evidence"
    if not isinstance(evidence, dict) or "scope" not in evidence or ci.get("Category") in {"输变电建设", "新能源制造业"}:
        _apply_legacy_capacity_evidence(ci, title, content)
    else:
        from s01b_project_extractor.stages.entity_response import EntityResponseError
        rationale = evidence.get("rationale")
        # A null explanation for an unknown optional generation measurement is
        # absence of evidence, not a malformed article or affirmative judgement.
        if rationale is None:
            rationale = ""
        if evidence.get("scope") not in {"project", "unit", "portfolio", "unknown"} or not isinstance(rationale, str):
            raise EntityResponseError("s2: invalid semantic Capacity Evidence", ci)
        quotes = evidence.get("evidence_quotes", [])
        if quotes is None:
            quotes = []
        if not isinstance(quotes, list) or any(not isinstance(q, str) for q in quotes):
            raise EntityResponseError("s2: capacity evidence_quotes must be strings", ci)
        quote = evidence.get("quote", "")
        if quote is None:
            quote = ""
        if not isinstance(quote, str):
            raise EntityResponseError("s2: capacity quote must be string", ci)
        text = "\n".join([quote, *quotes])
        ci["capacity_evidence_version"] = CAPACITY_EVIDENCE_VERSION
        ci["capacity_evidence_errors"] = []
        project_scope = evidence["scope"] == "project" and bool(rationale.strip())
        dimensions = (
            ("Capacity (MW)", "capacity_mw_parsed", "capacity_mw_raw_phrase", "capacity_mw", 0),
            ("Storage Power", "storage_power_mw_parsed", "storage_power_mw_raw_phrase", "storage_power_mw", 0),
            ("Storage Energy", "storage_energy_mwh_parsed", "storage_energy_mwh_raw_phrase", "storage_energy_mwh", 1),
        )
        for field, parsed, raw_key, db_field, dimension in dimensions:
            field_text = text + "\n" + storage_text if field.startswith("Storage") else text
            raw = str(ci.get(field, "") or "")
            if not raw:
                continue
            if field == "Capacity (MW)" and ci.get("Category") == "储能":
                ci[parsed] = None
                ci[field] = ci[raw_key] = ""
                continue
            desired = parse_capacity_mw(raw)[0] if field == "Capacity (MW)" else parse_storage_measurements(raw, raw)[dimension]
            pattern = (r"\d+(?:\.\d+)?\s*(?:MWp|MW(?!h)|兆瓦(?!时)|GW(?!h)|吉瓦(?!时)|万千瓦(?!时)|kW(?!h)|千瓦(?!时))" if dimension == 0
                       else r"\d+(?:\.\d+)?\s*(?:MWh|GWh|兆瓦时|吉瓦时|万千瓦时|千瓦时|kWh)")
            phrases = re.findall(pattern, field_text, re.I)
            values = {parse_capacity_mw(p)[0] if dimension == 0 else parse_storage_measurements(p, p)[1] for p in phrases}
            source_values = {parse_capacity_mw(p)[0] if dimension == 0 else parse_storage_measurements(p, p)[1]
                             for p in re.findall(pattern, title + "\n" + content, re.I)}
            field_scope = project_scope or (field.startswith("Storage") and ci.get("Category") == "储能"
                and isinstance(context, dict) and not quote and not quotes and evidence["scope"] == "unknown")
            valid = field_scope and desired is not None and desired in values and desired in source_values
            if field.startswith("Storage"):
                valid = valid and ci.get("storage_status_normalized") == "confirmed" and bool(_STORAGE.search(field_text))
            else:
                totals = {parse_capacity_mw(m.group("power"))[0] for m in _TOTAL.finditer(text)}
                if len(totals) == 1 and project_scope:
                    desired = next(iter(totals))
                    ci[field] = ci[raw_key] = next(m.group(0) for m in _TOTAL.finditer(text))
                    valid = desired in source_values
                elif re.search(r"燃气轮机|燃机", text) and re.search(r"蒸汽轮机|汽轮机", text) and not totals:
                    valid = False
                elif re.search(r"单机|每台", text) and not totals:
                    units = list(_UNIT.finditer(text))
                    if len(units) == 1 and project_scope and not _PARTIAL.search(text) and ci.get("Generation Sources") == ["风电"]:
                        unit = units[0]
                        desired = _count(unit.group("count")) * parse_capacity_mw(unit.group("power"))[0]
                        ci["capacity_details_note"] = f"capacity_source=unit_count_product; {_count(unit.group('count'))} × {unit.group('power')} = {desired:g} MW"
                        ci[field] = ci[raw_key] = unit.group(0)
                        valid = True
                    else:
                        valid = False
            if valid:
                ci[parsed] = desired
            else:
                ci.setdefault("capacity_rejected_values", {})[db_field] = {"raw": raw, "value": ci.get(parsed), "evidence": evidence}
                ci[parsed] = None
                ci[field] = ci[raw_key] = ""
                ci.setdefault("withheld_capacity_fields", []).append(db_field)
                prefix = field if field.startswith("Storage") else "Capacity Evidence"
                ci["capacity_evidence_errors"].append(prefix + ": quantity scope or dimension unsupported; cleared optional value")
    # Explicit null/withheld metadata prevents writer/backfill from resurrecting a
    # rejected model phrase. Raw evidence lives separately from accepted values.
    errors = ci.get("capacity_evidence_errors", []) or []
    mapping = {"capacity_mw": ("capacity_mw_parsed", "Capacity (MW)", "capacity_mw_raw_phrase"),
               "storage_power_mw": ("storage_power_mw_parsed", "Storage Power", "storage_power_mw_raw_phrase"),
               "storage_energy_mwh": ("storage_energy_mwh_parsed", "Storage Energy", "storage_energy_mwh_raw_phrase"),
               "production_capacity_value": ("production_capacity_value_parsed", "MF Production Capacity Evidence", "production_capacity_raw_phrase")}
    for field, (parsed, source, raw_key) in mapping.items():
        if errors and parsed in ci and ci[parsed] is None and ci.get(source):
            raw = ci[source]
            ci.setdefault("capacity_rejected_values", {}).setdefault(field, {"raw": raw, "errors": errors})
            ci.setdefault("withheld_capacity_fields", []).append(field)
            ci[source] = ci[raw_key] = ""
    ci["withheld_capacity_fields"] = sorted(set(ci.get("withheld_capacity_fields", [])))
    _clean_rejected_capacity_names(ci)


def _clean_rejected_capacity_names(ci: dict) -> None:
    """Strip only rejected quantities, keeping immutable original display names for audit.

    Semantic checks have already cleared raw fields, so the rejection ledger is
    authoritative here. Compare parsed quantities with dimensions, not substrings:
    15.99MW must not erase 115.99MW or an accepted 15.99MWh storage quantity.
    """
    rejected = ci.get("capacity_rejected_values", {})
    quantities = {"power": set(), "energy": set()}
    for field, item in rejected.items():
        if field not in {"capacity_mw", "storage_power_mw", "storage_energy_mwh"} or not isinstance(item, dict):
            continue
        dimension = "energy" if field == "storage_energy_mwh" else "power"
        raw = str(item.get("raw", "") or "")
        value = (parse_storage_measurements(raw, raw)[1] if dimension == "energy"
                 else parse_capacity_mw(raw)[0])
        if value is None:
            value = item.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            quantities[dimension].add(float(value))
    if not any(quantities.values()):
        return
    pattern = re.compile(
        r"(?<![\d.])\d+(?:\.\d+)?\s*(?:MWh|GWh|kWh|兆瓦时|吉瓦时|万千瓦时|千瓦时|MWp|MW|GW|kW|兆瓦|吉瓦|万千瓦|千瓦)(?![A-Za-z])", re.I)
    def replace(match):
        phrase = match.group(0)
        energy = bool(re.search(r"Wh|瓦时", phrase, re.I))
        value = parse_storage_measurements(phrase, phrase)[1] if energy else parse_capacity_mw(phrase)[0]
        values = quantities["energy" if energy else "power"]
        return "" if value is not None and any(math.isclose(value, n, rel_tol=1e-10, abs_tol=1e-10) for n in values) else phrase
    for name_key in ("Project Name", "Project Name (EN)"):
        name = str(ci.get(name_key, "") or "")
        clean = pattern.sub(replace, name)
        if clean != name:
            clean = re.sub(r"[（(]\s*[)）]", "", clean)
            clean = re.sub(r"\s{2,}", " ", clean).strip(" /／-")
            ci.setdefault("capacity_original_names", {}).setdefault(name_key, name)
            ci.setdefault("capacity_clean_names", {})[name_key] = clean


def _count(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {c: n for n, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value, 0)


def _anchors(ci: dict) -> list[str]:
    # Retain the actual project identifier; country/capacity/technology alone is insufficient.
    value = str(ci.get("Project Name", "") or "")
    for key in ("Country", "Location(State)"):
        token = str(ci.get(key, "") or "")
        if token:
            value = value.replace(token, "")
    value = re.sub(r"\d+(?:\.\d+)?\s*(?:MWh|GWh|kWh|兆瓦时|吉瓦时|千瓦时|MWp|MW|GW|kW|兆瓦|吉瓦|千瓦)", "", value, flags=re.I)
    value = value.replace("/", "").replace("／", "")
    value = re.sub(r"风储|风电场|风电|风力发电|光伏|太阳能|水电|独立储能|储能|项目|电站|工程|\([^)]*\)|（[^）]*）", " ", value)
    values = [part.strip() for part in re.split(r"[，,;；]", value) if len(part.strip()) >= 2]
    return values


def _has_other_project(sentence: str, anchor: str) -> bool:
    remainder = sentence.replace(anchor, "")
    remainder = re.sub(r"(?:该|本|上述|这个|此)?(?:风储|风电|光伏|储能|水电)?(?:电站项目|项目|电站)", "", remainder, count=1)
    remainder = re.sub(r"该项目|本项目|该电站|这个项目", "", remainder)
    return bool(re.search(r"项目|电站|另一|其他|另外|\b(?:project|plant)\b", remainder, re.I))


def _bound_sentences(surface: str, anchors: list[str]) -> list[str]:
    sentences = [s.strip() for s in re.split(r"[。；;\n]", surface) if s.strip()]
    result = []
    for i, sentence in enumerate(sentences):
        for anchor in anchors:
            if anchor in sentence and not _has_other_project(sentence, anchor):
                result.append(sentence)
                break
            if anchor in sentence:
                # A named project with measurements in its first clause can be followed
                # by portfolio/background project descriptions. Keep only that clause.
                first_clause = re.split(r"[，,]", sentence)[0]
                if anchor in first_clause and not _has_other_project(first_clause, anchor):
                    result.append(first_clause)
                    break
            if not re.match(r"(?:该项目|本项目|项目现场|项目[一二三四五六七八九十]+期|该电站)", sentence):
                continue
            if _has_other_project(sentence, ""):
                continue
            # At most two preceding sentences; every intervening sentence must explicitly
            # refer back to this project, never cross another named project or a semicolon.
            for offset in (1, 2):
                if i < offset:
                    continue
                previous = sentences[i - offset]
                if offset == 2 and not re.search(r"该项目|本项目", sentences[i - 1]):
                    continue
                if anchor in previous and not _has_other_project(previous, anchor):
                    start = surface.find(previous)
                    end = surface.find(sentence, start + len(previous))
                    if end >= 0 and ";" not in surface[start:end] and "；" not in surface[start:end]:
                        result.append(sentence)
                        break
            if sentence in result:
                break
    return result


def _restore_adjacent_capacity_context(item: dict, title: str, content: str, target: str) -> dict | None:
    """Restore a named preceding sentence across at most one explicit project-reference bridge.

    This copies a continuous source span, never assembles separate snippets or crosses
    a semicolon/project switch. The caller retains the original evidence for auditing.
    """
    quote = str(item.get("quote", "") or "")
    anchor = str(item.get("project_anchor", "") or "")
    if not quote or not anchor or anchor in quote or not re.match(
        r"(?:该项目|本项目|项目现场|项目[一二三四五六七八九十]+期)", quote
    ):
        return None
    for surface in (title, content):
        if surface.count(quote) != 1:
            continue
        pos = surface.index(quote)
        if _has_other_project(quote, ""):
            continue
        cursor = pos
        for depth in (1, 2):
            prefix = surface[:cursor].rstrip()
            if not prefix.endswith("。"):
                break
            preceding = prefix[:-1]
            start = max(preceding.rfind(mark) for mark in ("。", "；", ";", "\n")) + 1
            previous = preceding[start:]
            if len(previous) > 700:
                break
            if anchor in previous:
                if _has_other_project(previous, anchor):
                    break
                restored = dict(item, quote=surface[start:pos + len(quote)])
                valid, _ = validate_bound_evidence(restored, title, content, target_project=target)
                if valid:
                    return restored
                break
            if depth == 1 and re.search(r"该项目|本项目", previous) and not _has_other_project(previous, ""):
                cursor = start
                continue
            break
    return None


def _guard_manufacturing_capacity(ci: dict, title: str, content: str) -> None:
    """A production phrase must share its local source clause with this factory's actor.

    Exact text alone is insufficient in multi-factory articles. The declared developer
    is used only as a source-matching identifier, never as proof of ownership/identity.
    """
    raw = str(ci.get("MF Production Capacity Evidence", "") or "").strip()
    if not raw:
        return
    factory_clauses = [
        clause for clause in re.split(r"[。；;，,\n]", title + "\n" + content)
        if re.search(r"工厂|制造基地", clause) and re.search(r"建设|建厂|投资|年产", clause)
    ]
    # This guard addresses articles describing multiple factory developments. Preserve
    # the established simple-article atomic contract; do not invent a global actor rule.
    if len(factory_clauses) < 2:
        return
    developers = [v.strip() for v in re.split(r"[;；]", str(ci.get("Project Developer", "") or "")) if v.strip()]
    target = str(ci.get("Project Name", "") or "")
    developer_quote = str(ci.get("Project Developer Evidence", "") or "")
    supported = False
    for surface in (title, content):
        for clause in re.split(r"[。；;，,\n]", surface):
            if raw not in clause:
                continue
            for actor in developers:
                if actor not in clause:
                    continue
                # A model actor must also identify this target, or have a source-exact
                # factory-development quote. Product mismatch remains disqualifying.
                actor_bound = actor in target or (
                    developer_quote and developer_quote in surface and actor in developer_quote
                    and re.search(r"工厂|制造|生产|建厂", developer_quote)
                )
                conflicting_product = (
                    bool(re.search(r"风电|风机|叶片|塔筒", target))
                    and bool(re.search(r"电池|储能", clause))
                ) or (bool(re.search(r"电池|储能", target)) and bool(re.search(r"风电|风机|叶片|塔筒", clause)))
                if actor_bound and not conflicting_product:
                    supported = True
    if supported:
        return
    reason = "制造产能原文未绑定当前工厂/企业，禁止跨厂借用，待复核"
    ci["production_capacity_evidence_original"] = raw
    ci["MF Production Capacity Evidence"] = ""
    ci["production_capacity_raw_phrase"] = ""
    ci["production_capacity_value_parsed"] = None
    ci["production_capacity_unit_normalized"] = None
    ci["capacity_needs_review"] = True
    ci["capacity_evidence_errors"] = [reason]
    ci.setdefault("_txmf_contract_errors", []).append(reason)
    ci["capacity_evidence_version"] = CAPACITY_EVIDENCE_VERSION
    ci["capacity_details_note"] = reason
    details = str(ci.get("Capacity Details", "") or "")
    if raw in details:
        ci["capacity_details_original"] = details
        ci["Capacity Details"] = details.replace(raw, "[产能待复核]")


def _apply_legacy_capacity_evidence(ci: dict, title: str, content: str) -> None:
    """Apply bounded arithmetic and validate battery measurements against project-local text.

    Legacy plain generation totals remain handled by the existing unit parser. New explicit
    evidence is checked, and detected single-unit quotations never become project totals.
    Ambiguous equipment subsets do not establish a total. Audit notes survive DB ingestion.
    """
    if ci.get("Category") == "新能源制造业":
        _guard_manufacturing_capacity(ci, title, content)
        return
    if ci.get("Category") == "输变电建设":
        return
    errors = []
    anchors = _anchors(ci)
    supplied = ci.get("Capacity Evidence")
    if isinstance(supplied, dict) and not any(supplied.values()):
        supplied = None
    valid = not bool(supplied)
    if supplied:
        valid, reason = validate_bound_evidence(supplied, title, content, target_project=str(ci.get("Project Name", "")))
        if not valid and isinstance(supplied, dict):
            restored = _restore_adjacent_capacity_context(supplied, title, content, str(ci.get("Project Name", "")))
            # A model may use "Río Cullen油田的风储项目" while the adjacent source
            # sentence uses "Río Cullen项目". Only a unique Latin proper name already
            # shared by the target and original model anchor can bridge that wording.
            if restored is None and len(anchors) == 1:
                core = anchors[0]
                original_anchor = str(supplied.get("project_anchor", "") or "")
                quote = str(supplied.get("quote", "") or "")
                if (re.fullmatch(r"[A-Za-zÀ-ɏ]+(?:[ -][A-Za-zÀ-ɏ]+){0,3}", core)
                        and len(core) >= 3 and core in original_anchor):
                    candidate = dict(supplied, project_anchor=core)
                    if core in quote and not _has_other_project(quote, core):
                        candidate_valid, _ = validate_bound_evidence(
                            candidate, title, content, target_project=str(ci.get("Project Name", "")),
                        )
                        if candidate_valid:
                            restored = candidate
                    elif quote.startswith("项目现场"):
                        restored = _restore_adjacent_capacity_context(
                            candidate, title, content, str(ci.get("Project Name", "")),
                        )
            if restored:
                ci["capacity_evidence_original"] = dict(supplied)
                ci["Capacity Evidence"] = supplied = restored
                valid = True
                ci["capacity_evidence_context_restored"] = True
        if valid:
            anchors = [supplied["project_anchor"]]
        else:
            errors.append("Capacity Evidence: " + reason)
            ci["capacity_mw_parsed"] = None
    bound = _bound_sentences(title, anchors) + _bound_sentences(content, anchors)
    if supplied and valid:
        # A validated contiguous quote can isolate one named project in a comma-separated
        # list. Do not let other projects elsewhere in that sentence erase its capacity.
        bound += _bound_sentences(str(supplied["quote"]), anchors)
    # Repair source-bound operating or explicitly configured wind/solar-storage facts
    # when the model quoted a generic phrase. Never infer battery MW or MWh.
    if ci.get("storage_status_normalized") != "confirmed":
        for sentence in bound:
            source = ci.get("Generation Sources", [])
            label = "风储" if source == ["风电"] else "光储" if source == ["光伏"] else None
            operating = (label and label in sentence and re.search(r"投产|投运|并网|建成", sentence)
                    and not _NEGATIVE.search(sentence)
                    and not re.search(r"未来|将|规划|预计|有望|拟|(?:未|没有|不曾).{0,3}(?:投产|投运|并网|建成)", sentence))
            configured = any(
                re.search(r"(?:采用|配备|配置|搭载)[^。；;]{0,100}(?:储能系统|储能解决方案|电池储能|BESS)", clause, re.I)
                and not _NEGATIVE.search(clause)
                and not re.search(r"未|不采用|不配置|规划|计划|考虑|评估|研究|可能|待定|可选|拟", clause)
                for clause in re.split(r"[，,]", sentence)
            )
            if label and (operating or configured):
                ci["storage_status_evidence_original"] = {
                    "status": ci.get("Storage Status"),
                    "normalized_status": ci.get("storage_status_normalized"),
                    "quote": ci.get("Energy Composition Evidence"),
                }
                ci["Storage Status"] = ci["storage_status_normalized"] = "confirmed"
                ci["Energy Composition Evidence"] = sentence
                ci["energy_composition_evidence_normalized"] = sentence
                ci["technology_tags_json"] = json.dumps([label], ensure_ascii=False)
                ci["storage_status_source_restored"] = True
                break
    raw = str(ci.get("Capacity (MW)", "") or "")
    totals = [(s, m) for s in bound for m in _TOTAL.finditer(s) if not _STORAGE.search(s[:m.start()]) and not _NEGATIVE.search(s)]
    units = [(s, m) for s in bound for m in _UNIT.finditer(s)
             if not _PARTIAL.search(s) and not _NEGATIVE.search(s)
             and not re.search(r"燃气|柴油|燃煤|水轮|汽轮", s)]
    if ci.get("Category") == "储能":
        ci["capacity_mw_parsed"] = None
    elif totals:
        values = {parse_capacity_mw(m.group("power"))[0] for _, m in totals}
        if len(values) == 1:
            sentence, match = totals[0]
            ci["capacity_mw_parsed"] = values.pop()
            ci["capacity_mw_raw_phrase"] = match.group(0)
            ci["Capacity (MW)"] = match.group(0)
        else:
            ci["capacity_mw_parsed"] = None
            errors.append("conflicting project totals")
    elif units and ci.get("Generation Sources") == ["风电"]:
        values = {(_count(m.group("count")), parse_capacity_mw(m.group("power"))[0]) for _, m in units}
        if len(values) == 1:
            count, power = values.pop()
            if count > 0 and power is not None:
                sentence, match = units[0]
                ci["capacity_mw_parsed"] = count * power
                ci["capacity_mw_raw_phrase"] = sentence
                ci["Capacity (MW)"] = sentence
                ci["capacity_details_note"] = f"capacity_source=unit_count_product; {count} × {power:g} MW = {count * power:g} MW；原文：{sentence}"
        else:
            ci["capacity_mw_parsed"] = None
            errors.append("conflicting turbine counts or unit capacities")
    elif raw and any(raw in s and re.search(r"单机|每台", s) for s in re.split(r"[。；;\n]", content)):
        ci["capacity_mw_parsed"] = None
        errors.append("single-unit capacity lacks unambiguous complete project count")
    if supplied and (not valid or not any(str(supplied.get("quote", "")) in s or s in str(supplied.get("quote", "")) for s in bound)):
        ci["capacity_mw_parsed"] = None
        errors.append("capacity quotation has no bounded target-project context")
    elif supplied and raw and raw not in supplied["quote"] and not units and not totals:
        ci["capacity_mw_parsed"] = None
        errors.append("capacity value absent from supplied source quotation")
    elif raw and raw in title + content and not any(raw in s for s in bound) and not units and not totals:
        ci["capacity_mw_parsed"] = None
        errors.append("capacity belongs to an unbound project context")
    for field, parsed, raw_key in (
        ("Storage Power", "storage_power_mw_parsed", "storage_power_mw_raw_phrase"),
        ("Storage Energy", "storage_energy_mwh_parsed", "storage_energy_mwh_raw_phrase"),
    ):
        value = str(ci.get(field, "") or "")
        if not value:
            continue
        supported = False
        dimension = 0 if field == "Storage Power" else 1
        desired = parse_storage_measurements(value, value)[dimension]
        measurement_pattern = (
            r"\d+(?:\.\d+)?\s*(?:MW(?!h)|兆瓦(?!时))" if dimension == 0
            else r"\d+(?:\.\d+)?\s*(?:MWh|GWh|兆瓦时|吉瓦时)"
        )
        for s in bound:
            for clause in re.split(r"[，,]|并|及|和(?=\s*\d)", s):
                if _STORAGE.search(clause) and not _NEGATIVE.search(clause) and not re.search(
                    r"风机|风电|风力|光伏|发电|单机|年产", clause.replace(anchors[0], "") if anchors else clause
                ):
                    measures = re.findall(measurement_pattern, clause, re.I)
                    values = {parse_storage_measurements(m, m)[dimension] for m in measures}
                    if desired is not None and values == {desired}:
                        supported = True
                        # Restore the source spelling when a legacy/model phrase converted
                        # MW to 兆瓦 or MWh to 兆瓦时; raw columns remain actual source text.
                        ci[field] = measures[0]
                        ci[raw_key] = measures[0]
        if not supported:
            errors.append(field + ": no explicit project-bound storage measurement")
            ci[field] = ""
            ci[parsed] = None
            ci[raw_key] = ""
    ci["capacity_evidence_version"] = CAPACITY_EVIDENCE_VERSION
    ci["capacity_evidence_errors"] = errors
    if errors:
        note = "容量证据复核：" + "; ".join(errors)
        ci["capacity_details_note"] = "; ".join(filter(None, [ci.get("capacity_details_note"), note]))
