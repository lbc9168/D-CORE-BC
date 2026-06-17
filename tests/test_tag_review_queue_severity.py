"""tag_review_queue_severity：给 review_queue 加 severity + 回填 current_value 的纯 DB 行为。"""
import sqlite3
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
_oneoff = _root / "scripts" / "oneoff"
if str(_oneoff) not in sys.path:
    sys.path.insert(0, str(_oneoff))

import tag_review_queue_severity as trq


def _make_dbs(tmp_path):
    """建 projects.db(extracted_projects) + review.db(review_queue)，返回两路径。"""
    pdb = tmp_path / "projects.db"
    pc = sqlite3.connect(pdb)
    pc.execute(
        "CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, category TEXT, "
        "capacity_mw REAL, involvement_type TEXT, project_name TEXT)"
    )
    pc.executemany(
        "INSERT INTO extracted_projects VALUES (?,?,?,?,?)",
        [
            (10, "火电", 100.0, "EPC总承包", "塔尔煤矿"),
            (11, "光伏", 50.0, "EPC总承包", "A电站"),
            (12, "风电", 200.0, "EPC总承包", "B风电"),
        ],
    )
    pc.commit(); pc.close()

    rdb = tmp_path / "review.db"
    rc = sqlite3.connect(rdb)
    rc.execute(
        """CREATE TABLE review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id TEXT, source_link TEXT,
            reason_type TEXT NOT NULL, field TEXT, current_value TEXT, suggested_value TEXT,
            critic_confidence REAL, evidence TEXT, state TEXT NOT NULL DEFAULT 'pending', created_at TEXT)"""
    )
    rc.executemany(
        "INSERT INTO review_queue (canonical_id, reason_type, field, suggested_value, critic_confidence) "
        "VALUES (?,?,?,?,?)",
        [
            ("10", "low_confidence_field", "category", "石油开采业", 1.0),    # 改非RE → 高
            ("11", "low_confidence_field", "capacity_mw", "", 1.0),          # 无建议 → 低
            ("12", "low_confidence_field", "involvement_type", "设计/施工服务方", 0.9),  # 真角色换 → 中
            ("12", "exclude_suggested", "status", "exclude", 0.9),           # → 高
            ("11", "unnamed_entity", "implementing_entity", "", 1.0),        # → NULL
        ],
    )
    rc.commit(); rc.close()
    return str(rdb), str(pdb)


def _fetch(rdb):
    conn = sqlite3.connect(rdb)
    conn.row_factory = sqlite3.Row
    rows = {r["id"]: r for r in conn.execute("SELECT * FROM review_queue")}
    conn.close()
    return rows


def test_severity_and_current_value_written(tmp_path):
    """每条按 reason_type+字段算出正确 severity；current_value 从 projects.db 回填。"""
    rdb, pdb = _make_dbs(tmp_path)
    trq.backfill_and_tag(rdb, pdb, dry_run=False)
    rows = _fetch(rdb)
    # severity
    assert rows[1]["severity"] == "高"   # category→非RE
    assert rows[2]["severity"] == "低"   # capacity 无建议
    assert rows[3]["severity"] == "中"   # involvement 真角色换
    assert rows[4]["severity"] == "高"   # exclude_suggested
    assert rows[5]["severity"] is None   # unnamed_entity 单列
    # current_value 回填
    assert rows[1]["current_value"] == "火电"
    assert rows[2]["current_value"] == "50.0"
    assert rows[3]["current_value"] == "EPC总承包"


def test_dry_run_writes_nothing(tmp_path):
    """dry_run=True：返回计数但不写库（severity 仍为 NULL/列不存在）。"""
    rdb, pdb = _make_dbs(tmp_path)
    counts = trq.backfill_and_tag(rdb, pdb, dry_run=True)
    assert sum(counts.values()) == 5
    rows = _fetch(rdb)
    # severity 列已加（ALTER 非 dry-run 范畴），但值未写 → 仍为 None
    assert all(r["severity"] is None for r in rows.values())
    assert all((r["current_value"] in (None, "")) for r in rows.values())


def test_idempotent_rerun(tmp_path):
    """重复跑：列已存在不报错，结果一致。"""
    rdb, pdb = _make_dbs(tmp_path)
    trq.backfill_and_tag(rdb, pdb, dry_run=False)
    trq.backfill_and_tag(rdb, pdb, dry_run=False)  # 第二遍 ALTER 应被静默
    rows = _fetch(rdb)
    assert rows[1]["severity"] == "高"
    assert rows[3]["severity"] == "中"


def test_scope_count(tmp_path):
    """高+中 计数 = 3（category高 + involvement中 + exclude高）。"""
    rdb, pdb = _make_dbs(tmp_path)
    counts = trq.backfill_and_tag(rdb, pdb, dry_run=False)
    scope = sum(n for (rt, sev), n in counts.items() if sev in ("高", "中"))
    assert scope == 3
