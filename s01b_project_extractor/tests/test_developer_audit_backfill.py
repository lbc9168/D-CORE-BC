"""Developer audit backfill: complete coverage, evidence validation, and transactional apply."""

import csv
import json
import sqlite3
from datetime import date

import pytest

from s01b_project_extractor.tools.developer_audit_backfill import (
    BackfillConfig,
    apply_plan,
    build_plan,
    load_reviews,
    load_scope,
)


def _make_main_db(path):
    """Create a minimal main DB containing one wrong and one correct visible event."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE canonical_projects (
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT, excluded INTEGER
        );
        CREATE TABLE project_events (
            id TEXT PRIMARY KEY, canonical_id TEXT, publish_date TEXT,
            project_developer TEXT, project_developer_en TEXT, source_links TEXT,
            project_summary TEXT
        );
        CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY, event_id TEXT, implementing_entity TEXT);
        CREATE TABLE non_project_records (id INTEGER PRIMARY KEY, source_link TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO canonical_projects VALUES (?, ?, ?, 0)",
        [("P1", "候选名单项目", "Candidate List Project"),
         ("P2", "明确业主项目", "Explicit Owner Project")],
    )
    conn.executemany(
        "INSERT INTO project_events VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("E1", "P1", "2025-02-01", "A公司; B公司", "A; B", '["https://x/1"]', "摘要1"),
            ("E2", "P2", "2026-03-01", "C公司", "Company C", '["https://x/2"]', "摘要2"),
        ],
    )
    conn.execute("INSERT INTO chinese_entities VALUES (1, 'E1', '中国企业')")
    conn.execute("INSERT INTO non_project_records VALUES (1, 'https://x/n')")
    conn.commit()
    conn.close()


def _make_raw_db(path):
    """Create linked articles with candidate-only and final-owner evidence."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE raw_news (url TEXT, title TEXT, content TEXT)")
    conn.executemany(
        "INSERT INTO raw_news VALUES (?, ?, ?)",
        [
            ("https://x/1", "合格开发商名单公布", "A公司与B公司进入项目短名单。"),
            ("https://x/2", "项目签约", "项目由C公司全资持有。"),
        ],
    )
    conn.commit()
    conn.close()


def _write_reviews(path, include_second=True, bad_quote=False):
    """Write a complete review CSV, optionally omitting coverage or corrupting evidence."""
    fields = [
        "event_id", "canonical_id", "project_name", "publish_date", "current_developer",
        "source_links", "verdict", "proposed_developer", "proposed_developer_en",
        "reason", "evidence_quote", "issue_type",
    ]
    rows = [
        {
            "event_id": "E1", "canonical_id": "P1", "project_name": "候选名单项目",
            "publish_date": "2025-02-01", "current_developer": "A公司; B公司",
            "source_links": '["https://x/1"]', "verdict": "wrong",
            "proposed_developer": "", "proposed_developer_en": "",
            "reason": "只有短名单，没有最终授标", "evidence_quote": "不存在的引文" if bad_quote else "进入项目短名单",
            "issue_type": "candidate_list",
        }
    ]
    if include_second:
        rows.append(
            {
                "event_id": "E2", "canonical_id": "P2", "project_name": "明确业主项目",
                "publish_date": "2026-03-01", "current_developer": "C公司",
                "source_links": '["https://x/2"]', "verdict": "correct",
                "proposed_developer": "", "proposed_developer_en": "",
                "reason": "原文明示全资持有", "evidence_quote": "项目由C公司全资持有",
                "issue_type": "none",
            }
        )
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _config(tmp_path, review_path):
    """Build a validated config pointing only at temporary test artifacts."""
    db_path = tmp_path / "main.db"
    raw_path = tmp_path / "raw.db"
    flat_path = tmp_path / "projects_flat.json"
    _make_main_db(db_path)
    _make_raw_db(raw_path)
    flat_path.write_text(json.dumps([{"project_id": "P1"}, {"project_id": "P2"}]), encoding="utf-8")
    return BackfillConfig(
        db_path=db_path,
        raw_db_path=raw_path,
        projects_flat_path=flat_path,
        review_csv_path=review_path,
        out_path=tmp_path / "preview.csv",
        date_from=date(2025, 1, 1),
    )


def test_build_plan_requires_complete_review_coverage(tmp_path):
    """A partial audit must fail before producing any database mutation plan."""
    review_path = tmp_path / "review.csv"
    _write_reviews(review_path, include_second=False)
    config = _config(tmp_path, review_path)
    with pytest.raises(ValueError, match="覆盖不完整"):
        build_plan(config, load_scope(config), load_reviews(review_path))


def test_build_plan_rejects_non_verbatim_evidence(tmp_path):
    """Changed rows require one continuous quote from a linked title or body."""
    review_path = tmp_path / "review.csv"
    _write_reviews(review_path, bad_quote=True)
    config = _config(tmp_path, review_path)
    with pytest.raises(ValueError, match="连续子串"):
        build_plan(config, load_scope(config), load_reviews(review_path))


def test_apply_updates_only_developer_columns_and_creates_backup(tmp_path):
    """Apply changes the audited event, preserves all other data, and leaves a readable backup."""
    review_path = tmp_path / "review.csv"
    _write_reviews(review_path)
    config = _config(tmp_path, review_path)
    plan = build_plan(config, load_scope(config), load_reviews(review_path))
    assert [row["event_id"] for row in plan] == ["E1"]

    count, backup_path = apply_plan(config, plan)
    assert count == 1
    assert backup_path.is_file() and backup_path.stat().st_size > 0

    conn = sqlite3.connect(config.db_path)
    assert conn.execute(
        "SELECT project_developer, project_developer_en, project_summary FROM project_events WHERE id='E1'"
    ).fetchone() == ("", "", "摘要1")
    assert conn.execute(
        "SELECT project_developer, project_developer_en FROM project_events WHERE id='E2'"
    ).fetchone() == ("C公司", "Company C")
    conn.close()
