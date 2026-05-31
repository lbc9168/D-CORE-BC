"""e2e 测试：run_review 编排——mock critic 注入，验证 dry_run/非 dry_run 两路径、字段白名单、report 结构。"""
import json
import sqlite3

import pytest

from review import critic as critic_mod
from review import run_review


# ─── helpers ────────────────────────────────────────────────────────────────

def _make_projects_db(path):
    """建最小 extracted_projects 表（仅 run_review 读写的列）并插入 row A（干净）和 row B（可疑）。"""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE extracted_projects (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name            TEXT,
            category                TEXT,
            capacity_mw             REAL,
            capacity_mw_raw         TEXT,
            involvement_type        TEXT,
            implementing_entity     TEXT,
            is_unnamed_placeholder  INTEGER DEFAULT 0,
            source_link             TEXT
        )
    """)
    # row A：干净，involvement_type 非 Unknown → signals 不标可疑
    conn.execute(
        "INSERT INTO extracted_projects (project_name, category, capacity_mw, involvement_type, "
        "implementing_entity, is_unnamed_placeholder, source_link) VALUES (?,?,?,?,?,?,?)",
        ("巴基斯坦光伏项目", "光伏", 50.0, "EPC总承包", "中国电建", 0, "http://a"),
    )
    # row B：involvement_type=Unknown → signals 标可疑
    conn.execute(
        "INSERT INTO extracted_projects (project_name, category, capacity_mw, involvement_type, "
        "implementing_entity, is_unnamed_placeholder, source_link) VALUES (?,?,?,?,?,?,?)",
        ("某风电项目", "光伏", 50.0, "Unknown", "中国电建", 0, "http://b"),
    )
    conn.commit()
    conn.close()


def _make_article_fn():
    """返回一个 article_fn 注入函数；两个 URL 各有一篇假文章。"""
    articles = {
        "http://a": {"title": "光伏项目签约", "content": "EPC合同签署完成。", "publish_date": "2026-01-01"},
        "http://b": {"title": "风电项目启动", "content": "原文称风电场建设正式启动。", "publish_date": "2026-01-02"},
    }

    def article_fn(url, db_path=None):
        return articles.get(url)

    return article_fn


def _mock_critic_fn(article, project, suspicious, llm):
    """mock critic：row B (http://b) 返回一个 wrong verdict；row A 返回空列表。"""
    source = project.get("source_link", "")
    if source == "http://b":
        return [
            critic_mod.Verdict(
                field="category",
                verdict="wrong",
                suggested_value="风电",
                confidence=0.9,
                evidence="原文称风电场",
            )
        ]
    return []


# ─── tests ──────────────────────────────────────────────────────────────────

def test_run_review_non_dry_run_corrects_db(tmp_path):
    """非 dry_run：row B category 应被写为 "风电"；row A 不变；report 字段正确。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),  # 不会被访问（article_fn 注入）
        review_db=str(review_db),
        critic_fn=_mock_critic_fn,
        article_fn=_make_article_fn(),
        dry_run=False,
        conf_threshold=0.8,
        ts="20260601_test",
    )

    # report structure
    assert "n_candidates" in report
    assert "n_auto_corrected" in report
    assert "n_queued" in report
    assert "n_critic_failed" in report
    assert "n_no_article" in report
    assert "n_update_failed" in report
    assert "reasons" in report
    assert report["dry_run"] is False
    assert report["n_update_failed"] == 0  # 正常写库不应有失败

    # row B 有 involvement_type=Unknown → 1 candidate
    assert report["n_candidates"] == 1
    assert report["n_critic_failed"] == 0
    assert report["n_no_article"] == 0
    # verdict: category wrong, conf 0.9 >= 0.8 → auto_corrected
    assert report["n_auto_corrected"] == 1
    assert report["n_queued"] == 0

    # DB 验证：row B 的 category 应已变 "风电"
    conn = sqlite3.connect(str(projects_db))
    rows = conn.execute("SELECT id, category FROM extracted_projects ORDER BY id").fetchall()
    conn.close()
    assert rows[0][1] == "光伏", "row A 不应被改"
    assert rows[1][1] == "风电", "row B category 应被自动纠正为风电"


def test_run_review_dry_run_does_not_modify_db(tmp_path):
    """dry_run=True：DB 不被修改，但 report 仍报告 what-would-change。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),
        review_db=str(review_db),
        critic_fn=_mock_critic_fn,
        article_fn=_make_article_fn(),
        dry_run=True,
        conf_threshold=0.8,
        ts="20260601_dry",
    )

    assert report["dry_run"] is True
    assert report["n_candidates"] == 1
    assert report["n_auto_corrected"] == 1  # what would have been corrected
    assert report["n_critic_failed"] == 0

    # DB 验证：row B 不能被修改
    conn = sqlite3.connect(str(projects_db))
    rows = conn.execute("SELECT id, category FROM extracted_projects ORDER BY id").fetchall()
    conn.close()
    assert rows[1][1] == "光伏", "dry_run 模式不应写库"


