"""测试 codex_review.apply_actions：T1 merge / T2 exclude / T3 registry 补录产物。

覆盖：merge 正常/幂等/冲突、NN 连续、T2 幂等/downgrade、T3 形状、
     dry-run 零写入、备份三重断言、外键自检、台账记录。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codex_review.apply_actions import (
    _backup_db,
    _integrity_check,
    apply_t1,
    apply_t2,
    apply_t3,
    run_apply,
)
from codex_review.ledger import load_ledger


# ──────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────


def _make_db(path: Path) -> None:
    """建立最小 3 表测试库。"""
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE canonical_projects (
                id TEXT PRIMARY KEY,
                project_name TEXT,
                excluded INTEGER NOT NULL DEFAULT 0,
                excluded_reason TEXT
            );
            CREATE TABLE project_events (
                id TEXT PRIMARY KEY,
                canonical_id TEXT NOT NULL
            );
            CREATE TABLE chinese_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL
            );
        """)


def _insert_project(conn: sqlite3.Connection, cid: str, name: str = "") -> None:
    """插入一条 canonical_projects 行。"""
    conn.execute(
        "INSERT INTO canonical_projects (id, project_name) VALUES (?, ?)", (cid, name)
    )


def _insert_event(
    conn: sqlite3.Connection, eid: str, canonical_id: str
) -> None:
    """插入一条 project_events 行。"""
    conn.execute(
        "INSERT INTO project_events (id, canonical_id) VALUES (?, ?)", (eid, canonical_id)
    )


def _insert_entity(conn: sqlite3.Connection, event_id: str) -> int:
    """插入一条 chinese_entities 行，返回其 rowid。"""
    cur = conn.execute(
        "INSERT INTO chinese_entities (event_id) VALUES (?)", (event_id,)
    )
    return cur.lastrowid  # type: ignore[return-value]


def _count(conn: sqlite3.Connection, table: str, where: str = "1=1") -> int:
    """计 table 里满足 where 条件的行数。"""
    (n,) = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
    return n


def _make_t1_plan(
    pair_id: str, survivor_id: str, loser_id: str, a_id: str, b_id: str
) -> list[dict]:
    """构造一条 T1 apply_plan 条目（same_project=True）。"""
    return [
        {
            "verdict": {
                "pair_id": pair_id,
                "same_project": True,
                "confidence": "high",
                "evidence_quote": "test",
                "survivor_id": survivor_id,
                "reason": "test",
            },
            "context": {
                "a_canonical_id": a_id,
                "a_project_name": "proj A",
                "b_canonical_id": b_id,
                "b_project_name": "proj B",
            },
        }
    ]


def _make_t2_plan(
    project_id: str, action: str, trigger: str = "fixed_list"
) -> list[dict]:
    """构造一条 T2 apply_plan 条目。"""
    return [
        {
            "verdict": {
                "project_id": project_id,
                "action": action,
                "reason": "test",
                "evidence_quote": "test",
            },
            "context": {
                "project_name": "proj",
                "trigger": trigger,
            },
        }
    ]


def _make_t3_plan(
    raw_name: str,
    classification: str = "new_group",
    canonical_group_cn: str = "测试集团",
) -> list[dict]:
    """构造一条 T3 apply_plan 条目。"""
    return [
        {
            "verdict": {
                "raw_name": raw_name,
                "classification": classification,
                "canonical_group_cn": canonical_group_cn,
                "legal_full_name_cn": None,
                "parent_cn": None,
                "is_central_soe": True,
                "source_urls": ["https://example.com"],
                "confidence": "high",
                "notes": "测试",
            },
            "context": {"freq": 5},
        }
    ]


# ──────────────────────────────────────────────
# T1 merge 正常
# ──────────────────────────────────────────────


