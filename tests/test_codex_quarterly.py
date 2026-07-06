"""测试 codex_review/quarterly.py：export/ingest/apply 三子命令编排 + CODEX_README 生成器。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import codex_review.quarterly as q

QUARTER = "2026Q3"


# ──────────────────────────────────────────────
# Fixture：最小三表 SQLite 数据库（复用 export_queue 系测试的建表结构）
# ──────────────────────────────────────────────


def _make_db(path: Path) -> Path:
    """在 path 建三表最小列集 SQLite 数据库并返回路径。"""
    conn = sqlite3.connect(str(path))
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY,
            project_name TEXT,
            project_name_en TEXT,
            country TEXT,
            country_iso TEXT,
            category TEXT,
            excluded INTEGER NOT NULL DEFAULT 0,
            excluded_reason TEXT
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY,
            canonical_id TEXT NOT NULL,
            project_stage TEXT,
            capacity_mw REAL,
            capacity_mw_raw TEXT,
            publish_date TEXT,
            project_summary TEXT,
            source_links TEXT
        );
        CREATE TABLE chinese_entities (
            id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            implementing_entity TEXT,
            subsidiary_name TEXT
        );
    """)
    conn.commit()
    conn.close()
    return path


def _insert_project(conn, pid, name, name_en=None, country="印度", iso="IND", cat="PV", excluded=0):
    """向 canonical_projects 插入一行。"""
    conn.execute(
        "INSERT INTO canonical_projects VALUES (?,?,?,?,?,?,?,?)",
        (pid, name, name_en, country, iso, cat, excluded, None),
    )


def _insert_event(conn, eid, cid, stage="建设中", pub="2023-01-01", summary=None, links=None):
    """向 project_events 插入一行。"""
    conn.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)",
        (eid, cid, stage, None, None, pub, summary, json.dumps(links or [])),
    )


def _insert_entity(conn, eid, event_id, impl=None, sub=None):
    """向 chinese_entities 插入一行。"""
    conn.execute(
        "INSERT INTO chinese_entities VALUES (?,?,?,?)",
        (eid, event_id, impl, sub),
    )


def _make_populated_db(path: Path, with_t1_pair: bool = True) -> Path:
    """建库并插入能同时触发 T1（相似对）和 T2（无中企）候选的数据。"""
    _make_db(path)
    conn = sqlite3.connect(str(path))
    if with_t1_pair:
        # 两个高相似名称，同国同类 → T1 命中
        _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", "Vietnam 50MW PV", "越南", "VNM", "光伏")
        _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", "Ninh Thuan 50MW PV", "越南", "VNM", "光伏")
        _insert_event(conn, "E001", "VNM-PV-0001", links=["https://example.com/1"])
        _insert_event(conn, "E002", "VNM-PV-0002", links=["https://example.com/2"])
        _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
        _insert_entity(conn, "ENT002", "E002", impl="中国电建股份")
    # 一个无中企的项目 → T2 no_chinese_entity_scan 命中
    _insert_project(conn, "IND-PV-0001", "印度测试项目")
    _insert_event(conn, "E100", "IND-PV-0001")
    conn.commit()
    conn.close()
    return path


def _write_audit_csv(path: Path) -> Path:
    """写一份空的最小审计 CSV（utf-8-sig），列名与真实 CSV 对齐。"""
    header = "序号,event_id,project_id,项目名,类别,字段,现值,建议值,动作,置信度,判断依据,原文证据,附带备注\n"
    path.write_bytes(b"\xef\xbb\xbf" + header.encode("utf-8"))
    return path