def test_run_review_no_article_skips_critic(tmp_path):
    """article_fn 对 row B 返回 None → n_no_article=1，不调用 critic，不纠正。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    critic_called = []

    def mock_critic_track(article, project, suspicious, llm):
        critic_called.append(project.get("source_link"))
        return []

    def article_fn_none(url, db_path=None):
        return None  # 总是没有原文

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),
        review_db=str(review_db),
        critic_fn=mock_critic_track,
        article_fn=article_fn_none,
        dry_run=False,
        ts="20260601_noart",
    )

    assert report["n_no_article"] == 1
    assert critic_called == [], "article 为 None 时不应调用 critic"


def test_run_review_critic_failure_counted_and_skipped(tmp_path):
    """critic 抛 RuntimeError → n_critic_failed 计数，不 abort 整个 run。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    def failing_critic(article, project, suspicious, llm):
        raise RuntimeError("LLM timeout")

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),
        review_db=str(review_db),
        critic_fn=failing_critic,
        article_fn=_make_article_fn(),
        dry_run=False,
        ts="20260601_fail",
    )

    assert report["n_critic_failed"] == 1
    assert report["n_auto_corrected"] == 0


def test_run_review_disallowed_field_not_written(tmp_path):
    """verdict 字段不在白名单（如 source_link）→ 不写库，auto_corrected 不计入。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    def evil_critic(article, project, suspicious, llm):
        # 尝试改 source_link（不在白名单）
        return [
            critic_mod.Verdict(
                field="source_link",
                verdict="wrong",
                suggested_value="http://evil.com",
                confidence=0.99,
                evidence="注入测试",
            )
        ]

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),
        review_db=str(review_db),
        critic_fn=evil_critic,
        article_fn=_make_article_fn(),
        dry_run=False,
        ts="20260601_evil",
    )

    # DB 不应被改
    conn = sqlite3.connect(str(projects_db))
    rows = conn.execute("SELECT source_link FROM extracted_projects WHERE id=2").fetchone()
    conn.close()
    assert rows[0] == "http://b", "不在白名单的字段不应被改"


def test_run_review_report_json_written(tmp_path):
    """run_review 应将 report 写到 review/reports/<ts>.json。"""
    projects_db = tmp_path / "projects.db"
    review_db = tmp_path / "review.db"
    _make_projects_db(projects_db)

    report = run_review.run_review(
        projects_db=str(projects_db),
        raw_news_db=str(tmp_path / "raw_news.db"),
        review_db=str(review_db),
        critic_fn=_mock_critic_fn,
        article_fn=_make_article_fn(),
        dry_run=False,
        ts="20260601_rpt",
    )

    # 报告文件应存在（路径在 review/reports/）
    import pathlib
    _root = next(
        p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists()
    )
    report_path = _root / "review" / "reports" / "20260601_rpt.json"
    assert report_path.exists(), f"report JSON 未写入: {report_path}"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["n_candidates"] == report["n_candidates"]
