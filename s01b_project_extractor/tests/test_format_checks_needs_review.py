"""
任务⑦ TDD 测试 — format_checks 失败打 needs_review 标记

测试策略：
  1. run_format_checks_with_ids 接口：违规记录 ID 进入 failed_ids；合规记录不进入。
  2. DB 集成：写入含违规记录的 extracted_projects，调用标记函数后 needs_review=1；合规行不变。
"""

import sqlite3

import pytest

from conftest import make_record
from s01b_project_extractor.guardrails.format_checks import (
    run_format_checks,
    run_format_checks_with_ids,
)
from s01b_project_extractor.tools.project_extractor_tool import (
    _init_projects_db,
    mark_format_failed_needs_review,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. run_format_checks_with_ids 接口
# ─────────────────────────────────────────────────────────────────────────────

class TestRunFormatChecksWithIds:
    def test_valid_record_not_in_failed_ids(self):
        """合规记录不出现在 failed_ids 中。"""
        rec = make_record({"id": 1})
        errors, failed_ids = run_format_checks_with_ids([rec])
        assert errors == []
        assert 1 not in failed_ids

    def test_invalid_record_in_failed_ids(self):
        """违规记录（分类不合法）的 ID 出现在 failed_ids 中。"""
        rec = make_record({"id": 42, "category": "油气"})
        errors, failed_ids = run_format_checks_with_ids([rec])
        assert any("分类不合法" in e for e in errors)
        assert 42 in failed_ids

    def test_mixed_batch_correct_separation(self):
        """混合批次：违规记录进 failed_ids，合规记录不进。"""
        valid_rec = make_record({"id": 10})
        bad_rec = make_record({"id": 20, "project_stage": "规划中"})
        errors, failed_ids = run_format_checks_with_ids([valid_rec, bad_rec])
        assert 20 in failed_ids
        assert 10 not in failed_ids

    def test_empty_batch_returns_empty(self):
        """空批次：零错误，空集合。"""
        errors, failed_ids = run_format_checks_with_ids([])
        assert errors == []
        assert failed_ids == set()

    def test_backward_compat_run_format_checks_unchanged(self):
        """run_format_checks（旧接口）行为不变：仍返回 list[str]。"""
        rec = make_record({"id": 99, "category": "油气"})
        result = run_format_checks([rec])
        assert isinstance(result, list)
        assert any("分类不合法" in e for e in result)

    def test_errors_match_between_both_functions(self):
        """run_format_checks_with_ids 的 errors 与 run_format_checks 返回相同内容。"""
        rec = make_record({"id": 5, "category": "油气", "project_stage": "规划中"})
        old_errors = run_format_checks([rec])
        new_errors, _ = run_format_checks_with_ids([rec])
        assert old_errors == new_errors

    def test_record_without_id_handled(self):
        """无 id 字段的记录不崩溃（id 默认 '?'，不加入 failed_ids 但返回错误）。"""
        rec = {"project_name": None, "country": "肯尼亚", "category": "光伏", "implementing_entity": "A"}
        errors, failed_ids = run_format_checks_with_ids([rec])
        assert any("缺少必填字段" in e for e in errors)
        # '?' 不是 int，不在 failed_ids（set 可能含 '?' 或不含，取决于实现；只要不崩溃即可）


# ─────────────────────────────────────────────────────────────────────────────
# 2. extracted_projects needs_review 列存在
# ─────────────────────────────────────────────────────────────────────────────

class TestNeedsReviewColumn:
    def test_needs_review_column_exists_after_init(self, tmp_path):
        """_init_projects_db 后 extracted_projects 应有 needs_review 列。"""
        conn = _init_projects_db(str(tmp_path / "p.db"))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(extracted_projects)").fetchall()}
        conn.close()
        assert "needs_review" in cols

    def test_needs_review_default_is_zero(self, tmp_path):
        """插入记录时 needs_review 默认为 0。"""
        conn = _init_projects_db(str(tmp_path / "p.db"))
        conn.execute(
            "INSERT INTO extracted_projects (project_name, batch_id) VALUES (?, ?)",
            ("测试项目", "b1"),
        )
        conn.commit()
        row = conn.execute("SELECT needs_review FROM extracted_projects WHERE batch_id='b1'").fetchone()
        conn.close()
        assert row[0] == 0 or row[0] is None  # DEFAULT 0，旧库可能是 NULL


# ─────────────────────────────────────────────────────────────────────────────
# 3. mark_format_failed_needs_review DB 集成
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkFormatFailedNeedsReview:
    def _setup_db(self, tmp_path, rows):
        """辅助：初始化 DB 并插入指定行，返回 (conn, db_path)。"""
        db_path = str(tmp_path / "p.db")
        conn = _init_projects_db(db_path)
        for row in rows:
            conn.execute(
                "INSERT INTO extracted_projects (id, project_name, batch_id) VALUES (?, ?, ?)",
                (row["id"], row.get("project_name", "测试"), row.get("batch_id", "b1")),
            )
        conn.commit()
        conn.close()
        return db_path

    def test_failed_ids_get_needs_review_one(self, tmp_path):
        """mark_format_failed_needs_review 把 failed_ids 里的行打 needs_review=1。"""
        db_path = self._setup_db(tmp_path, [
            {"id": 1, "project_name": "项目A"},
            {"id": 2, "project_name": "项目B"},
        ])
        mark_format_failed_needs_review(db_path, failed_ids={1})
        conn = sqlite3.connect(db_path)
        r1 = conn.execute("SELECT needs_review FROM extracted_projects WHERE id=1").fetchone()[0]
        r2 = conn.execute("SELECT needs_review FROM extracted_projects WHERE id=2").fetchone()[0]
        conn.close()
        assert r1 == 1
        assert r2 != 1  # 合规行保持原值（0 或 NULL）

    def test_empty_failed_ids_no_update(self, tmp_path):
        """空 failed_ids：不做任何 UPDATE，所有行保持 needs_review=0/NULL。"""
        db_path = self._setup_db(tmp_path, [{"id": 1, "project_name": "项目A"}])
        mark_format_failed_needs_review(db_path, failed_ids=set())
        conn = sqlite3.connect(db_path)
        r = conn.execute("SELECT needs_review FROM extracted_projects WHERE id=1").fetchone()[0]
        conn.close()
        assert r != 1

    def test_large_batch_uses_executemany(self, tmp_path, monkeypatch):
        """超过 50 条时使用 executemany（行为验证：批量 UPDATE 全部落库）。"""
        rows = [{"id": i, "project_name": f"项目{i}"} for i in range(1, 61)]
        db_path = self._setup_db(tmp_path, rows)
        failed = set(range(1, 61))  # 60 条全标
        mark_format_failed_needs_review(db_path, failed_ids=failed)
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM extracted_projects WHERE needs_review=1"
        ).fetchone()[0]
        conn.close()
        assert count == 60
