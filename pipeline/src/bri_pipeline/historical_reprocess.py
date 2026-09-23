"""Explicit, conservative reconciliation of an isolated historical candidate.

No extraction, fuzzy matching or publication approval is performed here. Plans
cover one source article / one canonical event and all its enterprise relations;
multi-source merges and human decisions require separate review. Apply temporarily uses DELETE journals during the offline maintenance window
for SQLite's attached-database atomic commit, then restores original modes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RelationMapping(StrictModel):
    source_id: int | None = None
    candidate_id: int = Field(gt=0)
    insert_id: int | None = Field(default=None, gt=0)


class MappingSpec(StrictModel):
    """Operator-reviewed one-to-one identifiers; no mapping is inferred."""
    version: Literal[1] = 1
    article_url: str
    source_extracted_id: int = Field(gt=0)
    candidate_extracted_id: int = Field(gt=0)
    source_canonical_id: str = Field(min_length=1)
    candidate_canonical_id: str = Field(min_length=1)
    source_event_id: str = Field(min_length=1)
    candidate_event_id: str = Field(min_length=1)
    relations: list[RelationMapping] = Field(min_length=1)
    evidence_review_note: str = Field(min_length=1)


CAPACITY = set("capacity_mw_raw capacity_mw capacity_details storage_power_mw storage_energy_mwh generation_sources_json storage_status storage_power_mw_raw storage_energy_mwh_raw energy_composition_evidence voltage_kv_raw voltage_kv line_length_km_raw line_length_km transmission_capacity_mw_raw transmission_capacity_mw production_capacity_raw production_capacity_value production_capacity_unit capacity_source pv_capacity_mw".split())
FACTS = CAPACITY | set("project_stage project_developer project_developer_en planned_start_date planned_complete_date total_investment_usd_raw total_investment_usd contract_amount_usd_raw contract_amount_usd financing_amount_usd_raw financing_amount_usd investment_date financing_model financing_model_details project_summary".split())
IDENTITY = set("implementing_entity implementing_entity_en subsidiary_name subsidiary_name_en involvement_type is_central_soe entity_id entity_name group_entity_id raw_entity_name identity_status entity_evidence_json".split())
NUMERIC = set("capacity_mw storage_power_mw storage_energy_mwh voltage_kv line_length_km transmission_capacity_mw production_capacity_value pv_capacity_mw total_investment_usd contract_amount_usd financing_amount_usd".split())
DERIVED_NAMES = {"implementing_entity_en", "subsidiary_name_en"}
ALLOWED = {
    "extracted_projects": FACTS | IDENTITY | set("project_name project_name_en country country_iso category project_developer_evidence subcontract_amount_usd_raw subcontract_amount_usd publication_status excluded exclude_reason exclude_source needs_review".split()),
    "canonical_projects": set("project_name project_name_en category subcategory technology_tags_json excluded excluded_reason".split()),
    "project_events": FACTS,
    "chinese_entities": IDENTITY | {"subcontract_amount_raw", "subcontract_amount"},
    "project_classification_shadow": set("generation_sources_json storage_status storage_power_mw storage_energy_mwh proposed_category technology_tags_json classification_version needs_review review_reason classification_source".split()),
}


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row(conn, table, value):
    key = "canonical_id" if table == "project_classification_shadow" else "id"
    row = conn.execute(f'SELECT * FROM "{table}" WHERE "{key}"=?', (value,)).fetchone()
    return dict(row) if row else None


def _snapshot(conn, schema="main", replacements=()):
    """Schema plus every row makes staleness and non-target changes observable."""
    digest = hashlib.sha256()
    tables = conn.execute(f"SELECT name,sql FROM {schema}.sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for name, sql in tables:
        digest.update(json.dumps([name, sql], ensure_ascii=False).encode())
        cursor = conn.execute('SELECT * FROM ' + schema + '."' + name.replace('"', '""') + '"')
        columns = [c[0] for c in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor]
        for change in replacements:
            if change["table"] != name:
                continue
            key = "canonical_id" if name == "project_classification_shadow" else "id"
            records = [row for row in records if row[key] != change["key"]]
            records.append(change["after"])
        # SQLite updates the AUTOINCREMENT counter on an explicit relation insert.
        if name == "sqlite_sequence":
            for change in replacements:
                if change["before"] is None:
                    found = False
                    for row in records:
                        if row["name"] == change["table"]:
                            found = True
                            row["seq"] = max(row["seq"], change["key"])
                    if not found:
                        records.append({"name": change["table"], "seq": change["key"]})
        rows = sorted(json.dumps([row.get(c) for c in columns], ensure_ascii=False) for row in records)
        digest.update(json.dumps(rows, ensure_ascii=False).encode())
    return digest.hexdigest()


def _manual(row):
    if not row:
        return False
    reason = str(row.get("excluded_reason") or row.get("exclude_reason") or "").lower()
    return bool(row.get("manual_locked")) or any(token in reason for token in ("人工", "手工", "manual", "human")) or any(row.get(k) in ("manual_verified", "official", "manual", "human") for k in ("name_en_quality", "entity_en_quality", "exclude_source", "classification_source"))


def _same_fact(a, b, *, numeric=False):
    """S01B null/empty and S02A numeric text are equivalent representations."""
    a = None if a is None or (isinstance(a, str) and not a.strip()) else a
    b = None if b is None or (isinstance(b, str) and not b.strip()) else b
    if a is None or b is None:
        return a is b
    if numeric:
        try:
            left, right = Decimal(str(a)), Decimal(str(b))
        except InvalidOperation:
            return False
        return left.is_finite() and right.is_finite() and left == right
    return a == b


def _stored_row(conn, table, values):
    """Use SQLite itself to project source-column affinity and insert defaults.

    Historical extraction schemas may declare capacity as TEXT while current
    candidates use REAL. The reviewed after-row must be the actual persisted
    representation, without weakening exact target/non-target verification.
    """
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.row_factory = sqlite3.Row
        scratch.execute(schema)
        columns = list(values)
        scratch.execute(f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', [values[c] for c in columns])
        return dict(scratch.execute(f'SELECT * FROM "{table}"').fetchone())


def build_plan(source_extracted, source_main, candidate_extracted, candidate_main, mapping: MappingSpec | dict) -> dict:
    """Read-only exact diff; a review plan cannot be applied."""
    spec = mapping if isinstance(mapping, MappingSpec) else MappingSpec.model_validate(mapping)
    HttpUrl(spec.article_url)
    paths = {"source_extracted": str(Path(source_extracted).resolve()), "source_main": str(Path(source_main).resolve()), "candidate_extracted": str(Path(candidate_extracted).resolve()), "candidate_main": str(Path(candidate_main).resolve())}
    if len(set(paths.values())) != 4:
        raise ValueError("Source and candidate databases must be four distinct files")
    filenames = list(paths.values())
    if any(Path(a).samefile(b) for i, a in enumerate(filenames) for b in filenames[i + 1:]):
        raise ValueError("Source and candidate database files overlap")
    plan = {"version": 1, "paths": paths, "mapping": spec.model_dump(), "status": "ready", "reasons": [], "warnings": [], "limitations": [], "changes": [], "snapshots": {}}
    if len(spec.relations) != 1:
        plan["reasons"].append("Multiple enterprise relations require a multi-row review")
    with closing(_connect(source_extracted)) as se, closing(_connect(source_main)) as sm, closing(_connect(candidate_extracted)) as ce, closing(_connect(candidate_main)) as cm:
        for label, conn in (("source_extracted", se), ("source_main", sm), ("candidate_extracted", ce), ("candidate_main", cm)):
            plan["snapshots"][label] = _snapshot(conn)
        pairs = [("extracted", "extracted_projects", _row(se, "extracted_projects", spec.source_extracted_id), _row(ce, "extracted_projects", spec.candidate_extracted_id)), ("main", "canonical_projects", _row(sm, "canonical_projects", spec.source_canonical_id), _row(cm, "canonical_projects", spec.candidate_canonical_id)), ("main", "project_events", _row(sm, "project_events", spec.source_event_id), _row(cm, "project_events", spec.candidate_event_id))]
        if any(a is None or b is None for _, _, a, b in pairs):
            plan["reasons"].append("Missing mapped project/event/extracted row")
        else:
            for conn, eid, cid in ((sm, spec.source_event_id, spec.source_canonical_id), (cm, spec.candidate_event_id, spec.candidate_canonical_id)):
                event = _row(conn, "project_events", eid)
                if event["canonical_id"] != cid or json.loads(event.get("source_links") or "[]") != [spec.article_url]:
                    plan["reasons"].append("Event is not an exact single-article canonical match")
                if conn.execute("SELECT COUNT(*) FROM project_events WHERE canonical_id=?", (cid,)).fetchone()[0] != 1:
                    plan["reasons"].append("Multi-event canonical requires review")
            for conn, row_id in ((se, spec.source_extracted_id), (ce, spec.candidate_extracted_id)):
                row = _row(conn, "extracted_projects", row_id)
                if row.get("source_link") != spec.article_url or conn.execute("SELECT COUNT(*) FROM extracted_projects WHERE source_link=?", (spec.article_url,)).fetchone()[0] != 1:
                    plan["reasons"].append("Article split/multiple extracted rows requires review")
            candidate = pairs[0][3]
            publication = candidate.get("publication_status")
            if publication not in (None, "", "eligible") or candidate.get("excluded") or (candidate.get("needs_review") and publication != "eligible"):
                plan["reasons"].append("Candidate has not passed extraction publication gate")
            if "publication_status" not in candidate:
                plan["limitations"].append("Legacy candidate lacks explicit publication_status; operator review note and legacy exclusion/review flags govern eligibility")
            evidence = candidate.get("entity_evidence_json")
            if evidence:
                try:
                    parsed_evidence = json.loads(evidence)
                except (json.JSONDecodeError, TypeError):
                    plan["reasons"].append("Candidate entity audit evidence is not valid JSON")
                else:
                    if not isinstance(parsed_evidence, dict) or parsed_evidence.get("structure_validation_errors"):
                        plan["reasons"].append("Candidate has unresolved core structure errors")
            if pairs[1][2].get("country_iso") != pairs[1][3].get("country_iso"):
                plan["reasons"].append("Country identity changed")
            for field in sorted(FACTS & candidate.keys() & pairs[2][3].keys()):
                if not _same_fact(candidate[field], pairs[2][3][field], numeric=field in NUMERIC):
                    if field.endswith("_raw"):
                        plan["warnings"].append({"kind": "raw_text_normalized", "field": field, "extracted": candidate[field], "event": pairs[2][3][field]})
                    else:
                        plan["reasons"].append(f"Candidate extraction/event disagree: {field}")
        for conn, cid in ((sm, spec.source_canonical_id), (cm, spec.candidate_canonical_id)):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='project_classification_shadow'").fetchone() is None:
                plan["reasons"].append("Classification shadow missing; requires schema review")
                break
        else:
            a = _row(sm, "project_classification_shadow", spec.source_canonical_id)
            b = _row(cm, "project_classification_shadow", spec.candidate_canonical_id)
            if (a is None) != (b is None):
                plan["reasons"].append("Classification shadow insert/removal requires review")
            elif a:
                pairs.append(("main", "project_classification_shadow", a, b))
        old_ids = [r.source_id for r in spec.relations if r.source_id is not None]
        new_ids = [r.candidate_id for r in spec.relations]
        insert_ids = [r.insert_id for r in spec.relations if r.source_id is None]
        if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids) or None in insert_ids or len(set(insert_ids)) != len(insert_ids):
            plan["reasons"].append("Relations must have unique explicit old/new IDs and insert IDs")
        if set(old_ids) != {r[0] for r in sm.execute("SELECT id FROM chinese_entities WHERE event_id=?", (spec.source_event_id,))} or set(new_ids) != {r[0] for r in cm.execute("SELECT id FROM chinese_entities WHERE event_id=?", (spec.candidate_event_id,))}:
            plan["reasons"].append("Mapping must cover all event relations without deletion")
        for relation in spec.relations:
            a = _row(sm, "chinese_entities", relation.source_id) if relation.source_id else None
            b = _row(cm, "chinese_entities", relation.candidate_id)
            if b is None or (relation.source_id and a is None):
                plan["reasons"].append("Missing mapped relation")
                continue
            if (a and a["event_id"] != spec.source_event_id) or b["event_id"] != spec.candidate_event_id:
                plan["reasons"].append("Relation belongs to another event")
            extracted_candidate = pairs[0][3]
            if extracted_candidate:
                for field in sorted(IDENTITY & extracted_candidate.keys() & b.keys()):
                    if _same_fact(extracted_candidate[field], b[field]):
                        continue
                    if field in DERIVED_NAMES:
                        plan["warnings"].append({"kind": "derived_english_name", "field": field, "extracted": extracted_candidate[field], "relation": b[field]})
                    else:
                        plan["reasons"].append("Candidate extraction/entity relation disagree")
            if a is None:
                if relation.insert_id is None or _row(sm, "chinese_entities", relation.insert_id):
                    plan["reasons"].append("Insert ID missing or already exists")
                    continue
                after = {k: v for k, v in b.items() if k in ALLOWED["chinese_entities"]}
                after.update(id=relation.insert_id, event_id=spec.source_event_id)
                if "entity_en_quality" in b:
                    after["entity_en_quality"] = "ai_translated"
                source_columns = {row[1] for row in sm.execute("PRAGMA table_info(chinese_entities)")}
                missing = sorted(after.keys() - source_columns)
                if missing:
                    plan["reasons"].append(f"schema_needs_migration: main.chinese_entities missing candidate fields: {', '.join(missing)}")
                    continue
                after = _stored_row(sm, "chinese_entities", after)
                plan["changes"].append({"database": "main", "table": "chinese_entities", "key": relation.insert_id, "before": None, "after": after})
            else:
                pairs.append(("main", "chinese_entities", a, b))
        for database, table, before, candidate in pairs:
            if before is None or candidate is None:
                continue
            if _manual(before):
                plan["reasons"].append(f"Human-protected {table} row requires review")
            missing = sorted((ALLOWED[table] & candidate.keys()) - before.keys())
            if missing:
                plan["reasons"].append(f"schema_needs_migration: {database}.{table} missing candidate fields: {', '.join(missing)}")
            for column, value in candidate.items():
                if column in ALLOWED[table] and isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                    plan["reasons"].append(f"Invalid numeric candidate value: {table}.{column}")
            after = dict(before)
            for column in ALLOWED[table] & before.keys() & candidate.keys():
                after[column] = candidate[column]
            after = _stored_row(se if database == "extracted" else sm, table, after)
            if after != before:
                key = before.get("id", before.get("canonical_id"))
                plan["changes"].append({"database": database, "table": table, "key": key, "before": before, "after": after})
        if plan["reasons"]:
            plan["status"] = "requires_review"
    return plan


def _backup(path, destination):
    with destination.open("xb"):
        pass
    with closing(_connect(path)) as source, closing(sqlite3.connect(destination)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or _snapshot(source) != _snapshot(target):
            raise RuntimeError("Backup verification failed")


@contextmanager
def _rollback_journals(paths):
    """Offline maintenance: fail on active connections, restore previous modes."""
    original = {}
    try:
        for label in ("source_extracted", "source_main"):
            with closing(sqlite3.connect(paths[label], timeout=1)) as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                original[label] = mode
                if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                    raise RuntimeError("Cannot enter offline DELETE journal maintenance")
        yield
    finally:
        for label, mode in original.items():
            if mode not in {"delete", "wal", "truncate", "persist", "memory", "off"}:
                raise RuntimeError("Unexpected original journal mode")
            with closing(sqlite3.connect(paths[label], timeout=1)) as conn:
                if conn.execute("PRAGMA journal_mode=" + mode).fetchone()[0].lower() != mode:
                    raise RuntimeError("Could not restore original journal mode")


def apply_plan(plan: dict, backup_dir: str | Path) -> dict:
    """Rebuild the diff from immutable candidates; never execute supplied SQL/columns."""
    if plan.get("version") != 1 or plan.get("status") != "ready":
        raise ValueError("Only ready version-1 plans may be applied")
    paths = plan["paths"]
    backup_dir = Path(backup_dir).resolve()
    receipt = backup_dir / "receipt.json"
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    if receipt.exists():
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        if saved["plan_hash"] != plan_hash:
            raise ValueError("Backup directory belongs to another plan")
        for label in ("source_extracted", "source_main"):
            with closing(_connect(paths[label])) as conn:
                if _snapshot(conn) != saved["after"][label]:
                    raise ValueError("Applied databases changed after receipt")
        return {"status": "already_applied", "changes": 0}
    rebuilt = build_plan(**paths, mapping=plan["mapping"])
    if rebuilt != plan:
        raise ValueError("Stale or modified plan; regenerate and review")
    backup_dir.mkdir(parents=True, exist_ok=False)
    _backup(paths["source_extracted"], backup_dir / "projects.db")
    _backup(paths["source_main"], backup_dir / "main.db")
    expected = {}
    for label, database in (("source_extracted", "extracted"), ("source_main", "main")):
        with closing(_connect(paths[label])) as conn:
            expected[database] = _snapshot(conn, replacements=[c for c in plan["changes"] if c["database"] == database])
    with _rollback_journals(paths):
        with closing(sqlite3.connect(paths["source_main"])) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("ATTACH DATABASE ? AS extracted", (paths["source_extracted"],))
            conn.execute("BEGIN IMMEDIATE")
            # Acquire both write locks before verifying snapshots again.
            conn.execute("UPDATE extracted.extracted_projects SET id=id WHERE 0")
            for label in ("source_extracted", "source_main"):
                with closing(_connect(paths[label])) as check:
                    if _snapshot(check) != plan["snapshots"][label]:
                        raise ValueError("Source changed while preparing backups")
            for change in plan["changes"]:
                table, database = change["table"], change["database"]
                before, after = change["before"], change["after"]
                if before is None:
                    columns = list(after)
                    conn.execute(f'INSERT INTO {database}.{table} ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})', [after[c] for c in columns])
                else:
                    columns = [c for c in after if before[c] != after[c]]
                    key = "canonical_id" if table == "project_classification_shadow" else "id"
                    conn.execute(f'UPDATE {database}.{table} SET {",".join(c + "=?" for c in columns)} WHERE {key}=?', [after[c] for c in columns] + [change["key"]])
            for database in ("main", "extracted"):
                if conn.execute(f"PRAGMA {database}.foreign_key_check").fetchall() or conn.execute(f"PRAGMA {database}.integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Integrity/foreign-key validation failed; rolled back")
                if _snapshot(conn, schema=database) != expected[database]:
                    raise RuntimeError("Unexpected target/non-target changes; rolled back")
    after = {}
    for label in ("source_extracted", "source_main"):
        with closing(_connect(paths[label])) as conn:
            after[label] = _snapshot(conn)
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump({"plan_hash": plan_hash, "after": after, "changes": plan["changes"]}, handle, ensure_ascii=False, indent=2)
    return {"status": "applied", "changes": len(plan["changes"]), "backup_dir": str(backup_dir)}


class CoordinationDecision(StrictModel):
    """Explicit operator identity decision; not a new extraction or model verdict."""
    task_id: str = Field(min_length=1)
    decision: Literal["existing", "distinct"]
    canonical_id: str | None = None
    reviewer: str = Field(min_length=1)
    evidence_review_note: str = Field(min_length=10)


def build_coordination_plan(source_extracted, source_main, decision: dict) -> dict:
    """Read-only targeted release plan. Source and historical facts must be unchanged."""
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger
    spec = CoordinationDecision.model_validate(decision)
    if (spec.decision == "existing") != bool(spec.canonical_id):
        raise ValueError("Existing requires canonical_id; distinct must not specify one")
    paths = {"source_extracted": str(Path(source_extracted).resolve()), "source_main": str(Path(source_main).resolve())}
    if Path(source_extracted).samefile(source_main):
        raise ValueError("Source and main files overlap")
    with closing(_connect(source_extracted)) as se, closing(_connect(source_main)) as sm:
        task = se.execute("SELECT * FROM s02a_coordination WHERE task_id=?", (spec.task_id,)).fetchone()
        if not task or task["status"] != "pending":
            raise ValueError("Only pending tasks can be planned")
        project = json.loads(task["project_json"])
        saved = json.loads(task["source_rows_json"])
        ids = {r["id"] for r in saved}
        membership = {r[0] for r in se.execute("SELECT source_id FROM s02a_coordination_sources WHERE task_id=?", (spec.task_id,))}
        if not ids or membership != ids or ids != set(project["_source_record_ids"]):
            raise ValueError("Incomplete coordination source membership")
        for row in saved:
            current = _row(se, "extracted_projects", row["id"])
            if current != row or current["imported_to_s02a"]:
                raise ValueError("Held source changed; re-review rather than resetting import flags")
            if merger._pending_publication(current) or merger._excluded_publication(current):
                raise ValueError("Identity resolution cannot bypass participation eligibility")
        cid = spec.canonical_id
        canonical = _row(sm, "canonical_projects", cid) if cid else None
        if cid and (not canonical or canonical["country_iso"] != project["country_iso"]):
            raise ValueError("Canonical missing or country mismatch")
        if canonical and (canonical.get("excluded") or _manual(canonical)):
            raise ValueError("Excluded or manually locked canonical needs separate historical review")
        if not cid:
            cid = merger._gen_canonical_id(sm, project["country_iso"], merger._get_cat_code(project["category"]))
        event = sm.execute("SELECT * FROM project_events WHERE canonical_id=? AND project_stage=?", (cid, project["project_stage"])).fetchone()
        if event and (_manual(dict(event)) or event["verification_status"] == "manual_verified"):
            raise ValueError("Manually verified event needs separate historical review")
        eid = event["id"] if event else merger._gen_event_id(sm, cid)
        return {"version": 1, "kind": "s02a_identity_resolution", "status": "ready", "paths": paths,
                "decision": spec.model_dump(), "task": dict(task),
                "snapshots": {"source_extracted": _snapshot(se), "source_main": _snapshot(sm)},
                "preview": {"canonical_id": cid, "canonical_action": "reuse" if canonical else "create",
                            "event_id": eid, "event_action": "append_sources_fill_nulls" if event else "create",
                            "source_row_ids": sorted(ids), "incoming_project": project,
                            "historical_canonical": canonical, "historical_event": dict(event) if event else None,
                            "note": "No model calls. Existing event nonempty facts retained; energy taxonomy refresh and relation identity enrichment follow normal writer. No old ID reassignment."}}


def list_coordination(source_extracted) -> dict:
    """Read-only review inventory; retain model evidence separately from operator decisions."""
    with closing(_connect(source_extracted)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='s02a_coordination'").fetchone():
            return {"tasks": [], "pending": 0}
        tasks = []
        for row in conn.execute("SELECT * FROM s02a_coordination ORDER BY created_at, task_id"):
            task = dict(row)
            for column in ("project_json", "source_rows_json", "evidence_json", "resolution_json"):
                task[column.removesuffix("_json")] = json.loads(task.pop(column)) if row[column] else None
            tasks.append(task)
        return {"tasks": tasks, "pending": sum(t["status"] == "pending" for t in tasks)}


def apply_coordination_plan(plan: dict, backup_dir: str | Path) -> dict:
    """Apply only the reviewed task with backups and an atomic import/resolve commit."""
    from s02a_duplicate_merger.tools import coordination, duplicate_merger_tool as merger
    if plan.get("kind") != "s02a_identity_resolution" or plan.get("status") != "ready" or plan.get("version") != 1:
        raise ValueError("Not a ready identity resolution plan")
    paths = plan["paths"]
    spec = CoordinationDecision.model_validate(plan["decision"])
    plan_hash = hashlib.sha256(coordination.dumps(plan).encode()).hexdigest()
    # Database receipt, committed with the data, also covers a crash before file receipt.
    with closing(_connect(paths["source_extracted"])) as se:
        task = se.execute("SELECT * FROM s02a_coordination WHERE task_id=?", (spec.task_id,)).fetchone()
        if task and task["status"] == "resolved":
            resolution = json.loads(task["resolution_json"])
            if resolution["plan_hash"] != plan_hash:
                raise ValueError("Task already resolved by a different plan")
            for row_id in resolution["source_row_ids"]:
                if _row(se, "extracted_projects", row_id)["imported_to_s02a"] != 1:
                    raise ValueError("Resolved source import flag changed")
            with closing(_connect(paths["source_main"])) as sm:
                event = _row(sm, "project_events", resolution["event_id"])
                if not event or event["canonical_id"] != resolution["canonical_id"]:
                    raise ValueError("Resolved event changed or missing")
            return {"status": "already_applied", **resolution}
    rebuilt = build_coordination_plan(**paths, decision=spec.model_dump())
    if rebuilt != plan:
        raise ValueError("Stale or modified identity plan; regenerate and review")
    backup_dir = Path(backup_dir).resolve()
    backup_dir.mkdir(parents=True, exist_ok=False)
    for label, filename in (("source_extracted", "projects.db"), ("source_main", "main.db")):
        _backup(paths[label], backup_dir / filename)
    project = json.loads(plan["task"]["project_json"])
    result = {"plan_hash": plan_hash, "decision": spec.model_dump(),
              "canonical_id": plan["preview"]["canonical_id"], "event_id": plan["preview"]["event_id"],
              "source_row_ids": plan["preview"]["source_row_ids"], "backup_dir": str(backup_dir)}
    with coordination.attached_transaction(paths["source_extracted"], paths["source_main"]) as conn:
        for label, schema in (("source_extracted", "extracted"), ("source_main", "main")):
            if _snapshot(conn, schema) != plan["snapshots"][label]:
                raise ValueError("Data changed while creating backups")
        result["counts"] = merger._write_to_db(conn, [project], commit=False,
            resolved_canonical_id=spec.canonical_id, resolved_distinct=spec.decision == "distinct")
        conn.executemany("UPDATE extracted.extracted_projects SET imported_to_s02a=1 WHERE id=?", [(i,) for i in result["source_row_ids"]])
        conn.execute("UPDATE extracted.s02a_coordination SET status='resolved', resolution_json=?, resolved_at=CURRENT_TIMESTAMP WHERE task_id=?",
                     (coordination.dumps(result), spec.task_id))
        event = conn.execute("SELECT canonical_id,source_links FROM project_events WHERE id=?", (result["event_id"],)).fetchone()
        if not event or event[0] != result["canonical_id"] or not set(project["source_links"]).issubset(json.loads(event[1])):
            raise RuntimeError("Resolution ID/source verification failed")
        for schema in ("main", "extracted"):
            if conn.execute(f"PRAGMA {schema}.foreign_key_check").fetchall() or conn.execute(f"PRAGMA {schema}.integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Resolution integrity check failed")
    (backup_dir / "receipt.json").write_text(coordination.dumps(result), encoding="utf-8")
    return {"status": "applied", **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan")
    for flag in ("source-extracted", "source-main", "candidate-extracted", "candidate-main", "mapping", "out"):
        prepare.add_argument("--" + flag, required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--backup-dir", required=True)
    identity = commands.add_parser("coordination-plan", help="Plan an operator-reviewed pending identity release")
    for flag in ("source-extracted", "source-main", "decision", "out"):
        identity.add_argument("--" + flag, required=True)
    release = commands.add_parser("coordination-apply", help="Apply exactly one reviewed identity plan")
    release.add_argument("--plan", required=True)
    release.add_argument("--backup-dir", required=True)
    inventory = commands.add_parser("coordination-list", help="Read saved identity evidence without running extraction")
    inventory.add_argument("--source-extracted", required=True)
    inventory.add_argument("--out", required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "coordination-list":
        result = list_coordination(args["source_extracted"])
        with Path(args["out"]).open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"pending": result["pending"]}))
    elif command == "coordination-plan":
        output = Path(args.pop("out"))
        decision = json.loads(Path(args.pop("decision")).read_text(encoding="utf-8"))
        result = build_coordination_plan(**args, decision=decision)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"status": result["status"], "preview": result["preview"]}, ensure_ascii=False))
    elif command == "coordination-apply":
        print(json.dumps(apply_coordination_plan(json.loads(Path(args["plan"]).read_text(encoding="utf-8")), args["backup_dir"])))
    elif command == "plan":
        output = Path(args.pop("out"))
        mapping = json.loads(Path(args.pop("mapping")).read_text(encoding="utf-8"))
        result = build_plan(**args, mapping=mapping)
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        print(json.dumps({"status": result["status"], "reasons": result["reasons"]}))
    else:
        print(json.dumps(apply_plan(json.loads(Path(args["plan"]).read_text(encoding="utf-8")), args["backup_dir"])))


if __name__ == "__main__":
    main()
