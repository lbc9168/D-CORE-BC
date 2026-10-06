"""Read-only recurring discovery population, independent of pilot sampling.

The public s03 baseline remains 2022 inclusive. Only this private discovery
population extends its date threshold to 2018, and additionally admits renewable
generation projects that otherwise pass that baseline but lack capacity.
"""

import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from pathlib import Path

from .enrichment_targets import GENERATION_CODES, participation_evidence, target_card
from .sample import ROOT, SampleConfig, candidate_list, load_context, readonly_connection
from .targets import TargetConfig, _urls, source_hashes

VERSION = "recurring-targets-v1"
DISCOVERY_YEAR = 2018


def _exporters():
    source = str(ROOT / "s03_data_exporter/src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from s03_data_exporter import events_exporter, flat_builder
    return events_exporter, flat_builder


def pool_for_projection(row: dict, domain: str) -> tuple[str | None, str | None]:
    """Relax exactly the private date threshold, then generation capacity only.

    Moving a copied row's date to the public threshold reuses s03 policy without
    mutating its module global, which is important during concurrent exports.
    TX/MF retain their terminal-event year semantics and dedicated policy.
    """
    import bri_enums
    exporter, _ = _exporters()
    check = exporter.passes_txmf_baseline if domain in {"TX", "MF"} else exporter.passes_baseline
    if check(row):
        return "reference", "public"
    year_key = "event_year" if domain in {"TX", "MF"} else "last_event_year"
    year = row.get(year_key)
    if year is None or year < DISCOVERY_YEAR:
        return None, None
    relaxed = {**row, year_key: max(year, exporter.BASELINE_YEAR)}
    if check(relaxed):
        return "reference", "hidden_by_year"
    category = bri_enums.category_code(row.get("category") or "")
    if domain != "main" or category not in GENERATION_CODES or row.get("capacity_mw") is not None:
        return None, None
    if not check({**relaxed, "capacity_mw": 1.0}):
        return None, None
    # Integrated energy is not necessarily a renewable generating station.
    if category == "HB":
        sources = json.loads(row.get("generation_sources") or "[]")
        if not isinstance(sources, list):
            raise ValueError("invalid_generation_sources")
        renewable = {"光伏", "风电", "水电", "地热及余热", "生物质"}
        named = str(row.get("project_name") or "")
        if not renewable.intersection(sources) and not any(term in named for term in renewable | {"风光", "光储", "风储"}):
            return None, None
    return "capacity_missing", "hidden_by_capacity" if year >= exporter.BASELINE_YEAR else "hidden_by_year_and_capacity"


def _year(event: dict) -> int | None:
    value = str(event.get("publish_date") or event.get("investment_date") or "")
    return int(value[:4]) if len(value) >= 4 and value[:4].isdigit() else None


def _card(context, evidence):
    """Only a locally bound sentence may supply an automatic event date.

    Broader source paragraphs remain available to verification, but their first
    date must not silently become the date of a different facility or action.
    """
    narrow = []
    for item in evidence:
        for sentence in re.split(r"[。！？\n]", item["quote"]):
            if (item["matched_alias"] in sentence and item["project_anchor"] in sentence
                    and len(re.findall(r"20\d{2}年\d{1,2}月\d{1,2}日", sentence)) == 1):
                narrow.append({**item, "quote": sentence})
    return target_card(context, narrow)


def build_targets(main_db: Path, raw_db: Path, *, changed_event_ids=None) -> list[dict]:
    """Return real event contexts; an optional ID set limits incremental work.

    Source hashes (including WAL) are checked before/after. All s03 builders run
    only on a disposable backup. Source articles are read through mode=ro.
    Existing public projects are not subjected to a second lexical participation
    gate; missing source text or an unknown stage limits validation, not recall.
    """
    cfg = TargetConfig(main_db=main_db, raw_db=raw_db)
    changed = None
    if changed_event_ids is not None:
        if isinstance(changed_event_ids, (str, bytes)):
            raise ValueError("changed_event_ids_must_be_collection")
        changed = set(changed_event_ids)
        if any(not isinstance(item, str) or not item for item in changed):
            raise ValueError("invalid_changed_event_id")
    before = source_hashes(cfg)
    try:
        with tempfile.TemporaryDirectory(prefix="s02e-recurring-targets-") as directory:
            return _build(cfg, Path(directory), changed)
    finally:
        if source_hashes(cfg) != before:
            raise RuntimeError("source_changed_during_recurring_target_generation")


def _build(cfg: TargetConfig, directory: Path, changed: set[str] | None) -> list[dict]:
    import bri_enums
    _, builder = _exporters()
    snapshot, projection = directory / "source.db", directory / "projection.db"
    with closing(readonly_connection(cfg.main_db)) as src, closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst)
    builder.build_flat(snapshot, projection, {})
    builder.build_tx_flat(snapshot, projection, {})
    builder.build_mf_flat(snapshot, projection, {})
    selected = {}
    with closing(readonly_connection(projection)) as conn:
        for table, domain in (("projects_flat", "main"), ("tx_flat", "TX"), ("mf_flat", "MF")):
            for record in conn.execute("SELECT * FROM " + table):
                flat = dict(record)
                # TX/MF belong exclusively to their dedicated projection policy.
                if domain == "main" and bri_enums.category_code(flat.get("category") or "") in {"TX", "MF"}:
                    continue
                pool, visibility = pool_for_projection(flat, domain)
                if pool:
                    selected[flat["project_id"]] = (pool, visibility, domain, flat)
    with closing(readonly_connection(snapshot)) as conn:
        events = [dict(row) for row in conn.execute("SELECT * FROM project_events ORDER BY id")]
    targets = []
    for event in events:
        project_id, event_id = event["canonical_id"], event["id"]
        if project_id not in selected or not builder.record_is_publishable(event):
            continue
        if changed is not None and event_id not in changed:
            continue
        year = _year(event)
        if year is not None and year < DISCOVERY_YEAR:
            continue
        pool, visibility, domain, flat = selected[project_id]
        context = load_context(SampleConfig(project_id=project_id, event_id=event_id, main_db=snapshot, raw_db=cfg.raw_db))
        # A publishable relation in the Chinese-participant table is the intake
        # decision; source quotes enrich it but are not a special-sentence gate.
        entities = [entity for entity in context.entities if builder.record_is_publishable(entity)]
        evidence = participation_evidence(context, builder.record_is_publishable)
        card = _card(context, evidence)
        targets.append({
            "pool": pool, "project_id": project_id, "event_id": event_id,
            "context": context.model_dump(), "card": card.model_dump(mode="json"),
            "participation_evidence": evidence,
            "participation_basis": "publishable_chinese_entity_relation" if entities else "project_projection_chinese_participant",
            "year": year, "visibility": visibility, "domain": domain,
            "category_code": bri_enums.category_code(context.project.get("category") or ""),
            "project_name": context.project.get("project_name"),
            "country_iso": context.project.get("country_iso"),
            "stage": event.get("project_stage"), "input_fingerprint": context.fingerprint(),
            "source_urls": _urls(event.get("source_links")),
            "candidates": candidate_list(context, []),
            "project_capacity_mw": flat.get("capacity_mw"),
        })
    return targets


def summarize_targets(targets: list[dict]) -> dict:
    """Count projects separately from event work units and public visibility."""
    return {
        "version": VERSION, "discovery_year": DISCOVERY_YEAR,
        "public_baseline_year": _exporters()[0].BASELINE_YEAR,
        "pools": {pool: {"projects": len({r["project_id"] for r in targets if r["pool"] == pool}),
                          "events": sum(r["pool"] == pool for r in targets)}
                  for pool in ("reference", "capacity_missing")},
        "visibility": {key: {"projects": len({r["project_id"] for r in targets if r["visibility"] == key}),
                             "events": sum(r["visibility"] == key for r in targets)}
                       for key in sorted({r["visibility"] for r in targets})},
        "domains": dict(Counter(r["domain"] for r in targets)),
    }
