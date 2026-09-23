"""The one-off exclusion repair must change only the authorized historical scope cells."""
import sqlite3
from pathlib import Path

import pytest

from scripts.oneoff import repair_entity_gate_exclusions as repair


_REASON = sorted(repair.GATE_REASONS)[0]
_ID = "EGY-HB-0001"


def _database(path: Path, *, excluded: int, reason=None, name="历史项目"):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, excluded INTEGER, excluded_reason TEXT);
            CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT REFERENCES canonical_projects(id), capacity_mw REAL);
            CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT REFERENCES project_events(id), implementing_entity TEXT, subsidiary_name TEXT, involvement_type TEXT);
        """)
        conn.execute("INSERT INTO canonical_projects VALUES (?,?,?,?)", (_ID, name, excluded, reason))
        conn.execute("INSERT INTO project_events VALUES (?,?,100)", (_ID + "-01", _ID))
        conn.execute("INSERT INTO chinese_entities VALUES (1,?,'中国电建','具体子公司','EPC总承包')", (_ID + "-01",))
        # An unlisted new canonical with the same gate reason must never be repaired.
        conn.execute("INSERT INTO canonical_projects VALUES ('NEW-PV-0001','新增项目',1,?)", (_REASON,))


def test_readonly_apply_backup_and_idempotent_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(repair, "REPAIR_OUTPUT_ROOT", tmp_path)
    baseline, current = tmp_path / "baseline.db", tmp_path / "current.db"
    _database(baseline, excluded=0)
    _database(current, excluded=1, reason=_REASON)
    before = repair.file_hash(current)
    baseline_hash = repair.file_hash(baseline)
    dry = repair.run_repair(current, baseline, tmp_path / "dry.json")
    assert dry["candidate_count"] == 1 and dry["changed_rows"] == 0
    assert repair.file_hash(current) == before
    applied = repair.run_repair(current, baseline, tmp_path / "apply.json", apply=True)
    assert applied["changed_rows"] == 1
    assert Path(applied["backup"]).is_file()
    assert repair.file_hash(baseline) == baseline_hash
    with repair.connect_readonly(Path(applied["backup"])) as backup:
        assert backup.execute("SELECT excluded FROM canonical_projects WHERE id=?", (_ID,)).fetchone()[0] == 1
    with repair.connect_readonly(current) as conn:
        assert tuple(conn.execute("SELECT excluded,excluded_reason FROM canonical_projects WHERE id=?", (_ID,)).fetchone()) == (0, None)
        assert conn.execute("SELECT excluded FROM canonical_projects WHERE id='NEW-PV-0001'").fetchone()[0] == 1
    assert repair.run_repair(current, baseline, tmp_path / "again.json")["candidate_count"] == 0


@pytest.mark.parametrize("alter,reason", [
    ("UPDATE canonical_projects SET excluded=1 WHERE id='EGY-HB-0001'", "baseline_already_excluded"),
    ("UPDATE canonical_projects SET project_name='其他项目' WHERE id='EGY-HB-0001'", "project_name_changed"),
    ("DELETE FROM chinese_entities", "historical_participation_or_events_missing"),
])
def test_requires_matching_previously_valid_historical_evidence(tmp_path, alter, reason):
    baseline, current = tmp_path / "baseline.db", tmp_path / "current.db"
    _database(baseline, excluded=0)
    _database(current, excluded=1, reason=_REASON)
    with sqlite3.connect(baseline) as conn:
        conn.execute(alter)
    report = repair.run_repair(current, baseline, tmp_path / "dry.json")
    assert report["candidate_count"] == 0
    assert {"canonical_id": _ID, "reason": reason} in report["skipped"]


def test_non_exact_exclusion_reason_is_preserved(tmp_path):
    baseline, current = tmp_path / "baseline.db", tmp_path / "current.db"
    _database(baseline, excluded=0)
    _database(current, excluded=1, reason=_REASON + "；人工确认范围排除")
    assert repair.run_repair(current, baseline, tmp_path / "dry.json")["candidate_count"] == 0


def test_no_overwrite_same_source_or_production_apply(tmp_path, monkeypatch):
    baseline, current = tmp_path / "baseline.db", tmp_path / "current.db"
    _database(baseline, excluded=0)
    _database(current, excluded=1, reason=_REASON)
    monkeypatch.setattr(repair, "REPAIR_OUTPUT_ROOT", tmp_path / "isolated")
    with pytest.raises(ValueError, match="different files"):
        repair.run_repair(current, current, tmp_path / "report.json")
    with pytest.raises(ValueError, match="isolated copies"):
        repair.run_repair(current, baseline, tmp_path / "report.json", apply=True)
    with pytest.raises(ValueError, match="isolated copies"):
        repair.run_repair(current, baseline, tmp_path / "report.json", apply=True, allow_production=True)
    out = tmp_path / "report.json"
    out.write_text("existing audit", encoding="utf-8")
    with pytest.raises(FileExistsError):
        repair.run_repair(current, baseline, out)
    assert out.read_text(encoding="utf-8") == "existing audit"


def test_failed_invariant_rolls_back_entire_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(repair, "REPAIR_OUTPUT_ROOT", tmp_path)
    baseline, current = tmp_path / "baseline.db", tmp_path / "current.db"
    _database(baseline, excluded=0)
    _database(current, excluded=1, reason=_REASON)
    original = repair.database_fingerprint

    def broken_after_change(conn, mutable_ids=frozenset()):
        result = original(conn, mutable_ids)
        if mutable_ids and conn.execute("SELECT excluded FROM canonical_projects WHERE id=?", (_ID,)).fetchone()[0] == 0:
            result["unexpected_change"] = True
        return result

    monkeypatch.setattr(repair, "database_fingerprint", broken_after_change)
    with pytest.raises(RuntimeError, match="Non-target data"):
        repair.run_repair(current, baseline, tmp_path / "apply.json", apply=True)
    with repair.connect_readonly(current) as conn:
        assert conn.execute("SELECT excluded FROM canonical_projects WHERE id=?", (_ID,)).fetchone()[0] == 1
    assert (tmp_path / "apply.before.sqlite").exists()
