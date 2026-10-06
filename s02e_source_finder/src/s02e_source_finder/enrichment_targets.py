"""Freeze reference and missing-generation-capacity targets without source writes.

Public capacity filtering remains in s03. This private discovery pool relaxes only
that field, and retains unknown identities and insufficient source evidence as
separate audit outcomes rather than silently declaring them valid projects.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ConfigDict

from .contracts import EventContext
from .sample import MODULE, ROOT, SampleConfig, candidate_list, file_hash, load_context, readonly_connection, write_json
from .structured import TargetCard
from .targets import TargetConfig, source_hashes

VERSION = "dual-enrichment-targets-v1"
SEED = "s02e-luna-capacity-20261005-v1"
QUOTAS = {"reference": 5, "capacity_missing": 5}
GENERATION_CODES = {"PV", "WD", "HY", "GT", "BM", "HB"}
HISTORICAL_FILES = (
    "pilot-v1/selected_events.json", "acceptance-v1/selection.json",
    "acceptance-v1/inputs-v1.json", "google-grounding-smoke-v1/manifest.json",
    "google-grounding-smoke-v1/results.json", "google-grounding-smoke-v1/resolved-links.json",
    "google-source-loop-v1/inputs-v1.json", "google-source-loop-v1/initial-six.json",
    "google-source-loop-v1/previous-preview-sample.json",
)
ACTION = re.compile(r"承建|承包|中标|签约|签署|供货|供应|提供|建设|施工|投资|收购|持股|开发|融资|贷款|运营|总包")
GENERATION = re.compile(r"光伏|风电|风力|水电|水力|地热|余热|生物质|垃圾焚烧发电|燃煤|发电站|电站")
UNKNOWN = {"", "unknown", "n/a", "null", "none", "未知", "不详"}


class EnrichmentConfig(TargetConfig):
    model_config = ConfigDict(extra="forbid")
    output_dir: Path = MODULE / "data/luna-capacity-pilot-20261005"


def normalized_url(url: str) -> str:
    """Collapse HTTP/HTTPS and fragments, retaining query identity."""
    parsed = urlsplit(url)
    return f"{(parsed.hostname or '').lower()}{parsed.path.rstrip('/')}?{parsed.query}"


def _json_urls(value: str | None) -> list[str]:
    result = json.loads(value or "[]")
    if not isinstance(result, list) or any(not isinstance(item, str) for item in result):
        raise ValueError("invalid_link_list")
    return list(dict.fromkeys(item for item in result if item))


def historical_seen(data_dir: Path = MODULE / "data") -> dict:
    """Freeze prior selected targets plus actually observed source/candidate URLs.

    Do not load old targets.json: that is the entire population, not a seen set.
    Cached pages/searches supplement the explicit prior selections.
    """
    paths = [data_dir / name for name in HISTORICAL_FILES]
    for folder in ("pilot-v1", "acceptance-v1", "google-grounding-smoke-v1", "google-source-loop-v1"):
        for subdir in ("pages", "searches"):
            paths.extend(sorted((data_dir / folder / subdir).glob("*.json")))
    events, projects, urls, hashes = set(), set(), set(), {}

    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get("event_id"), str):
                events.add(value["event_id"])
            if isinstance(value.get("project_id"), str):
                projects.add(value["project_id"])
            for key, item in value.items():
                if key == "contexts" and isinstance(item, dict):
                    events.update(item)
                if key in {"source_links", "official_website_links", "non_official_website_links"} and isinstance(item, str):
                    collect(_json_urls(item))
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str) and value.startswith(("http://", "https://")) and not any(ch.isspace() for ch in value):
            urls.add(normalized_url(value))

    for path in sorted(set(paths)):
        if not path.is_file():
            # Explicit historical manifests are required; absent cache folders are fine.
            raise FileNotFoundError(f"historical_manifest_missing:{path}")
        hashes[str(path.relative_to(ROOT))] = file_hash(path)
        collect(json.loads(path.read_text(encoding="utf-8")))
    return {"event_ids": sorted(events), "project_ids": sorted(projects), "normalized_urls": sorted(urls), "artifact_hashes": hashes}


def _aliases(entity: dict) -> list[str]:
    """Use source names and conservative lexical short forms; no ID requirement."""
    names = [str(entity.get(key) or "").strip() for key in (
        "raw_entity_name", "implementing_entity", "subsidiary_name", "entity_name",
        "implementing_entity_en", "subsidiary_name_en")]
    known = {"中国电力建设": "中国电建", "中国能源建设": "中国能建", "中国水利水电": "中国水电",
             "中国电力投资": "中电投", "国家电力投资": "国家电投", "中国机械工业": "国机"}
    for name in list(names):
        short = re.sub(r"(?:股份有限公司|有限责任公司|有限公司|股份公司|集团公司|集团)$", "", name)
        if len(short) >= 4:
            names.append(short)
        names.extend(alias for prefix, alias in known.items() if name.startswith(prefix))
    return sorted({name for name in names if len(name) >= 3 and name.lower() not in UNKNOWN}, key=lambda x: (-len(x), x))


def project_terms(project: dict) -> list[str]:
    """Prefer proper-name fragments after removing country and generic facilities."""
    name = str(project.get("project_name") or "")
    for word in (project.get("country") or "", "抽水蓄能", "机电设备改造", "一揽子", "微电网", "项目", "工程", "一期", "二期", "三期", "光伏", "风电", "水电", "发电", "电站", "太阳能", "垃圾焚烧", "生物质", "兆瓦", "综合能源", "分布式", "集中式", "储能", "制造", "基地", "屋顶", "电厂", "电网", "千吨级"):
        if word:
            name = name.replace(word, " ")
    name = re.sub(r"(?<![A-Za-z0-9])[0-9.]+\s*(?:MWp?|GW|MWh|GWh|吨/日)?", " ", name, flags=re.I)
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9-]{2,}", name)
    terms = set(parts)
    for part in parts:
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 4:
            terms.update(part[i:i+4] for i in range(len(part) - 3))
    return sorted(terms, key=lambda x: (-len(x), x))


def participation_evidence(context: EventContext, publishable) -> list[dict]:
    """Retain exact contiguous passages binding a named participant to this project.

    This is an auditable candidate-blind lexical gate, not a legal-identity or
    publication approval. A multi-project body cannot pass on a company elsewhere.
    """
    terms = project_terms(context.project)
    evidence = []
    for entity in context.entities:
        if not publishable(entity):
            continue
        for article in context.source_articles:
            # Sentence windows stay local; title may supply a proper-name anchor.
            content = article.get("content") or ""
            paragraphs = [part for part in re.split(r"\n+", content) if part.strip()]
            for paragraph in paragraphs:
                for offset in range(0, len(paragraph), 450):
                    text = paragraph[offset:offset + 700]
                    aliases = [name for name in _aliases(entity) if name.casefold() in text.casefold()]
                    anchors = [term for term in terms if term.casefold() in text.casefold()]
                    if not aliases or not anchors or not ACTION.search(text):
                        continue
                    # A bare equipment/service industry discussion has no actual facility.
                    facility = text + " " + str(article.get("title") or "")
                    if not re.search(r"光伏|风电|风力|水电|水力|地热|余热|生物质|发电|电站|储能|输电|输变电|变电站|制造|工厂|生产|基地", facility):
                        continue
                    evidence.append({"url": article["url"], "participant": entity.get("raw_entity_name") or entity.get("implementing_entity"),
                                     "matched_alias": aliases[0], "project_anchor": anchors[0], "quote": text,
                                     "paragraph_offset": offset, "identity_id_required": False,
                                     "entity_id": entity.get("entity_id"), "involvement_type": entity.get("involvement_type")})
                    break
            if evidence and evidence[-1]["participant"] == (entity.get("raw_entity_name") or entity.get("implementing_entity")):
                break
    return evidence


def pool_for_flat(row: dict, passes_baseline) -> tuple[str | None, list[str]]:
    """Relax only capacity; all other main public policy conditions are preserved."""
    import bri_enums
    category = bri_enums.category_code(row.get("category") or "")
    if category == "UNK" or not category:
        return None, ["unknown_category"]
    if category in {"TX", "MF"}:
        from s03_data_exporter.events_exporter import passes_txmf_baseline
        return ("reference", []) if passes_txmf_baseline(row) else (None, ["txmf_public_baseline_failed"])
    if category == "ES":
        return ("reference", []) if passes_baseline(row) else (None, ["storage_public_baseline_failed"])
    if category not in GENERATION_CODES:
        return None, ["outside_public_scope"]
    candidate = {**row, "capacity_mw": 1.0} if row.get("capacity_mw") is None else row
    if not passes_baseline(candidate):
        reasons = []
        if str(row.get("primary_entity") or "").strip().lower() in UNKNOWN:
            reasons.append("unknown_primary_participant")
        if (row.get("country_iso") or "").upper() in {"CHN", "HKG", "MAC", "TWN"}:
            reasons.append("excluded_country")
        if row.get("first_event_year") is None or row.get("last_event_year") is None or row.get("last_event_year", 0) < 2022:
            reasons.append("no_public_milestone_since_2022")
        return None, reasons or ["other_public_policy_condition"]
    return ("capacity_missing" if row.get("capacity_mw") is None else "reference"), []


def assign_components(rows: list[dict]) -> None:
    """Build URL/project components across all source events, including excluded ones."""
    parent = {row["event_id"]: row["event_id"] for row in rows}
    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key
    owners = {}
    for row in rows:
        keys = ["project:" + row["project_id"]]
        keys.extend("url:" + normalized_url(url) for url in row["source_urls"] + [c["url"] for c in row["candidates"]])
        for key in keys:
            if key in owners:
                a, b = sorted((find(row["event_id"]), find(owners[key])))
                parent[b] = a
            else:
                owners[key] = row["event_id"]
    for row in rows:
        row["leakage_group"] = find(row["event_id"])


def build_pools(cfg: EnrichmentConfig, temporary_dir: Path, seen: dict) -> tuple[list[dict], dict]:
    s03_src = ROOT / "s03_data_exporter/src"
    if str(s03_src) not in sys.path:
        sys.path.insert(0, str(s03_src))
    from s03_data_exporter import events_exporter, flat_builder
    import bri_enums
    snapshot, projection = temporary_dir / "source.db", temporary_dir / "projection.db"
    with closing(readonly_connection(cfg.main_db)) as src, closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst)
    projection_counts = {"projects_flat": flat_builder.build_flat(snapshot, projection, {}),
                         "tx_flat": flat_builder.build_tx_flat(snapshot, projection, {}),
                         "mf_flat": flat_builder.build_mf_flat(snapshot, projection, {})}
    with closing(readonly_connection(projection)) as conn:
        flats = {row["project_id"]: dict(row) for row in conn.execute("SELECT * FROM projects_flat")}
        for table, category in (("tx_flat", "输变电建设"), ("mf_flat", "新能源制造业")):
            flats.update({row["project_id"]: {**dict(row), "category": category} for row in conn.execute("SELECT * FROM " + table)})
    with closing(readonly_connection(snapshot)) as conn:
        projects = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM canonical_projects")}
        events = [dict(row) for row in conn.execute("SELECT * FROM project_events ORDER BY id")]
        counts = {table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("canonical_projects", "project_events", "chinese_entities")}
    rows = []
    for event in events:
        project = projects[event["canonical_id"]]
        row = {"project_id": project["id"], "event_id": event["id"], "domain": "main", "project_name": project.get("project_name"),
               "category": project.get("category"), "category_code": bri_enums.category_code(project.get("category") or ""),
               "country_iso": project.get("country_iso"), "stage": event.get("project_stage"),
               "source_urls": _json_urls(event.get("source_links")), "exclusion_reasons": [], "unknown_issues": []}
        context = EventContext(project_id=project["id"], event_id=event["id"], project=project, event=event, entities=[], source_articles=[])
        row["candidates"] = candidate_list(context, [])
        row["project_link_present"] = bool(row["candidates"])
        flat = flats.get(project["id"])
        if project.get("excluded"):
            row["pool"], row["exclusion_reasons"] = None, ["canonical_excluded"]
        elif flat is None:
            row["pool"], row["exclusion_reasons"] = None, ["no_publishable_project_projection"]
        else:
            row["pool"], row["exclusion_reasons"] = pool_for_flat(flat, events_exporter.passes_baseline)
        row["missing_goal_fields"] = (["capacity_mw"] if row["pool"] == "capacity_missing" else []) + (["external_reference"] if not row["candidates"] else [])
        row["project_capacity_mw"] = flat.get("capacity_mw") if flat else None
        if not flat_builder.record_is_publishable(event):
            row["exclusion_reasons"].append("event_pending_or_excluded")
        event_date = event.get("publish_date") or event.get("investment_date") or ""
        row["year"] = int(event_date[:4]) if event_date[:4].isdigit() else None
        if row["year"] is None or row["year"] < 2022:
            row["exclusion_reasons"].append("event_before_baseline_or_undated")
        if row["stage"] in {None, "", "Unknown"}:
            row["unknown_issues"].append("unknown_stage")
        rows.append(row)
    assign_components(rows)
    seen_events, seen_projects, seen_urls = set(seen["event_ids"]), set(seen["project_ids"]), set(seen["normalized_urls"])
    contaminated = {row["leakage_group"] for row in rows if row["event_id"] in seen_events or row["project_id"] in seen_projects or
                    any(normalized_url(url) in seen_urls for url in row["source_urls"] + [c["url"] for c in row["candidates"]])}
    for row in rows:
        if row["leakage_group"] in contaminated:
            row["exclusion_reasons"].append("prior_seen_connected_component")
        row["participation_evidence"] = []
        if row["pool"] and not row["exclusion_reasons"]:
            context = load_context(SampleConfig(project_id=row["project_id"], event_id=row["event_id"], main_db=cfg.main_db, raw_db=cfg.raw_db))
            row["participation_evidence"] = participation_evidence(context, flat_builder.record_is_publishable)
            if not row["participation_evidence"]:
                row["exclusion_reasons"].append("participation_evidence_insufficient")
            if row["category_code"] == "HB" and not any(GENERATION.search(item["quote"]) for item in row["participation_evidence"]):
                row["exclusion_reasons"].append("hybrid_generation_not_clear")
            if any(str(item.get("involvement_type") or "").lower() in UNKNOWN for item in context.entities):
                row["unknown_issues"].append("unknown_participation_role")
        row["eligible"] = row["pool"] is not None and not row["exclusion_reasons"]
    code = [Path(__file__), Path(flat_builder.__file__), Path(events_exporter.__file__), ROOT / "bri_enums.py", ROOT / "enums_mapping.json", Path(sys.modules[load_context.__module__].__file__)]
    return rows, {"projection_counts": projection_counts, "source_row_counts": counts,
                  "projection_source": "s03 build_flat/build_tx_flat/build_mf_flat from isolated SQLite backup of mode=ro main source; empty province map; no events CSV eligibility",
                  "code_hashes": {str(path.relative_to(ROOT)): file_hash(path) for path in code},
                  "source_snapshot_sha256": file_hash(snapshot), "projection_sha256": file_hash(projection)}


def select_targets(rows: list[dict]) -> list[dict]:
    selected, groups = [], set()
    for pool, quota in QUOTAS.items():
        chosen = []
        while len(chosen) < quota:
            candidates = [row for row in rows if row["pool"] == pool and row["eligible"] and row["leakage_group"] not in groups]
            if pool == "reference" and any(row["category_code"] in GENERATION_CODES for row in candidates):
                candidates = [row for row in candidates if row["category_code"] in GENERATION_CODES]
            if not candidates:
                raise ValueError(f"insufficient_unseen_projects:{pool}")
            def score(row):
                novelty = sum(weight for field, weight in (("category_code", 30), ("country_iso", 15), ("stage", 5)) if row[field] not in {item[field] for item in chosen})
                return -novelty, hashlib.sha256((SEED + row["event_id"]).encode()).hexdigest()
            row = dict(min(candidates, key=score))
            row["selection_reason"] = "fixed seeded selection; eligible exact local source passage; novel category/country/stage preferred; one connected component per project slot"
            row["selection_slot"] = len(selected) + len(chosen) + 1
            chosen.append(row)
            groups.add(row["leakage_group"])
        selected.extend(chosen)
    return selected


def target_card(context: EventContext, evidence: list[dict]) -> TargetCard:
    """Keep metadata dates as metadata; only explicit actual dates become intervals."""
    stage = context.event.get("project_stage") or "Unknown"
    start = end = None
    precision = "unknown"
    stage_ambiguous = True
    quotes = [{"url": item["url"], "quote": item["quote"], "kind": "project_participation"} for item in evidence]
    for item in evidence:
        quote = item["quote"]
        # No plan date, relative date, or publication date is promoted to an event day.
        if stage != "Unknown" and stage in quote and not re.search(r"计划|预计|拟于|将于", quote):
            match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", quote)
            if match:
                try:
                    start = end = date(*map(int, match.groups()))
                except ValueError:
                    continue
                precision, stage_ambiguous = "day", False
                break
    return TargetCard(project_id=context.project_id, event_id=context.event_id, input_fingerprint=context.fingerprint(),
                      stage=stage, scope_terms=[context.project.get("project_name") or context.project_id, *project_terms(context.project)[:4]],
                      time_start=start, time_end=end, time_precision=precision, stage_ambiguous=stage_ambiguous,
                      identity_conflicts=[], evidence=quotes,
                      notes="Machine-derived candidate-blind draft; human_confirmed=false. Source publication date is a search clue, not an event date. Exact source paragraphs retained; independent review required.")


def freeze_targets(cfg: EnrichmentConfig) -> dict:
    output, private = cfg.output_dir.resolve(), (MODULE / "data").resolve()
    if not output.is_relative_to(private):
        raise ValueError("output_must_be_under_module_data")
    names = ("targets.json", "selected-targets.json", "source-snapshot-manifest.json")
    if any((output / name).exists() for name in names):
        raise FileExistsError("frozen_enrichment_artifact_exists")
    before = source_hashes(cfg)
    seen = historical_seen()
    with tempfile.TemporaryDirectory(prefix=".enrichment-projection-", dir=private) as directory:
        rows, projection = build_pools(cfg, Path(directory), seen)
        selected = select_targets(rows)
        for row in selected:
            context = load_context(SampleConfig(project_id=row["project_id"], event_id=row["event_id"], main_db=cfg.main_db, raw_db=cfg.raw_db))
            row["context"], row["input_fingerprint"] = context.model_dump(), context.fingerprint()
            row["card"] = target_card(context, row["participation_evidence"]).model_dump(mode="json")
    after = source_hashes(cfg)
    if before != after:
        raise RuntimeError("source_changed_during_target_generation")
    common = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "source_hashes": before, "source_unchanged": True}
    counts = {pool: {"projects": len({row["project_id"] for row in rows if row["pool"] == pool}),
                     "events": sum(row["pool"] == pool for row in rows),
                     "eligible_projects": len({row["project_id"] for row in rows if row["pool"] == pool and row["eligible"]}),
                     "eligible_events": sum(row["pool"] == pool and row["eligible"] for row in rows)} for pool in QUOTAS}
    report = {**common, "summary": counts, "events": rows,
              "exclusion_counts": dict(Counter(reason for row in rows for reason in row["exclusion_reasons"])),
              "unknown_issue_counts": dict(Counter(reason for row in rows for reason in row["unknown_issues"])),
              "eligibility": "reference preserves main and TX/MF public baselines; capacity_missing relaxes only capacity_mw for generation; publishable events and source-tied Chinese participants; prior connected components excluded; lexical source-binding uncertainty is retained for review, not a project invalidity ruling"}
    selection = {**common, "selection_seed": SEED, "quotas": QUOTAS, "events": selected,
                 "contexts": {row["event_id"]: row["context"] for row in selected}, "cards": {row["event_id"]: row["card"] for row in selected},
                 "human_confirmed": False, "annotation_status": "machine-derived source-frozen cards; no search/model results used; no silent target replacement",
                 "summary": {pool: {"projects": sum(row["pool"] == pool for row in selected),
                                    "categories": dict(Counter(row["category_code"] for row in selected if row["pool"] == pool)),
                                    "countries": dict(Counter(row["country_iso"] for row in selected if row["pool"] == pool))} for pool in QUOTAS}}
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / names[0], report)
    write_json(output / names[1], selection)
    manifest = {**common, **projection, "source_hashes_before": before, "source_hashes_after": after, "historical_seen": seen,
                "source_invariance": "main/raw file SHA256 and any active WAL files identical before/after; temporary backups/projections deleted",
                "artifacts": {name: file_hash(output / name) for name in names[:2]}}
    write_json(output / names[2], manifest)
    return {"output_dir": str(output), "summary": counts, "selected": selection["summary"], "source_unchanged": True,
            "exclusion_counts": report["exclusion_counts"]}


def main():
    parser = argparse.ArgumentParser(description="Read-only dual enrichment target pools and frozen 5+5 pilot")
    parser.add_argument("--main-db", type=Path, default=EnrichmentConfig().main_db)
    parser.add_argument("--raw-db", type=Path, default=EnrichmentConfig().raw_db)
    parser.add_argument("--output-dir", type=Path, default=EnrichmentConfig().output_dir)
    print(json.dumps(freeze_targets(EnrichmentConfig.model_validate(vars(parser.parse_args()))), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