def test_t1_merge_normal(tmp_path: Path) -> None:
    """S 有 2 事件、L 有 2 事件+3 实体行 → 合并后 S 有 4 事件（-03/-04），实体全跟随，L excluded=1。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S, L = "IND-PV-0001", "IND-PV-0002"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S, "survivor")
        _insert_project(conn, L, "loser")
        _insert_event(conn, f"{S}-01", S)
        _insert_event(conn, f"{S}-02", S)
        _insert_event(conn, f"{L}-01", L)
        _insert_event(conn, f"{L}-02", L)
        _insert_entity(conn, f"{L}-01")
        _insert_entity(conn, f"{L}-01")
        _insert_entity(conn, f"{L}-02")
        conn.commit()

    plan = _make_t1_plan("pair-001", S, L, S, L)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = apply_t1(
        plan_path=plan_path,
        db_path=db,
        ledger_path=ledger_path,
        quarter="2026Q3",
        apply=True,
    )

    with sqlite3.connect(db) as conn:
        # S 应有 4 个事件
        assert _count(conn, "project_events", f"canonical_id='{S}'") == 4
        # 新 id 应为 -03 和 -04
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM project_events WHERE canonical_id=? ORDER BY id", (S,)
            )
        ]
        assert f"{S}-03" in ids
        assert f"{S}-04" in ids
        # L 的旧 events 应不存在
        assert _count(conn, "project_events", f"id='{L}-01'") == 0
        # L excluded=1
        (excl, reason) = conn.execute(
            "SELECT excluded, excluded_reason FROM canonical_projects WHERE id=?", (L,)
        ).fetchone()
        assert excl == 1
        assert reason == f"merged_into:{S}"
        # 实体 event_id 全部跟随
        entity_event_ids = [
            r[0] for r in conn.execute("SELECT event_id FROM chinese_entities")
        ]
        assert all(eid.startswith(S) for eid in entity_event_ids)

    # 外键自检
    _integrity_check(db)

    # 台账记录
    ledger = load_ledger(ledger_path)
    assert any(
        e["status"] == "applied" for e in ledger["entries"].values()
    )

    assert report["done"] == 1
    assert report["skipped_idempotent"] == 0


# ──────────────────────────────────────────────
# T1 merge 幂等
# ──────────────────────────────────────────────


def test_t1_merge_idempotent(tmp_path: Path) -> None:
    """同 plan 重跑 → 0 变更，报告 skipped_idempotent。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S, L = "IND-PV-0001", "IND-PV-0002"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_project(conn, L)
        _insert_event(conn, f"{S}-01", S)
        _insert_event(conn, f"{L}-01", L)
        conn.commit()

    plan = _make_t1_plan("pair-001", S, L, S, L)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    # 第一次
    apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    # 第二次（幂等）
    report2 = apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    assert report2["done"] == 0
    assert report2["skipped_idempotent"] == 1


# ──────────────────────────────────────────────
# T1 merge 冲突
# ──────────────────────────────────────────────


def test_t1_merge_conflict(tmp_path: Path) -> None:
    """plan 含 A-B 与 B-C 两条 → 两条都 conflict_skipped，库零变更。"""
    db = tmp_path / "test.db"
    _make_db(db)
    A, B, C = "IND-PV-0001", "IND-PV-0002", "IND-PV-0003"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        for cid in [A, B, C]:
            _insert_project(conn, cid)
        for cid in [A, B, C]:
            _insert_event(conn, f"{cid}-01", cid)
        conn.commit()

    plan = (
        _make_t1_plan("pair-AB", A, B, A, B)
        + _make_t1_plan("pair-BC", B, C, B, C)
    )
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    # 两条都 conflict_skipped
    assert report["conflict_skipped"] == 2
    assert report["done"] == 0

    with sqlite3.connect(db) as conn:
        # 无 excluded
        assert _count(conn, "canonical_projects", "excluded=1") == 0
        # 所有事件仍在原 canonical_id
        assert _count(conn, "project_events", f"canonical_id='{A}'") == 1
        assert _count(conn, "project_events", f"canonical_id='{B}'") == 1
        assert _count(conn, "project_events", f"canonical_id='{C}'") == 1


