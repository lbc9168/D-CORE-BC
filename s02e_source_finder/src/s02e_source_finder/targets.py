"""Build live s03 targets and freeze a small offline development/holdout pilot."""

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from .contracts import EventContext
from .sample import MODULE, ROOT, SampleConfig, candidate_list, file_hash, load_context, readonly_connection, write_json

VERSION = "live-s03-pilot-v1"
SEED = "s02e-pilot-v1-20260906"
SEEN_EVENT = "EGY-ES-0001-01"
SPLITS = {"development": {"main": 14, "TX": 3, "MF": 3}, "holdout": {"main": 6, "TX": 2, "MF": 2}}


class TargetConfig(BaseModel):
    """The only writable destination is the module's private data directory."""

    model_config = ConfigDict(extra="forbid")
    main_db: Path = ROOT / "s02a_duplicate_merger/data/db/bri_re_database.db"
    raw_db: Path = ROOT / "s01a_news_crawler/data/raw_news.db"
    output_dir: Path = MODULE / "data/pilot-v1"


def _urls(value: str | None) -> list[str]:
    urls = json.loads(value or "[]")
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise ValueError("invalid_source_links")
    return list(dict.fromkeys(url for url in urls if url))


def source_hashes(cfg: TargetConfig) -> dict[str, str]:
    """Include an active WAL, if any, in the read-only invariance evidence."""
    result = {}
    for path in (cfg.main_db, cfg.raw_db):
        path = path.resolve(strict=True)
        result[str(path)] = file_hash(path)
        wal = Path(str(path) + "-wal")
        if wal.exists():
            result[str(wal)] = file_hash(wal)
    return result


def build_live_targets(main_db: Path, temporary_dir: Path) -> tuple[list[dict], dict]:
    """Reuse s03 on a read-only source snapshot; never call its production runner.

    s03's existing builders open ordinary connections. A SQLite backup from our
    mode=ro connection isolates those functions from the production database.
    Province is irrelevant to eligibility, so an empty map avoids API/cache use.
    """
    s03_src = ROOT / "s03_data_exporter/src"
    if str(s03_src) not in sys.path:
        sys.path.insert(0, str(s03_src))
    from s03_data_exporter import events_exporter, flat_builder
    import bri_enums

    snapshot = temporary_dir / "source-snapshot.db"
    projection = temporary_dir / "projection.db"
    csv_path = temporary_dir / "events.csv"
    with closing(readonly_connection(main_db)) as src, closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst)
    counts = {
        "projects_flat": flat_builder.build_flat(snapshot, projection, {}),
        "tx_flat": flat_builder.build_tx_flat(snapshot, projection, {}),
        "mf_flat": flat_builder.build_mf_flat(snapshot, projection, {}),
    }
    counts["event_entity_rows"] = events_exporter.export_events_csv(snapshot, [csv_path], flat_db=projection)
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        exported = {(row["canonical_id"], row["event_id"]): row for row in csv.DictReader(stream)}
    with closing(readonly_connection(snapshot)) as conn:
        projects = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM canonical_projects")}
        events = {row["id"]: dict(row) for row in conn.execute("SELECT * FROM project_events")}
    stages = defaultdict(set)
    source_projects = defaultdict(set)
    for event in events.values():
        stages[event["canonical_id"]].add(event.get("project_stage") or "")
        for url in _urls(event.get("source_links")):
            source_projects[url].add(event["canonical_id"])
    targets = []
    for (project_id, event_id), row in sorted(exported.items()):
        project, event = projects[project_id], events[event_id]
        category_code = bri_enums.category_code(project.get("category") or "")
        context = EventContext(project_id=project_id, event_id=event_id, project=project, event=event, entities=[], source_articles=[])
        candidates = candidate_list(context, [])
        source_urls = _urls(event.get("source_links"))
        features = {
            "no_legacy_candidates": not candidates,
            "legacy_official_field": any(c["legacy_field"] == "official_website_links" for c in candidates),
            "legacy_non_official_field": any(c["legacy_field"] == "non_official_website_links" for c in candidates),
            "shared_source_multiple_projects": any(len(source_projects[url]) > 1 for url in source_urls),
            "project_multiple_stages": len(stages[project_id] - {""}) > 1,
            "previously_seen_development": event_id == SEEN_EVENT,
        }
        targets.append({"project_id": project_id, "event_id": event_id,
                        "domain": category_code if category_code in {"TX", "MF"} else "main",
                        "category": project.get("category"), "category_code": category_code,
                        "country_iso": project.get("country_iso"), "stage": event.get("project_stage"),
                        "year": int(row["year"]), "project_name": project.get("project_name"),
                        "source_urls": source_urls, "candidates": candidates, "features": features})
    if not targets:
        raise ValueError("live_s03_baseline_has_no_events")
    _assign_leakage_groups(targets)
    code_paths = [Path(flat_builder.__file__), Path(events_exporter.__file__), ROOT / "enums_mapping.json"]
    return targets, {"projection_counts": counts, "projection_code_hashes": {str(p.relative_to(ROOT)): file_hash(p) for p in code_paths},
                     "eligibility": "s03 build_flat/build_tx_flat/build_mf_flat + export_events_csv(flat_db), event year >= 2022",
                     "province_map": "empty; not an eligibility input"}


def _assign_leakage_groups(targets: list[dict]) -> None:
    """Keep connected projects/articles together, including indirect URL overlaps."""
    parents = {t["event_id"]: t["event_id"] for t in targets}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    owners = {}
    for target in targets:
        keys = ["project:" + target["project_id"]]
        keys.extend("url:" + url for url in target["source_urls"])
        keys.extend("url:" + c["url"] for c in target["candidates"])
        for key in keys:
            if key in owners:
                a, b = sorted((find(target["event_id"]), find(owners[key])))
                parents[b] = a
            else:
                owners[key] = target["event_id"]
    for target in targets:
        target["leakage_group"] = find(target["event_id"])


