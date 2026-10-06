"""Private, offline enrichment review and paired metrics from frozen trial files.

Model agreement is descriptive only. Capacity proposals independently require
grounded project, source, quantity, participation and existing-event evidence.
This module never writes a production database or publishes external links.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import structured
from .capacity_apply import (
    Candidate, Candidates, GENERATION_CATEGORIES, POWER_UNIT_END, _after, _connect, _validate_target,
    manifest_hash, prepare_plan, rehearse_plan,
)
from .contracts import EventContext, Page
from .pilot import assert_sources, private_path
from .publishers import PublisherRegistry, verify_publisher
from .sample import file_hash
from .structured import Extraction, TargetCard
from .trial_budget import PRICES, usage_cost

VERSION = "capacity-review-v1"
UNITS = r"MWac|MWdc|MWp|MW|GW|kW|万千瓦|兆瓦|吉瓦|千瓦"
QUANTITY = re.compile(r"(?<![\d.])([0-9]+(?:\.[0-9]+)?)\s*(" + UNITS + r")" + POWER_UNIT_END, re.I)
PHASE = re.compile(r"第?[一二三四五六七八九十0-9]+期|\bphase\s+[A-Za-z0-9]+\b|\bstage\s+[IVX0-9]+\b", re.I)
PUBLIC_DECISIONS = {"event_reference", "project_reference"}


def _read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return value


def _save(path, value):
    """Review output is reproducible and refuses to overwrite a different review."""
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"Review output differs; choose a new report directory: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)


def _norm(text):
    return " ".join(str(text).split()).casefold()


def _anchors(card, text):
    return [term for term in card.scope_terms if len(term.strip()) >= 2 and _norm(term) in _norm(text)]


def _capacity_sentence(card, text, match):
    """A project name elsewhere in a multi-project paragraph cannot lend its identity."""
    before = re.split(r"[。！？；;\n]|(?<!\d)\.(?!\d)", text[:match.start()])[-1]
    after = re.split(r"[。！？；;\n]|(?<!\d)\.(?!\d)", text[match.end():])[0]
    return bool(_anchors(card, before + match[0] + after))


def _participation(target, context):
    """Recheck quoted original evidence, participant and project anchors locally."""
    articles = {a["url"]: a for a in context.source_articles}
    valid = []
    for evidence in target.get("participation_evidence", []):
        article = articles.get(evidence.get("url"))
        quote, alias, anchor = (evidence.get(k, "") for k in ("quote", "matched_alias", "project_anchor"))
        if (article and quote and alias and anchor and _norm(quote) in _norm(article.get("content", ""))
                and _norm(alias) in _norm(quote) and _norm(anchor) in _norm(quote)
                and re.search(r"承建|承包|中标|签约|签署|签订|供货|供应|提供|建设|施工|投资|收购|持股|开发|融资|贷款|运营|总包", quote)
                and any(evidence.get("participant") in [e.get("raw_entity_name"), e.get("implementing_entity"), e.get("entity_name")]
                        and e.get("publication_status") in (None, "", "eligible") for e in context.entities)):
            valid.append(evidence)
    return valid


def _event_binding(card, extraction, page, identity_ids):
    """Bind to the reviewed milestone; publication dates never substitute for it."""
    if card.stage_ambiguous or not card.time_start or not card.time_end:
        return None, ["target_event_ambiguous_or_undated"]
    body = structured.blocks(page)
    positions = {b["id"]: index for index, b in enumerate(body)}
    checks = []
    for observation in extraction.observations:
        text = structured.span_text(observation.evidence, body)
        when = observation.when
        check = {"action": observation.action, "quote": text, "stage_matches": observation.action in structured.STAGE_ACTIONS.get(card.stage, set()),
                 "actual": structured.actual_action(observation, text), "project_anchor": bool(_anchors(card, text)),
                 "identity_nearby": any(abs(positions[a] - positions[b]) <= 2 for a in observation.evidence.block_ids for b in identity_ids),
                 "date_matches": False}
        if when:
            date_text = structured.span_text(when.evidence, body)
            nearby = any(abs(positions[a] - positions[b]) <= 1 for a in observation.evidence.block_ids for b in when.evidence.block_ids)
            interval = structured.date_interval(when.expression, structured.publication_date(page))
            located = _norm(when.expression) in _norm(date_text)
            bound = structured.date_supports_action(observation, text, when.expression, card.scope_terms)
            if interval and nearby and located and bound:
                precision = (interval[1] - interval[0]).days <= max(60, (card.time_end - card.time_start).days)
                check["date_matches"] = precision and interval[0] <= card.time_end and interval[1] >= card.time_start
                check["interval"] = [d.isoformat() for d in interval]
        checks.append(check)
        if all(check[k] for k in ("stage_matches", "actual", "project_anchor", "identity_nearby", "date_matches")):
            return check, checks
    return None, checks or ["no_actual_milestone_observation"]


def assess_capacity_row(target: dict, card: TargetCard, case: dict, row: dict,
                        registry: PublisherRegistry, before: dict) -> dict:
    """A proposal gate independent of the extraction pipeline's final decision."""
    result = {"case_id": case["case_id"], "provider": row["provider"], "reference_decision": row.get("decision"),
              "reference_reason": row.get("reason"), "capacity_status": "pending", "reasons": [], "candidates": [], "quantities": []}
    context = EventContext.model_validate(case["context"])
    page = Page.model_validate(case["page"])
    result.update(url=page.final_url, title=page.title, body_sha256=page.text_hash)
    if page.problem or page.http_status != 200 or not page.text:
        result["reasons"].append(page.problem or "body_not_readable")
        return result
    if hashlib.sha256(page.text.encode("utf-8")).hexdigest() != page.text_hash:
        result["reasons"].append("body_hash_mismatch")
        return result
    if not row.get("extraction"):
        result["reasons"].append("no_valid_model_extraction:" + str(row.get("reason", "")))
        return result
    extraction = Extraction.model_validate(row["extraction"])
    body = structured.blocks(page)
    identity = [structured.span_text(span, body) for span in extraction.identity_evidence]
    result.update(language=extraction.language, identity_quotes=identity,
                  participant_observations=[p.model_dump() | {"quote": structured.span_text(p.evidence, body)} for p in extraction.participants])
    source = verify_publisher(page, registry)
    if source.get("host") == "www.yidaiyilu.gov.cn" and not source.get("problem") and not source.get("review_id"):
        item_source = structured.source_result(page, extraction, body)
        if item_source.get("problem"):
            source["problem"] = item_source["problem"]
    result["source"] = source
    if card.stage != before.get("project_stage"):
        result["reasons"].append("reviewed_stage_differs_from_existing_event")
    if source.get("problem") or source.get("source_type") not in {"official", "media"}:
        result["reasons"].append("source_unverified:" + str(source.get("problem")))
    if extraction.language not in {"zh", "en"}:
        result["reasons"].append("evidence_language_unresolved")
    if extraction.relation != "same_project" or not identity or not any(_anchors(card, text) for text in identity):
        result["reasons"].append("specific_project_identity_not_grounded")
    if card.identity_conflicts or extraction.identity_conflicts:
        result["reasons"].append("identity_or_capacity_conflict")
        result["conflicts"] = card.identity_conflicts + extraction.identity_conflicts
    participation = _participation(target, context)
    result["participation_evidence"] = participation
    if not participation:
        result["reasons"].append("chinese_participation_not_grounded_in_original")
    identity_ids = {key for span in extraction.identity_evidence for key in span.block_ids}
    binding, result["event_checks"] = _event_binding(card, extraction, page, identity_ids)
    if binding is None:
        result["reasons"].append("existing_event_binding_not_proven")
    # Keep quantity evidence visible even when event or other gates are pending.
    phase_names = list(dict.fromkeys(PHASE.findall(str(context.project.get("project_name", "")))))
    for measure in extraction.measurements:
        if measure.kind not in ({"generation_power", "pv_power"} if context.project.get("category") == "光伏" else {"generation_power"}):
            continue
        text = structured.span_text(measure.evidence, body)
        quantity = {"model_value": measure.value, "model_unit": measure.unit, "scope": measure.scope,
                    "quote": text, "reasons": []}
        result["quantities"].append(quantity)
        if measure.scope != "whole_project":
            quantity["reasons"].append("component_or_unknown_capacity_scope")
        if not _anchors(card, text):
            quantity["reasons"].append("capacity_clause_has_no_project_anchor")
        grounded = structured.measurement_value(measure, text)
        matches = [match for match in QUANTITY.finditer(text) if grounded and math.isclose(
            float(match[1]) * {"GW": 1000, "吉瓦": 1000, "kW": .001, "千瓦": .001, "万千瓦": 10}.get(match[2], 1),
            grounded["canonical_value"], rel_tol=1e-9)]
        if not grounded or len({(m[1], m[2]) for m in matches}) != 1:
            quantity["reasons"].append("explicit_raw_value_and_unit_not_unique")
        if matches and not all(_capacity_sentence(card, text, match) for match in matches):
            quantity["reasons"].append("capacity_sentence_has_no_project_anchor")
        # Portfolio, production and equipment supply totals cannot become plant capacity.
        if re.search(r"组合|项目群|打包|年产|组件订单|设备订单|\bportfolio\b|\bper year\b|\bannual production\b", text, re.I):
            quantity["reasons"].append("portfolio_or_non_generation_quantity")
        scope, phase = "project", "whole_project"
        if phase_names:
            if len(phase_names) != 1 or not binding or _norm(phase_names[0]) not in _norm(text) or _norm(phase_names[0]) not in _norm(binding["quote"]):
                quantity["reasons"].append("physical_phase_binding_not_proven")
            else:
                scope, phase = "phase", phase_names[0]
        if matches:
            quantity.update(raw_value=matches[0][1], raw_unit=matches[0][2], normalized_mw=grounded["canonical_value"])
        if quantity["reasons"] or result["reasons"]:
            continue
        candidate_id = "cap-" + manifest_hash([context.project_id, context.event_id, page.text_hash, matches[0][0], scope, phase])[:24]
        candidate = Candidate.model_validate({
            "candidate_id": candidate_id, "project_id": context.project_id, "event_id": context.event_id,
            "capacity_mw": float(grounded["canonical_value"]), "raw_value": matches[0][1], "raw_unit": matches[0][2],
            "scope": scope, "phase": phase, "evidence": {"url": page.final_url, "language": extraction.language,
                "publisher": source["publisher"], "source_type": source["source_type"], "quote": text,
                "content_text": page.text, "content_sha256": page.text_hash},
            "event_binding": {"project_id": context.project_id, "event_id": context.event_id,
                "project_stage": before["project_stage"], "phase": phase, "evidence_quote": binding["quote"],
                "reason": "Frozen project anchor, actual milestone and reviewed event interval match; no human approval inferred."},
            "before": before, "status": "eligible", "conflict": False})
        result["candidates"].append(candidate.model_dump())
    if result["candidates"]:
        result["capacity_status"] = "eligible_for_human_review"
    elif not result["quantities"]:
        result["reasons"].append("no_generation_capacity_observation")
    return result