# ──────────────────────────────────────────────
# NN 连续性（零填充）
# ──────────────────────────────────────────────


def test_t1_nn_sequential(tmp_path: Path) -> None:
    """S 已有 -01/-02，L 事件原 -01/-02 → 新 id -03/-04 零填充正确。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S, L = "IND-PV-0001", "IND-PV-0002"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_project(conn, L)
        _insert_event(conn, f"{S}-01", S)
        _insert_event(conn, f"{S}-02", S)
        _insert_event(conn, f"{L}-01", L)
        _insert_event(conn, f"{L}-02", L)
        conn.commit()

    plan = _make_t1_plan("pair-001", S, L, S, L)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    with sqlite3.connect(db) as conn:
        ids = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM project_events WHERE canonical_id=?", (S,)
            )
        }
    assert ids == {f"{S}-01", f"{S}-02", f"{S}-03", f"{S}-04"}


# ──────────────────────────────────────────────
# T2 exclude
# ──────────────────────────────────────────────


def test_t2_exclude(tmp_path: Path) -> None:
    """exclude 落 reason 带 quarter；重跑幂等。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, "IND-PV-0001")
        conn.commit()

    plan = _make_t2_plan("IND-PV-0001", "exclude", "fixed_list")
    plan_path = tmp_path / "apply_plan_T2.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = apply_t2(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    with sqlite3.connect(db) as conn:
        (excl, reason) = conn.execute(
            "SELECT excluded, excluded_reason FROM canonical_projects WHERE id='IND-PV-0001'"
        ).fetchone()
    assert excl == 1
    assert "codex_T2_exclude" in reason
    assert "2026Q3" in reason
    assert report["done"] == 1

    # 幂等
    report2 = apply_t2(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)
    assert report2["skipped_idempotent"] == 1
    assert report2["done"] == 0


def test_t2_downgrade(tmp_path: Path) -> None:
    """downgrade reason 正确。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, "IND-PV-0001")
        conn.commit()

    plan = _make_t2_plan("IND-PV-0001", "downgrade_non_project")
    plan_path = tmp_path / "apply_plan_T2.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t2(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    with sqlite3.connect(db) as conn:
        (reason,) = conn.execute(
            "SELECT excluded_reason FROM canonical_projects WHERE id='IND-PV-0001'"
        ).fetchone()
    assert "codex_T2_non_project" in reason
    assert "2026Q3" in reason


# ──────────────────────────────────────────────
# T3 additions 文件形状
# ──────────────────────────────────────────────


def test_t3_additions_shape(tmp_path: Path) -> None:
    """additions 文件形状对齐 draft 格式；不触库。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"
    runs_dir = tmp_path / "runs"

    plan = _make_t3_plan("测试公司甲", "new_group", "测试集团有限公司")
    plan_path = tmp_path / "apply_plan_T3.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = apply_t3(
        plan_path=plan_path,
        db_path=db,
        ledger_path=ledger_path,
        quarter="2026Q3",
        apply=True,
        runs_dir=runs_dir,
    )

    additions_path = runs_dir / "2026Q3" / "registry_additions_2026Q3.json"
    assert additions_path.exists()

    data = json.loads(additions_path.read_text(encoding="utf-8"))
    # 必须有 new_groups 和 additions_to_existing_groups
    assert "new_groups" in data
    assert "additions_to_existing_groups" in data
    # 有一条 new_group 条目
    assert len(data["new_groups"]) == 1
    entry = data["new_groups"][0]
    assert "canonical_name_cn" in entry
    assert "source_urls" in entry
    assert "notes" in entry

    # 不动库
    with sqlite3.connect(db) as conn:
        assert _count(conn, "canonical_projects") == 0

    # T3 不写台账
    ledger = load_ledger(ledger_path)
    assert len(ledger.get("entries", {})) == 0

    assert report["done"] >= 1