def summarize(targets: list[dict]) -> dict:
    """Count event associations separately from globally unique candidate URLs."""
    result = {}
    for domain in ("all", "main", "TX", "MF"):
        rows = [t for t in targets if domain == "all" or t["domain"] == domain]
        projects = {t["project_id"] for t in rows}
        linked = {t["project_id"] for t in rows if t["candidates"]}
        result[domain] = {"projects": len(projects), "events": len(rows),
                          "legacy_candidate_associations": sum(len(t["candidates"]) for t in rows),
                          "unique_legacy_urls": len({c["url"] for t in rows for c in t["candidates"]}),
                          "projects_without_legacy_candidates": len(projects - linked),
                          "events_without_legacy_candidates": sum(not t["candidates"] for t in rows)}
    return result


def select_events(targets: list[dict]) -> list[dict]:
    """Deterministic structural diversity sampling, with no guessed body labels."""
    if len({t["event_id"] for t in targets}) != len(targets):
        raise ValueError("duplicate_target_event")
    seen = next((t for t in targets if t["event_id"] == SEEN_EVENT), None)
    if seen is None:
        raise ValueError("previously_seen_event_missing_from_live_baseline")
    selected = []
    assigned_groups = {}
    selected_ids = set()
    for split, quotas in SPLITS.items():
        for domain, count in quotas.items():
            chosen = []

            def add(target):
                chosen.append(target)
                selected_ids.add(target["event_id"])
                assigned_groups[target["leakage_group"]] = split

            if split == "development" and domain == "main":
                add(seen)
            while len(chosen) < count:
                pool = [t for t in targets if t["domain"] == domain and t["event_id"] not in selected_ids
                        and assigned_groups.get(t["leakage_group"], split) == split]
                if not pool:
                    raise ValueError(f"insufficient_isolated_targets:{split}:{domain}")
                covered = {key for t in chosen for key, value in t["features"].items() if value}
                # The last development-main slot exercises a second milestone
                # from an already selected project when such a target exists.
                if split == "development" and domain == "main" and len(chosen) == count - 1:
                    pairs = [t for t in pool if any(t["project_id"] == c["project_id"] and t["stage"] != c["stage"] for c in chosen)]
                    if pairs:
                        pool = pairs

                def score(target):
                    novelty = sum(25 for key, value in target["features"].items() if value and key not in covered and key != "previously_seen_development")
                    for field, weight in (("category_code", 15), ("stage", 8), ("country_iso", 3), ("year", 2), ("project_id", 1)):
                        if target[field] not in {t[field] for t in chosen}:
                            novelty += weight
                    tie = hashlib.sha256((SEED + target["event_id"]).encode()).hexdigest()
                    return -novelty, tie

                add(min(pool, key=score))
            selected.extend({**t, "split": split, "manual_label": None} for t in chosen)
    return selected


def freeze_pilot(cfg: TargetConfig) -> dict:
    """Create two new private artifacts and fail closed on source changes."""
    output = cfg.output_dir.resolve()
    private_data = (MODULE / "data").resolve()
    if not output.is_relative_to(private_data):
        raise ValueError("output_must_be_under_module_data")
    paths = [output / "targets.json", output / "selected_events.json"]
    if any(path.exists() for path in paths):
        raise FileExistsError("pilot_artifact_already_exists; choose a new output directory")
    before = source_hashes(cfg)
    output.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".live-projection-", dir=private_data) as tmp:
            targets, projection = build_live_targets(cfg.main_db, Path(tmp))
            selected = select_events(targets)
            for target in selected:
                context = load_context(SampleConfig(project_id=target["project_id"], event_id=target["event_id"], main_db=cfg.main_db, raw_db=cfg.raw_db))
                target["context"] = context.model_dump()
                target["input_fingerprint"] = context.fingerprint()
    finally:
        if source_hashes(cfg) != before:
            raise RuntimeError("source_changed_during_target_generation")
    common = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
              "source_hashes": before, "source_unchanged": True, **projection}
    target_report = {**common, "summary": summarize(targets), "events": targets}
    selection_report = {**common, "selection_seed": SEED, "split_quotas": SPLITS,
                        "summary": summarize(selected), "events": selected,
                        "coverage": {split: dict(Counter(key for t in selected if t["split"] == split for key, value in t["features"].items() if value)) for split in SPLITS},
                        "coverage_requires_body_review": ["legacy_positive", "wrong_project_or_stage", "access_failure"],
                        "annotation_status": "unlabelled; structural features are sampling clues, not verification results",
                        "split_isolation": "connected components of project IDs, raw-source URLs and legacy-candidate URLs"}
    write_json(paths[0], target_report)
    write_json(paths[1], selection_report)
    return {"targets_path": str(paths[0]), "selected_events_path": str(paths[1]),
            "target_summary": target_report["summary"], "selected_summary": selection_report["summary"],
            "source_unchanged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze live s03 targets and a 30-event offline pilot")
    parser.add_argument("--output-dir", type=Path, default=MODULE / "data/pilot-v1")
    parser.add_argument("--main-db", type=Path, default=TargetConfig().main_db)
    parser.add_argument("--raw-db", type=Path, default=TargetConfig().raw_db)
    print(json.dumps(freeze_pilot(TargetConfig.model_validate(vars(parser.parse_args()))), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