def _render_review(report):
    esc = lambda value: html.escape(str(value))
    sections = []
    for project in report["projects"]:
        proposals = project["proposals"]
        rows = []
        for observation in project["observations"]:
            quantities = "".join(f'<blockquote>{esc(q["quote"])}</blockquote><p>{esc(q.get("raw_value", "?"))} {esc(q.get("raw_unit", "?"))} → {esc(q.get("normalized_mw", "?"))} MW；范围 {esc(q["scope"])}；{esc("; ".join(q["reasons"]))}</p>' for q in observation["quantities"])
            rows.append(f'<details><summary>{esc(observation["provider"])} · {esc(observation.get("language", "未知语言"))} · {esc(observation["capacity_status"])}</summary>'
                        f'<p>{esc(observation["title"])} — <a href="{esc(observation["url"])}">私有证据</a></p>'
                        f'<p>{esc("; ".join(observation["reasons"]))}</p>{quantities}'
                        f'<pre>{esc(json.dumps(observation.get("event_checks", []), ensure_ascii=False, indent=2))}</pre></details>')
        sections.append(f'<section><h2>{esc(project["project_id"])} · {esc(project["project_name"])}</h2>'
                        f'<p><strong>{esc(project["status"])}</strong>　{esc("; ".join(project["reasons"]))}</p>'
                        f'<p>拟绑定事件：{esc(project["event_id"])} · {esc(project["stage"])}；原容量：{esc(project["before"]["capacity_mw"])} MW；拟填：{esc([p["capacity_mw"] for p in proposals])}</p>'
                        f'<p>{esc(project["display_expectation"])}</p><details><summary>原报道中的中方参与依据</summary><pre>{esc(json.dumps(project["participation_evidence"], ensure_ascii=False, indent=2))}</pre></details>'
                        f'<details open><summary>独立复核：项目级容量线索（不等于写入资格）</summary><pre>{esc(json.dumps(project["project_capacity_findings"], ensure_ascii=False, indent=2))}</pre></details>'
                        + "".join(rows) + '</section>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' \
           '<title>容量候选私有复核</title><style>body{max-width:1040px;margin:40px auto;padding:0 20px;font:16px/1.6 system-ui;background:#f5f7fa;color:#172b4d}section{background:white;padding:24px;margin:22px 0;border-radius:10px}h1,h2{line-height:1.3}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}blockquote{border-left:3px solid #4b83b5;margin:12px 0;padding-left:14px}summary{cursor:pointer;padding:10px 0}a{color:#1261a0}</style>' \
           '<h1>容量候选私有复核</h1><p>这份报告不构成人工批准。模型一致不等于证据充分；英文来源仅作私有证据，链接不进入公开字段。生产数据库未修改。</p>' \
           f'<p>项目 {len(report["projects"])}；可交人工审阅的容量候选 {report["candidate_count"]}；标签/卡片人工确认：{esc(report["human_confirmed"])}</p>' + "".join(sections) + '</html>'


def build_capacity_review(input_dir: Path, main_db: Path, report_dir: Path | None = None) -> dict:
    """Generate all-ten-project review plus an eligible-only apply-contract file."""
    input_dir = private_path(input_dir, existing=True)
    output = private_path(report_dir or input_dir / "review")
    names = ("new-comparison.json", "new-corpus.json", "selected-targets.json", "reviewed-target-cards.json", "new-labels.json")
    comparison, corpus, selected, cards, labels = [_read(input_dir / name) for name in names]
    inputs = {name: file_hash(input_dir / name) for name in names}
    if comparison.get("freeze", {}).get("corpus_sha256") != inputs["new-corpus.json"]:
        raise ValueError("Comparison not bound to corpus")
    if labels.get("corpus_sha256") != inputs["new-corpus.json"] or comparison["freeze"].get("labels_sha256") != inputs["new-labels.json"]:
        raise ValueError("Independent review labels not frozen to comparison corpus")
    registry_path = next((input_dir / name for name in ("new-publisher-registry.json", "publisher-registry.json")
                          if (input_dir / name).is_file() and file_hash(input_dir / name) == comparison["freeze"].get("registry_sha256")), None)
    if registry_path is None:
        raise ValueError("Frozen comparison publisher registry not found")
    registry_data = _read(registry_path)
    inputs[registry_path.name] = file_hash(registry_path)
    if corpus.get("selection_sha256") != inputs["selected-targets.json"] or corpus.get("reviewed_cards_sha256") != inputs["reviewed-target-cards.json"] or cards.get("selection_sha256") != inputs["selected-targets.json"]:
        raise ValueError("Corpus or cards not bound to frozen selection")
    assert_sources(selected["source_hashes"])
    if not any(Path(path).resolve() == main_db.resolve() for path in selected["source_hashes"]):
        raise ValueError("Requested main database not in frozen source manifest")
    targets = selected["events"]
    if len(targets) != 10 or len({t["project_id"] for t in targets}) != 10:
        raise ValueError("Review requires the frozen ten distinct projects")
    cases = {case["case_id"]: case for case in corpus["cases"]}
    if len(cases) != len(corpus["cases"]):
        raise ValueError("Duplicate corpus cases")
    rows = defaultdict(list)
    seen = set()
    for row in comparison["rows"]:
        key = (row["case_id"], row["provider"])
        if row["case_id"] not in cases or key in seen:
            raise ValueError("Unknown or duplicate comparison row")
        seen.add(key)
        rows[row["event_id"]].append(row)
    registry = PublisherRegistry.model_validate(registry_data)
    projects, candidates = [], []
    with closing(_connect(main_db, readonly=True)) as conn:
        conn.execute("BEGIN")
        for target in targets:
            context = EventContext.model_validate(target["context"])
            card = TargetCard.model_validate(cards["cards"][target["event_id"]])
            if (card.project_id, card.event_id, card.input_fingerprint) != (context.project_id, context.event_id, context.fingerprint()):
                raise ValueError("Reviewed card context mismatch")
            live = conn.execute("SELECT * FROM project_events WHERE id=?", (target["event_id"],)).fetchone()
            if live is None or dict(live) != context.event:
                raise ValueError("Full before event differs from live database")
            before = dict(live)
            project = {"project_id": target["project_id"], "project_name": context.project.get("project_name"),
                       "event_id": target["event_id"], "stage": before["project_stage"], "pool": target["pool"],
                       "before": before, "status": "pending", "reasons": [], "observations": [], "proposals": [],
                       "project_capacity_findings": [],
                       "participation_evidence": _participation(target, context),
                       "display_expectation": "无生产更改；英文证据链接不公开。"}
            for row in rows[target["event_id"]]:
                case = cases[row["case_id"]]
                if (case["project_id"], case["event_id"], row["project_id"]) != (context.project_id, context.event_id, context.project_id):
                    raise ValueError("Comparison/corpus project owner mismatch")
                if case["context"] != target["context"] or case["card"] != cards["cards"][target["event_id"]]:
                    raise ValueError("Case differs from frozen reviewed target")
                try:
                    observation = assess_capacity_row(target, card, case, row, registry, before)
                except (ValueError, KeyError, TypeError) as exc:
                    observation = {"case_id": case["case_id"], "provider": row["provider"], "capacity_status": "pending", "quantities": [],
                                   "candidates": [], "reasons": ["invalid_grounded_extraction:" + str(exc)], "url": case["page"]["final_url"], "title": case["page"].get("title", "")}
                project["observations"].append(observation)
            # Preserve independently reviewed field clues even when neither model
            # grounds them or the target event cannot be bound. These never enter
            # the apply manifest and are explicitly not human-approved findings.
            for case in cases.values():
                if case["event_id"] != target["event_id"]:
                    continue
                label = labels.get("cases", {}).get(case["case_id"], {})
                expected = label.get("capacity_expected_mw")
                if expected is None:
                    continue
                quotes = [e["quote"] for e in label.get("evidence_sources", []) if e.get("source") == "candidate_body"
                          and e.get("url") == case["page"]["final_url"] and e.get("quote")
                          and _norm(e["quote"]) in _norm(case["page"]["text"])]
                if quotes:
                    project["project_capacity_findings"].append({
                        "case_id": case["case_id"], "capacity_mw": expected, "status": "provisional_project_level_only",
                        "human_confirmed": labels.get("human_confirmed", False), "reviewer": labels.get("reviewer"),
                        "evidence_quotes": quotes, "url": case["page"]["final_url"], "reason": label.get("reason"),
                        "details": label.get("capacity_details"), "event_bindable": label.get("event_bindable"),
                        "automatic_existing_event_write_eligible": False})
            eligible = [candidate for obs in project["observations"] for candidate in obs["candidates"]]
            if before.get("capacity_mw") is not None or before.get("capacity_mw_raw") not in (None, ""):
                project.update(status="existing_capacity_preserved", reasons=["fill_null_only_existing_value_not_overwritten"])
            elif context.project.get("category") not in GENERATION_CATEGORIES:
                project["reasons"] = ["outside_generation_capacity_scope"]
            elif not eligible:
                project["reasons"] = sorted({reason for obs in project["observations"] for reason in obs["reasons"] + [r for q in obs["quantities"] for r in q["reasons"]]}) or ["no_candidate_page_or_model_result"]
            elif len({(c["capacity_mw"], c["raw_unit"], c["scope"], c["phase"]) for c in eligible}) != 1:
                project["reasons"] = ["multiple_capacity_values_or_bases_require_review"]
            else:
                # Stable source preference; model agreement neither necessary nor sufficient.
                chosen = sorted(eligible, key=lambda c: (c["evidence"]["source_type"] != "official", c["evidence"]["language"] != "zh", c["evidence"]["url"], c["candidate_id"]))[0]
                try:
                    model = Candidate.model_validate(chosen)
                    _validate_target(conn, model)
                except ValueError as exc:
                    project["reasons"] = ["apply_contract_rejected:" + str(exc)]
                else:
                    project.update(status="eligible_for_human_review", proposals=[chosen], after=_after(model),
                                   display_expectation="审批并应用后仅补发电容量；是否进入公开展示还需原有公开政策与重新导出验证。英文来源链接不进入公开字段。")
                    candidates.append(chosen)
            projects.append(project)
    assert_sources(selected["source_hashes"])
    manifest = Candidates.model_validate({"schema_version": "capacity-candidates-v1", "candidates": candidates}).model_dump()
    report = {"version": VERSION, "human_confirmed": False, "approval_status": "not_approved", "production_applied": False,
              "inputs": inputs, "source_hashes": selected["source_hashes"], "candidate_count": len(candidates),
              "candidate_manifest_sha256": manifest_hash(manifest), "projects": projects}
    _save(output / "capacity-candidates.json", manifest)
    _save(output / "capacity-review.json", report)
    rendered = _render_review(report)
    html_path = output / "capacity-review.html"
    if html_path.exists() and html_path.read_text(encoding="utf-8") != rendered:
        raise FileExistsError("HTML review differs; choose a new output directory")
    html_path.write_text(rendered, encoding="utf-8")
    return report


class ComparisonLabel(BaseModel):
    model_config = ConfigDict(extra="allow")
    expected_decision: str
    acceptable_decisions: list[str] = Field(min_length=1)
    capacity_expected_mw: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reason: str = Field(min_length=1)


def paired_metrics(comparison_path: Path, corpus_path: Path, labels_path: Path) -> dict:
    """Score provider pairs against explicit provisional labels, retaining every case."""
    comparison, corpus, labels = map(_read, (comparison_path, corpus_path, labels_path))
    corpus_hash = file_hash(corpus_path)
    if labels.get("corpus_sha256") != corpus_hash or comparison.get("freeze", {}).get("corpus_sha256") != corpus_hash:
        raise ValueError("Metrics labels or comparison not bound to corpus")
    if comparison["freeze"].get("labels_sha256") != file_hash(labels_path):
        raise ValueError("Metrics labels changed after comparison freeze")
    cases = {c["case_id"]: c for c in corpus["cases"]}
    if set(labels.get("cases", {})) != set(cases) or not isinstance(labels.get("human_confirmed"), bool):
        raise ValueError("Metrics require complete labels and explicit human confirmation flag")
    results, aggregates, details = {}, defaultdict(Counter), []
    counted_requests = defaultdict(set)
    for row in comparison["rows"]:
        key = row["case_id"], row["provider"]
        if key in results or row["case_id"] not in cases or row["provider"] not in {"openai", "deepseek"}:
            raise ValueError("Unknown/duplicate paired result")
        results[key] = row
    for case_id, case in cases.items():
        label = ComparisonLabel.model_validate(labels["cases"][case_id])
        decisions = PUBLIC_DECISIONS | {"pending", "rejected"}
        if label.expected_decision not in label.acceptable_decisions or not set(label.acceptable_decisions).issubset(decisions):
            raise ValueError("Inconsistent expected/acceptable labels")
        detail = {"case_id": case_id, "project_id": case["project_id"], "event_id": case["event_id"],
                  "expected_decision": label.expected_decision, "acceptable_decisions": label.acceptable_decisions,
                  "capacity_expected_mw": label.capacity_expected_mw, "label_reason": label.reason, "providers": {}}
        for provider in ("deepseek", "openai"):
            row = results.get((case_id, provider), {})
            actual = row.get("decision")
            acceptable = actual in label.acceptable_decisions
            expected_public = bool(PUBLIC_DECISIONS.intersection(label.acceptable_decisions))
            false_allow = actual in PUBLIC_DECISIONS and not expected_public
            event_overclaim = actual == "event_reference" and "event_reference" not in label.acceptable_decisions
            false_block = not acceptable and expected_public and actual not in PUBLIC_DECISIONS
            values = []
            format_failure = False
            evidence_invalid = str(row.get("reason", "")).startswith("evidence_or_projection_invalid")
            page_problem = case["page"].get("problem")
            if row.get("extraction"):
                try:
                    extraction = Extraction.model_validate(row["extraction"])
                    page = Page.model_validate(case["page"])
                    # A schema-valid response can still invent span identifiers.
                    body = structured.blocks(page)
                    for span in extraction.identity_evidence:
                        structured.span_text(span, body)
                    for observation in extraction.observations:
                        structured.span_text(observation.evidence, body)
                        if observation.when:
                            structured.span_text(observation.when.evidence, body)
                    for measure in extraction.measurements:
                        if measure.kind in {"generation_power", "pv_power"} and measure.scope == "whole_project":
                            evidence = structured.measurement_evidence(measure, body)
                            if evidence and evidence["unit"] == "MW":
                                values.append(evidence["canonical_value"])
                except ValidationError:
                    format_failure = True
                except (ValueError, KeyError, TypeError):
                    evidence_invalid = True
            elif not page_problem and re.search(r"json|schema|format|extract|structured|validation", str(row.get("reason", "")), re.I):
                format_failure = True
            expected = label.capacity_expected_mw
            capacity_correct = not values if expected is None else bool(values) and all(math.isclose(v, expected, rel_tol=1e-8) for v in values)
            call = row.get("call", {})
            request_id = call.get("request_sha256") or f"{case_id}:{provider}"
            new_request = bool(call) and request_id not in counted_requests[provider]
            if new_request:
                counted_requests[provider].add(request_id)
            cost = usage_cost(call, provider) if call else None
            elapsed = call.get("elapsed_seconds")
            item = {"decision": actual, "reason": row.get("reason"), "acceptable": acceptable,
                    "false_allow": false_allow, "event_overclaim": event_overclaim, "false_block": false_block,
                    "false_project_allow": actual == "project_reference" and not expected_public,
                    "false_event_allow": event_overclaim, "evidence_or_projection_invalid": evidence_invalid,
                    "page_problem": page_problem, "model_call_recorded": bool(call),
                    "format_failure": format_failure, "missing_result": not bool(row),
                    "processing_failed": row.get("processing_status") == "failed", "grounded_capacity_mw": sorted(set(values)),
                    "capacity_matches_label": capacity_correct, "capacity_false_assertion": expected is None and bool(values),
                    "capacity_missing": expected is not None and not values,
                    "elapsed_seconds": elapsed, "known_usage_cost_usd": cost, "response_model": call.get("response_model")}
            detail["providers"][provider] = item
            counts = aggregates[provider]
            counts["cases"] += 1
            for flag in ("acceptable", "false_allow", "event_overclaim", "false_project_allow", "false_event_allow", "false_block", "format_failure", "evidence_or_projection_invalid", "missing_result", "processing_failed", "capacity_matches_label", "capacity_false_assertion", "capacity_missing"):
                counts[flag] += int(item[flag])
            counts["page_problem_cases"] += bool(page_problem)
            counts["actual_call_records"] += int(new_request)
            counts["rows_without_call"] += int(not bool(call))
            if new_request:
                counts["elapsed_known_sum_seconds"] += elapsed if isinstance(elapsed, (int, float)) else 0
                counts["elapsed_unknown_requests"] += int(not isinstance(elapsed, (int, float)))
                counts["known_usage_cost_usd"] += cost or 0
                counts["unknown_usage_requests"] += int(cost is None)
        detail["decisions_differ"] = detail["providers"]["deepseek"]["decision"] != detail["providers"]["openai"]["decision"]
        details.append(detail)
    return {"version": "paired-enrichment-metrics-v1", "split": comparison.get("split"), "human_confirmed": labels["human_confirmed"],
            "label_status": "human-confirmed" if labels["human_confirmed"] else "agent-reviewed provisional labels; not a human gold standard",
            "label_reviewer": labels.get("reviewer"), "corpus_sha256": corpus_hash, "labels_sha256": file_hash(labels_path),
            "cost_basis": "Frozen trial accounting rates; known usage only, unknown is not zero; not an invoice",
            "capacity_metric_basis": "Grounded model quantity observations against target labels, before project/event/apply gates; not a count of public or database errors",
            "accounting_rates_per_million": PRICES, "budget_upper_bound": comparison.get("budget"),
            "providers": dict(aggregates), "paired_decision_differences": sum(d["decisions_differ"] for d in details), "cases": details}


def write_paired_metrics(input_dir: Path, report_dir: Path | None = None) -> dict:
    input_dir = private_path(input_dir, existing=True)
    output = private_path(report_dir or input_dir / "review")
    report = {"version": "old-new-paired-comparison-v1", "approval_status": "not_approved", "splits": {}}
    for split in ("old", "new"):
        report["splits"][split] = paired_metrics(input_dir / f"{split}-comparison.json", input_dir / f"{split}-corpus.json", input_dir / f"{split}-labels.json")
    _save(output / "paired-metrics.json", report)
    return report


def rehearse_eligible_report(main_db: Path, report_dir: Path) -> dict:
    """Developer acceptance only: never converts report eligibility into user approval."""
    report_dir = private_path(report_dir, existing=True)
    manifest = _read(report_dir / "capacity-candidates.json")
    report = _read(report_dir / "capacity-review.json")
    if manifest_hash(manifest) != report["candidate_manifest_sha256"]:
        raise ValueError("Candidate manifest differs from review")
    if not manifest["candidates"]:
        result = {"status": "blocked_no_eligible_capacity_candidate", "production_applied": False,
                  "reason": "No evidenced existing-event fill-null candidate; no acceptance fixture manufactured."}
        _save(report_dir / "capacity-rehearsal-result.json", result)
        return result
    approval = {"schema_version": "capacity-approval-v1", "candidate_manifest_sha256": manifest_hash(manifest),
                "approved_candidate_ids": [c["candidate_id"] for c in manifest["candidates"]],
                "reviewer": "Codex developer acceptance; not user approval", "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "review_note": "Isolated acceptance of independently gated real evidence candidates; production requires separate user approval.",
                "approval_kind": "developer_rehearsal"}
    _save(report_dir / "developer-rehearsal-approval.json", approval)
    plan = prepare_plan(main_db, manifest, approval)
    _save(report_dir / "developer-rehearsal-plan.json", plan)
    result = rehearse_plan(main_db, plan, report_dir / "capacity-rehearsal")
    _save(report_dir / "capacity-rehearsal-result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["capacity", "metrics", "rehearse"])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--main-db", type=Path)
    args = parser.parse_args(argv)
    if args.command == "capacity":
        if args.main_db is None:
            parser.error("capacity requires --main-db")
        report = build_capacity_review(args.input, args.main_db, args.report_dir)
        print(json.dumps({"candidate_count": report["candidate_count"], "projects": len(report["projects"])}, ensure_ascii=False))
    elif args.command == "metrics":
        report = write_paired_metrics(args.input, args.report_dir)
        print(json.dumps({split: value["providers"] for split, value in report["splits"].items()}, ensure_ascii=False))
    else:
        if args.main_db is None:
            parser.error("rehearse requires --main-db")
        print(json.dumps(rehearse_eligible_report(args.main_db, args.report_dir or args.input / "review"), ensure_ascii=False))


if __name__ == "__main__":
    main()