# ──────────────────────────────────────────────
# dry-run 零写入
# ──────────────────────────────────────────────


def test_dryrun_no_writes(tmp_path: Path) -> None:
    """dry-run：库文件内容前后一致（COUNT 断言），无 .bak 产生。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S, L = "IND-PV-0001", "IND-PV-0002"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_project(conn, L)
        _insert_event(conn, f"{S}-01", S)
        _insert_event(conn, f"{L}-01", L)
        conn.commit()

    plan = _make_t1_plan("pair-001", S, L, S, L)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    # dry-run（apply=False 为默认）
    apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=False)

    # 库无变更
    with sqlite3.connect(db) as conn:
        assert _count(conn, "canonical_projects", "excluded=1") == 0
        assert _count(conn, "project_events", f"canonical_id='{L}'") == 1

    # 无 .bak
    bak_files = list(tmp_path.glob("*.bak.*"))
    assert len(bak_files) == 0


# ──────────────────────────────────────────────
# 备份三重断言
# ──────────────────────────────────────────────


def test_backup_assertions(tmp_path: Path) -> None:
    """--apply 产生 .bak 且 COUNT 一致（三重断言）。"""
    db = tmp_path / "test.db"
    _make_db(db)
    with sqlite3.connect(db) as conn:
        _insert_project(conn, "IND-PV-0001")
        conn.commit()

    bak_path = _backup_db(db)
    # 断言1：存在
    assert Path(bak_path).exists()
    # 断言2：size > 0
    assert Path(bak_path).stat().st_size > 0
    # 断言3：可打开且 COUNT 一致
    with sqlite3.connect(db) as src_conn, sqlite3.connect(bak_path) as bak_conn:
        src_count = src_conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0]
        bak_count = bak_conn.execute("SELECT COUNT(*) FROM canonical_projects").fetchone()[0]
    assert src_count == bak_count


# ──────────────────────────────────────────────
# 台账记录（T1/T2 写入；T3 不写）
# ──────────────────────────────────────────────


def test_ledger_t1_written(tmp_path: Path) -> None:
    """T1 apply → 台账有 applied 状态记录。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S, L = "IND-PV-0001", "IND-PV-0002"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_project(conn, L)
        _insert_event(conn, f"{S}-01", S)
        _insert_event(conn, f"{L}-01", L)
        conn.commit()

    plan = _make_t1_plan("pair-001", S, L, S, L)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    ledger = load_ledger(ledger_path)
    applied = [e for e in ledger["entries"].values() if e["status"] == "applied"]
    assert len(applied) == 1


def test_ledger_t2_written(tmp_path: Path) -> None:
    """T2 apply → 台账有 applied 状态记录。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, "IND-PV-0001")
        conn.commit()

    plan = _make_t2_plan("IND-PV-0001", "exclude")
    plan_path = tmp_path / "apply_plan_T2.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t2(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    ledger = load_ledger(ledger_path)
    applied = [e for e in ledger["entries"].values() if e["status"] == "applied"]
    assert len(applied) == 1


def test_ledger_t3_not_written(tmp_path: Path) -> None:
    """T3 apply → 台账无记录（T3 天然幂等，不占台账）。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"
    runs_dir = tmp_path / "runs"

    plan = _make_t3_plan("测试公司乙", "new_group", "测试集团")
    plan_path = tmp_path / "apply_plan_T3.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t3(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True, runs_dir=runs_dir)

    ledger = load_ledger(ledger_path)
    assert len(ledger.get("entries", {})) == 0


# ──────────────────────────────────────────────
# 外键自检
# ──────────────────────────────────────────────


