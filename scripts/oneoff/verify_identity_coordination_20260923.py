"""Zero-network historical response replay and explicit synthetic recovery drill.

Only new output files are writable. Does not edit saved responses, reset a
production import flag, or claim a new model judgment for the Oman identity.
"""
from contextlib import ExitStack, closing
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), *[str(ROOT / p / "src") for p in (
    "pipeline", "s01a_news_crawler", "s01b_project_extractor", "s02a_duplicate_merger", "s02b_entity_normalizer", "s03_data_exporter")]]
OLD = ROOT / "output/pipeline-recall-restoration-20260923-123237"
OUT = ROOT / "output/pipeline-production-readiness-20260923-144217"


def write(path, data):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def ro(path):
    c = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def backup(src, dst):
    with dst.open("xb"):
        pass
    with closing(ro(src)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)


def core(path):
    with closing(ro(path)) as c:
        return {t: {r["id"]: dict(r) for r in c.execute(f'SELECT * FROM "{t}" ORDER BY id')}
                for t in ("canonical_projects", "project_events", "chinese_entities", "non_project_records")}


def main():
    from bri_pipeline import flow, historical_reprocess as history
    from s01b_project_extractor import main as bulk
    from s01b_project_extractor.llm.client import LLMClient
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger
    from google import genai
    from google.genai import models
    from openai.resources.chat.completions import Completions

    work = OUT / "recorded-replay-v2"
    work.mkdir(exist_ok=False)
    logging.basicConfig(filename=work / "run.log", level=logging.INFO, encoding="utf-8")
    production = ROOT / "s02a_duplicate_merger/data/db/bri_re_database.db"
    backup(production, work / "main.db")
    before = core(work / "main.db")
    backup(ROOT / "output/incremental-20-20260923/raw.db", work / "raw.db")
    # Fresh selected-row input from the exact previous historical run; no global reset.
    source = OLD / "historical-2/work/projects.db"
    with closing(ro(source)) as src, closing(sqlite3.connect(work / "projects.db")) as dst, dst:
        schema = src.execute("SELECT sql FROM sqlite_master WHERE name='extracted_projects'").fetchone()[0]
        dst.execute(schema)
        incoming = [dict(r) for r in src.execute("SELECT * FROM extracted_projects ORDER BY id")]
        for row in incoming:
            row["imported_to_s02a"] = 0  # new input ledger, previously consumed copy is untouched
            dst.execute(f"INSERT INTO extracted_projects ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", list(row.values()))
    assert len({r["source_link"] for r in incoming}) == 2
    shutil.copy2(ROOT / "s03_data_exporter/data/province_cache.json", work / "province-cache.json")
    requests = {n: json.loads((OLD / f"responses/request-{n}.json").read_text(encoding="utf-8")) for n in (194, 195)}
    texts = {n: (OLD / f"responses/response-{n}.txt").read_text(encoding="utf-8") for n in (194, 195)}
    replayed, forbidden = [], []

    def forbid(*args, **kwargs):
        forbidden.append("network")
        raise AssertionError("No external requests authorized in this validation")

    def generate(**kwargs):
        for number, request in requests.items():
            if kwargs["contents"] == request["contents"] and kwargs["model"] == request["model"]:
                replayed.append(number)
                return SimpleNamespace(text=texts[number])
        write(work / "unmatched-request.json", {"model": kwargs.get("model"), "contents": kwargs.get("contents")})
        raise AssertionError("No exact saved request match; do not invent a model verdict")

    cfg = flow.PipelineConfig(skip_s01a=True, raw_news_db_path=str(work / "raw.db"),
        projects_db_path=str(work / "projects.db"), review_db_path=str(work / "review.db"),
        db_path=str(work / "main.db"), raw_ids=[31, 39], max_extract_workers=1,
        run_entity_intake=False, run_location_normalize=False, run_developer_normalize=False,
        run_en_backfill=False, run_txmf_backfill=False, run_residual_translate=False,
        run_storage_subcat_backfill=False, run_energy_taxonomy_shadow=False,
        run_export=True, export_dir=str(work / "export"), export_web_dir=str(work / "web/data"),
        export_cache_path=str(work / "province-cache.json"), export_allow_model_calls=False)
    with ExitStack() as stack:
        for target, name, value in (
            (socket.socket, "connect", forbid), (socket, "create_connection", forbid),
            (models.Models, "generate_content", forbid), (Completions, "create", forbid),
            (LLMClient, "_raw_call", forbid), (bulk, "_RUN_LOG_PATH", work / "extraction.log"),
            (genai, "Client", lambda **kw: SimpleNamespace(models=SimpleNamespace(generate_content=generate))),
        ):
            stack.enter_context(patch.object(target, name, value))
        stack.enter_context(patch.dict(os.environ, {"GEMINI_API_KEY": "offline-unused"}))
        result = flow.run_pipeline(cfg)
        assert result["status"] == "partial" and result["merge"]["coordination_pending"] == 1
        assert replayed == [194, 195] and not forbidden
        after = core(work / "main.db")
        assert set(after["canonical_projects"]) == set(before["canonical_projects"])
        assert set(after["project_events"]) - set(before["project_events"]) == {"THA-TH-0001-02"}
        assert "OMN-WD-0005" not in after["canonical_projects"]
        for table in before:
            for rid, row in before[table].items():
                for key, value in row.items():
                    if table == "canonical_projects" and rid == "THA-TH-0001" and key == "category":
                        continue  # existing normal biomass classification refresh; immutable ID
                    assert after[table][rid][key] == value, (table, rid, key)
        with closing(ro(work / "projects.db")) as c:
            tasks = [dict(r) for r in c.execute("SELECT * FROM s02a_coordination")]
            sources = [dict(r) for r in c.execute("SELECT id,source_link,imported_to_s02a,publication_status FROM extracted_projects")]
        assert len(tasks) == 1
        evidence = json.loads(tasks[0]["evidence_json"])[0]
        assert evidence["raw_response"] == texts[194]
        assert evidence["candidate_canonical_id"] == "OMN-WD-0002"
        assert evidence["conflict_fields"]["capacity_mw"] == {"historical": 120.0, "incoming": "123.0"}
        oman_url = "https://news.goalfore.cn/topstories/detail/98167.html"
        assert all(r["imported_to_s02a"] == int(r["source_link"] != oman_url or r["publication_status"] == "excluded") for r in sources)
        public = json.loads((work / "web/data/projects_flat.json").read_text(encoding="utf-8"))
        public_text = json.dumps(public, ensure_ascii=False)
        assert "OMN-WD-0005" not in public_text and oman_url not in public_text
        csv_text = (work / "web/data/events_export.csv").read_text(encoding="utf-8-sig")
        assert "OMN-WD-0005" not in csv_text and oman_url not in csv_text
        count = len(replayed)
        repeat = flow.run_pipeline(cfg)
        assert repeat["merge"]["coordination_created"] == 0 and len(replayed) == count
        assert core(work / "main.db") == after
        with closing(ro(work / "projects.db")) as c:
            assert [dict(r) for r in c.execute("SELECT * FROM s02a_coordination")] == tasks
        write(work / "verification.json", dict(method="Exact raw response194/195 replay; exact request text/model matched. S01B uses saved successful source rows and skips both URLs. No new real model validation.",
            first=result, repeat=repeat, replayed_response_numbers=replayed,
            source_states=sources, task=tasks[0],
            old_ids_preserved=True, old_rows_preserved_except_thai_category=True,
            counts_before={t:len(v) for t,v in before.items()}, counts_after={t:len(v) for t,v in after.items()},
            oman_new_canonical=False, oman_export_leak=False, external_calls=0, blocked_network_attempts=forbidden))

        # Deliberately separate synthetic operator decision from the unresolved real case.
        drill = OUT / "synthetic-resolution-drill"
        drill.mkdir(exist_ok=False)
        backup(work / "projects.db", drill / "projects.db")
        backup(work / "main.db", drill / "main.db")
        decision = dict(task_id=tasks[0]["task_id"], decision="existing", canonical_id="OMN-WD-0002",
                        reviewer="synthetic-drill-not-a-real-approval",
                        evidence_review_note="Synthetic recovery-path exercise only. Real Oman capacity/owner/identity remains unadjudicated and must not be applied to production.")
        plan = history.build_coordination_plan(drill / "projects.db", drill / "main.db", decision)
        write(drill / "review-plan.json", plan)
        applied = history.apply_coordination_plan(plan, drill / "backup")
        repeated = history.apply_coordination_plan(plan, drill / "backup")
        resolved = core(drill / "main.db")
        assert set(resolved["canonical_projects"]) == set(after["canonical_projects"])
        assert applied["canonical_id"] == "OMN-WD-0002" and repeated["status"] == "already_applied"
        for table in ("project_events", "chinese_entities", "non_project_records"):
            for rid, row in after[table].items():
                assert resolved[table][rid] == row
        assert not forbidden and len(replayed) == 2
        checks = {}
        for path in (drill / "main.db", drill / "projects.db", work / "main.db", work / "projects.db"):
            with closing(ro(path)) as c:
                integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
                fk = c.execute("PRAGMA foreign_key_check").fetchall()
                assert integrity == "ok" and not fk
                checks[str(path.relative_to(OUT))] = {"integrity": integrity, "foreign_keys": fk}
        write(drill / "verification.json", dict(method="Synthetic operator decision against copied real evidence; NOT an Oman identity confirmation", apply=applied,
            repeat=repeated, all_old_canonical_ids_preserved=True, old_event_relation_rows_preserved=True,
            checks=checks, external_calls=0, real_oman_still_pending=True))
    initial = json.loads((OUT / "protected-before.json").read_text(encoding="utf-8"))
    changed = [name for name, old_hash in initial.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != old_hash]
    write(OUT / "replay-protected-verification.json", {"checked_files":len(initial), "changed":changed, "external_calls":0})
    assert not changed
    print(json.dumps({"status":"passed", "replayed":replayed, "production_unchanged":True, "external_calls":0}))


if __name__ == "__main__":
    main()