def _write_misses_csv(path: Path, rows: list[tuple[str, int]]) -> Path:
    """写一份 T3 misses CSV（name,count）。"""
    lines = ["name,count\n"] + [f"{n},{c}\n" for n, c in rows]
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_registry(path: Path) -> Path:
    """写一份最小 registry JSON。"""
    path.write_text(
        json.dumps({"schema_version": 2, "groups": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# ──────────────────────────────────────────────
# export 子命令
# ──────────────────────────────────────────────


def test_export_end_to_end_generates_three_queues_and_readme(tmp_path):
    """export：三队列文件生成，CODEX_README.md 存在且含三任务条数与禁止额外字段字样。"""
    db_path = _make_populated_db(tmp_path / "test.db")
    audit_csv = _write_audit_csv(tmp_path / "audit.csv")
    misses_csv = _write_misses_csv(tmp_path / "entity_reparse_misses_20260706_000000.csv",
                                    [("华为技术有限公司", 100)])
    registry = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "runs"
    ledger_path = tmp_path / "ledger.json"

    q.run_export(
        quarter=QUARTER,
        db=db_path,
        audit_csv=audit_csv,
        extra_ids=[],
        misses_csv_dir=tmp_path,
        registry=registry,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    quarter_dir = out_dir / QUARTER
    assert (quarter_dir / "queue_T1.json").exists()
    assert (quarter_dir / "queue_T2.json").exists()
    assert (quarter_dir / "queue_T3.json").exists()

    readme_path = quarter_dir / "CODEX_README.md"
    assert readme_path.exists()
    text = readme_path.read_text(encoding="utf-8")
    assert "禁止任何额外字段" in text

    # 三任务条数各自出现在文件清单表里
    t1_count = len(json.loads((quarter_dir / "queue_T1.json").read_text(encoding="utf-8"))["items"])
    t2_count = len(json.loads((quarter_dir / "queue_T2.json").read_text(encoding="utf-8"))["items"])
    t3_count = len(json.loads((quarter_dir / "queue_T3.json").read_text(encoding="utf-8"))["items"])
    assert t1_count >= 1
    assert t2_count >= 1
    assert t3_count >= 1
    assert str(t1_count) in text
    assert str(t2_count) in text
    assert str(t3_count) in text


def test_export_zero_queue_marked_no_activity(tmp_path):
    """export：某任务队列为 0 条时，README 标注"本轮无活"。"""
    # 无 T1 对（只一个项目，无法配对）、无 T2 触发（有中企）
    db_path = tmp_path / "test.db"
    _make_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度单一项目")
    _insert_event(conn, "E001", "IND-PV-0001")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv")
    # 空 misses CSV → T3 也 0 条
    misses_csv = _write_misses_csv(tmp_path / "entity_reparse_misses_20260706_000000.csv", [])
    registry = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "runs"
    ledger_path = tmp_path / "ledger.json"

    q.run_export(
        quarter=QUARTER,
        db=db_path,
        audit_csv=audit_csv,
        extra_ids=[],
        misses_csv_dir=tmp_path,
        registry=registry,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    readme_text = (out_dir / QUARTER / "CODEX_README.md").read_text(encoding="utf-8")
    assert "本轮无活" in readme_text


# ──────────────────────────────────────────────
# misses CSV 自动选最新
# ──────────────────────────────────────────────


def test_export_picks_latest_misses_csv_by_mtime(tmp_path):
    """misses 自动选最新：两份不同 mtime 的 CSV → 用新的那份（队列内容来自新 CSV）。"""
    import os
    import time

    db_path = tmp_path / "test.db"
    _make_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度单一项目")
    _insert_event(conn, "E001", "IND-PV-0001")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv")
    registry = _write_registry(tmp_path / "registry.json")

    old_csv = _write_misses_csv(tmp_path / "entity_reparse_misses_20260701_000000.csv",
                                 [("旧公司名称有限公司", 10)])
    new_csv = _write_misses_csv(tmp_path / "entity_reparse_misses_20260706_000000.csv",
                                 [("新公司名称有限公司", 20)])

    old_time = time.time() - 1000
    new_time = time.time()
    os.utime(old_csv, (old_time, old_time))
    os.utime(new_csv, (new_time, new_time))

    out_dir = tmp_path / "runs"
    ledger_path = tmp_path / "ledger.json"

    q.run_export(
        quarter=QUARTER,
        db=db_path,
        audit_csv=audit_csv,
        extra_ids=[],
        misses_csv_dir=tmp_path,
        registry=registry,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / QUARTER / "queue_T3.json").read_text(encoding="utf-8"))
    names = [item["raw_name"] for item in data["items"]]
    assert "新公司名称有限公司" in names
    assert "旧公司名称有限公司" not in names


def test_export_no_misses_csv_found_raises(tmp_path):
    """misses CSV 找不到（glob 空）→ 报错。"""
    db_path = tmp_path / "test.db"
    _make_db(db_path)
    audit_csv = _write_audit_csv(tmp_path / "audit.csv")
    registry = _write_registry(tmp_path / "registry.json")
    empty_dir = tmp_path / "empty_supporting_docs"
    empty_dir.mkdir()

    with pytest.raises((ValueError, FileNotFoundError)):
        q.run_export(
            quarter=QUARTER,
            db=db_path,
            audit_csv=audit_csv,
            extra_ids=[],
            misses_csv_dir=empty_dir,
            registry=registry,
            out_dir=tmp_path / "runs",
            ledger_path=tmp_path / "ledger.json",
        )


# ──────────────────────────────────────────────
# ingest 子命令
# ──────────────────────────────────────────────


def _write_queue(path: Path, task: str, items: list[dict]) -> None:
    """写假队列文件。"""
    path.write_text(
        json.dumps({"task": task, "quarter": QUARTER, "generated_at": "2026-07-06T00:00:00Z",
                    "items": items}),
        encoding="utf-8",
    )


def _write_verdicts(path: Path, task: str, verdicts: list[dict]) -> None:
    """写假 verdicts 文件。"""
    path.write_text(
        json.dumps({"task": task, "quarter": QUARTER, "verdicts": verdicts}),
        encoding="utf-8",
    )


def _t2_queue_item(project_id: str) -> dict:
    """构造一条 T2QueueItem dict。"""
    return {
        "item_id": "k1",
        "project_id": project_id,
        "trigger": "fixed_list",
        "audit_note": None,
        "project": {
            "canonical_id": project_id,
            "project_name": "测试项目",
            "country": "印度",
            "country_iso": "IND",
            "category": "PV",
            "events": [],
            "entity_names": [],
        },
    }


def _t2_verdict(project_id: str, action: str = "keep") -> dict:
    """构造 T2Verdict dict。"""
    return {
        "project_id": project_id,
        "action": action,
        "reason": "判断原因",
        "evidence_quote": "引用文本",
    }


def test_ingest_only_t2_present_others_skipped(tmp_path):
    """ingest：只存在 verdicts_T2.json → T2 被处理、T1/T3 跳过提示，退出码 0。"""
    runs_dir = tmp_path / "runs"
    quarter_dir = runs_dir / QUARTER
    quarter_dir.mkdir(parents=True)

    _write_queue(quarter_dir / "queue_T2.json", "T2", [_t2_queue_item("IND-PV-0001")])
    _write_verdicts(quarter_dir / "verdicts_T2.json", "T2", [_t2_verdict("IND-PV-0001")])

    ledger_path = tmp_path / "ledger.json"

    exit_code = q.run_ingest_all(
        quarter=QUARTER,
        runs_dir=runs_dir,
        ledger_path=ledger_path,
        apply=False,
        sample_rate=0.15,
    )

    assert exit_code == 0
    assert (quarter_dir / "ingest_report_T2.md").exists()
    assert not (quarter_dir / "ingest_report_T1.md").exists()
    assert not (quarter_dir / "ingest_report_T3.md").exists()


def test_ingest_envelope_mismatch_nonzero_exit(tmp_path):
    """ingest：信封 task 不符 → 整体非零退出。"""
    runs_dir = tmp_path / "runs"
    quarter_dir = runs_dir / QUARTER
    quarter_dir.mkdir(parents=True)

    _write_queue(quarter_dir / "queue_T2.json", "T2", [_t2_queue_item("IND-PV-0001")])
    # verdicts 信封 task 写错为 T1
    (quarter_dir / "verdicts_T2.json").write_text(
        json.dumps({"task": "T1", "quarter": QUARTER, "verdicts": []}),
        encoding="utf-8",
    )

    ledger_path = tmp_path / "ledger.json"

    exit_code = q.run_ingest_all(
        quarter=QUARTER,
        runs_dir=runs_dir,
        ledger_path=ledger_path,
        apply=False,
        sample_rate=0.15,
    )

    assert exit_code != 0


def test_ingest_summary_printed(tmp_path, capsys):
    """ingest：汇总打印含各任务关键计数。"""
    runs_dir = tmp_path / "runs"
    quarter_dir = runs_dir / QUARTER
    quarter_dir.mkdir(parents=True)

    _write_queue(quarter_dir / "queue_T2.json", "T2", [_t2_queue_item("IND-PV-0001")])
    _write_verdicts(quarter_dir / "verdicts_T2.json", "T2", [_t2_verdict("IND-PV-0001")])

    q.run_ingest_all(
        quarter=QUARTER,
        runs_dir=runs_dir,
        ledger_path=tmp_path / "ledger.json",
        apply=False,
        sample_rate=0.15,
    )

    captured = capsys.readouterr()
    assert "T2" in captured.out
    # dry-run 提示下一步
    assert "--apply" in captured.out


# ──────────────────────────────────────────────
# apply 子命令
# ──────────────────────────────────────────────


def test_apply_only_t2_present_others_skipped(tmp_path):
    """apply：只存在 apply_plan_T2.json → T2 执行，其余跳过。"""
    db_path = tmp_path / "test.db"
    _make_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度测试项目")
    conn.commit()
    conn.close()

    runs_dir = tmp_path / "runs"
    quarter_dir = runs_dir / QUARTER
    quarter_dir.mkdir(parents=True)

    plan = [
        {
            "verdict": _t2_verdict("IND-PV-0001", action="exclude"),
            "context": {"project_name": "测试项目", "trigger": "fixed_list"},
        }
    ]
    (quarter_dir / "apply_plan_T2.json").write_text(json.dumps(plan), encoding="utf-8")

    ledger_path = tmp_path / "ledger.json"

    exit_code = q.run_apply_all(
        quarter=QUARTER,
        runs_dir=runs_dir,
        db=db_path,
        ledger_path=ledger_path,
        apply=True,
    )

    assert exit_code == 0
    assert (quarter_dir / "apply_report_T2.json".replace(".json", ".md")).exists() or \
        (quarter_dir / "apply_report_T2.md").exists()
    assert not (quarter_dir / "apply_report_T1.md").exists()
    assert not (quarter_dir / "apply_report_T3.md").exists()

    conn = sqlite3.connect(str(db_path))
    (excluded,) = conn.execute(
        "SELECT excluded FROM canonical_projects WHERE id=?", ("IND-PV-0001",)
    ).fetchone()
    conn.close()
    assert excluded == 1


def test_apply_summary_printed(tmp_path, capsys):
    """apply：汇总打印含各任务关键计数（done/skipped/conflict/error）。"""
    db_path = tmp_path / "test.db"
    _make_db(db_path)
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度测试项目")
    conn.commit()
    conn.close()

    runs_dir = tmp_path / "runs"
    quarter_dir = runs_dir / QUARTER
    quarter_dir.mkdir(parents=True)

    plan = [
        {
            "verdict": _t2_verdict("IND-PV-0001", action="exclude"),
            "context": {"project_name": "测试项目", "trigger": "fixed_list"},
        }
    ]
    (quarter_dir / "apply_plan_T2.json").write_text(json.dumps(plan), encoding="utf-8")

    q.run_apply_all(
        quarter=QUARTER,
        runs_dir=runs_dir,
        db=db_path,
        ledger_path=tmp_path / "ledger.json",
        apply=True,
    )

    captured = capsys.readouterr()
    assert "T2" in captured.out
    assert "done" in captured.out.lower() or "完成" in captured.out