def test_integrity_check_passes(tmp_path: Path) -> None:
    """外键一致性检查：干净库通过。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S = "IND-PV-0001"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_event(conn, f"{S}-01", S)
        _insert_entity(conn, f"{S}-01")
        conn.commit()

    _integrity_check(db)  # 无异常即通过


def test_integrity_check_fails_on_orphan(tmp_path: Path) -> None:
    """外键自检：孤立 chinese_entities.event_id 导致异常。"""
    db = tmp_path / "test.db"
    _make_db(db)
    S = "IND-PV-0001"

    with sqlite3.connect(db) as conn:
        _insert_project(conn, S)
        _insert_event(conn, f"{S}-01", S)
        # 插入一个 event_id 不存在的 entity（孤立）
        conn.execute("INSERT INTO chinese_entities (event_id) VALUES (?)", ("NOT-EXIST-01",))
        conn.commit()

    with pytest.raises(RuntimeError):
        _integrity_check(db)


# ──────────────────────────────────────────────
# run_apply 集成（dry-run plan 文件缺失报错）
# ──────────────────────────────────────────────


def test_run_apply_missing_plan(tmp_path: Path) -> None:
    """plan 文件不存在 → ValueError（提示先跑 ingest --apply）。"""
    db = tmp_path / "test.db"
    _make_db(db)
    ledger_path = tmp_path / "ledger.json"

    with pytest.raises((ValueError, FileNotFoundError)):
        run_apply(
            task="T1",
            quarter="2026Q3",
            db_path=db,
            plan_path=tmp_path / "nonexistent.json",
            ledger_path=ledger_path,
            apply=False,
        )


# ──────────────────────────────────────────────
# T1 survivor 不属于 pair（应被跳过）
# ──────────────────────────────────────────────


def test_t1_survivor_not_in_pair(tmp_path: Path) -> None:
    """survivor_id 不属于 pair 双方 → conflict_skipped，库零变更。"""
    db = tmp_path / "test.db"
    _make_db(db)
    A, B, X = "IND-PV-0001", "IND-PV-0002", "IND-PV-0099"
    ledger_path = tmp_path / "ledger.json"

    with sqlite3.connect(db) as conn:
        for cid in [A, B]:
            _insert_project(conn, cid)
            _insert_event(conn, f"{cid}-01", cid)
        conn.commit()

    # survivor_id=X 不在 pair (A, B) 中
    plan = _make_t1_plan("pair-bad", X, B, A, B)
    plan_path = tmp_path / "apply_plan_T1.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    report = apply_t1(plan_path=plan_path, db_path=db, ledger_path=ledger_path, quarter="2026Q3", apply=True)

    assert report["conflict_skipped"] >= 1
    with sqlite3.connect(db) as conn:
        assert _count(conn, "canonical_projects", "excluded=1") == 0


# ──────────────────────────────────────────────
# T3 alias_of_existing 形状
# ──────────────────────────────────────────────


def test_t3_alias_shape(tmp_path: Path) -> None:
    """alias_of_existing → additions_to_existing_groups 里有对应条目。"""
    db = tmp_path / "test.db"
    _make_db(db)
    runs_dir = tmp_path / "runs"
    ledger_path = tmp_path / "ledger.json"

    plan = [
        {
            "verdict": {
                "raw_name": "测试别名",
                "classification": "alias_of_existing",
                "canonical_group_cn": "现有集团",
                "legal_full_name_cn": None,
                "parent_cn": None,
                "is_central_soe": None,
                "source_urls": ["https://example.com"],
                "confidence": "medium",
                "notes": "别名测试",
            },
            "context": {"freq": 2},
        }
    ]
    plan_path = tmp_path / "apply_plan_T3.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    apply_t3(
        plan_path=plan_path,
        db_path=db,
        ledger_path=ledger_path,
        quarter="2026Q3",
        apply=True,
        runs_dir=runs_dir,
    )

    additions_path = runs_dir / "2026Q3" / "registry_additions_2026Q3.json"
    data = json.loads(additions_path.read_text(encoding="utf-8"))
    assert len(data["additions_to_existing_groups"]) == 1
    assert data["additions_to_existing_groups"][0]["canonical_name_cn"] == "现有集团"
