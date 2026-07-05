"""测试 codex_review/export_queue.py 的 T1 漏合并队列导出功能。"""
import json
import sqlite3
from pathlib import Path

import pytest

from codex_review import export_queue as eq
from codex_review.ledger import load_ledger, record, save_ledger, t1_key
from codex_review.schemas import T1QueueFile


# ──────────────────────────────────────────────
# Fixture: 最小三表 SQLite 数据库
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
            excluded INTEGER NOT NULL DEFAULT 0
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


def _insert_project(conn, pid, name, name_en, country, iso, cat, excluded=0):
    """向 canonical_projects 插入一行。"""
    conn.execute(
        "INSERT INTO canonical_projects VALUES (?,?,?,?,?,?,?)",
        (pid, name, name_en, country, iso, cat, excluded),
    )


def _insert_event(conn, eid, cid, stage="建设中", cap=None, cap_raw=None,
                  pub=None, summary=None, links=None):
    """向 project_events 插入一行。"""
    conn.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)",
        (eid, cid, stage, cap, cap_raw, pub, summary,
         json.dumps(links or [], ensure_ascii=False)),
    )


def _insert_entity(conn, eid, event_id, impl=None, sub=None):
    """向 chinese_entities 插入一行。"""
    conn.execute(
        "INSERT INTO chinese_entities VALUES (?,?,?,?)",
        (eid, event_id, impl, sub),
    )


# ──────────────────────────────────────────────
# 命中：同国同类高相似名 → 入队
# ──────────────────────────────────────────────


def test_hit_high_similarity(tmp_path):
    """命中：同国同类、名称高相似 → 成对入队，similarity>0.85，events/entity_names 组装正确。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    # 两个高相似名称（token_sort_ratio ~96，可靠超阈值）
    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", "Vietnam 50MW PV",
                    "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", "Ninh Thuan 50MW PV",
                    "越南", "VNM", "光伏")

    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01",
                  links=["https://example.com/1"])
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-06-01",
                  links=["https://example.com/2"])

    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    _insert_entity(conn, "ENT002", "E002", impl="中国电建股份")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    queue_file = out_dir / "2026Q3" / "queue_T1.json"
    assert queue_file.exists()

    data = json.loads(queue_file.read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert q.task == "T1"
    assert q.quarter == "2026Q3"
    assert len(q.items) == 1

    item = q.items[0]
    assert item.similarity > 0.85

    # 双方 events 组装正确
    ids = {item.a.canonical_id, item.b.canonical_id}
    assert ids == {"VNM-PV-0001", "VNM-PV-0002"}

    side_a = item.a if item.a.canonical_id == "VNM-PV-0001" else item.b
    assert len(side_a.events) == 1
    assert side_a.events[0].source_links == ["https://example.com/1"]
    assert "中国电建集团" in side_a.entity_names


# ──────────────────────────────────────────────
# 不命中：不同国家
# ──────────────────────────────────────────────


def test_no_hit_different_country(tmp_path):
    """不命中：同类但不同国家同名 → 不成对。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "宁顺50MW光伏", None, "越南", "VNM", "光伏")
    _insert_project(conn, "THA-PV-0001", "宁顺50MW光伏", None, "泰国", "THA", "光伏")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 0


# ──────────────────────────────────────────────
# 不命中：低相似度
# ──────────────────────────────────────────────


def test_no_hit_low_similarity(tmp_path):
    """不命中：同国同类但名称差异大 → 低相似度不成对。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南北部大坝水电站", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "宁顺沿海风电场", None, "越南", "VNM", "光伏")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 0


# ──────────────────────────────────────────────
# 不命中：excluded=1 不参与
# ──────────────────────────────────────────────


def test_excluded_not_paired(tmp_path):
    """excluded=1 的记录不参与比对。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏",
                    excluded=1)

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 0


# ──────────────────────────────────────────────
# both_sides_visible
# ──────────────────────────────────────────────


def test_both_sides_visible_true(tmp_path):
    """both_sides_visible=True：双方均有 >=2022 事件 + 具名实体 + 正常类别 + 非大陆港澳台。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")

    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-05-01")
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-06-01")

    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    _insert_entity(conn, "ENT002", "E002", impl="中国能建集团")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 1
    assert q.items[0].both_sides_visible is True


def test_both_sides_visible_false_no_recent_event(tmp_path):
    """both_sides_visible=False：其中一侧缺乏 >=2022 的事件。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")

    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-05-01")
    # 一侧没有 2022+ 事件（只有 2021 的）
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2021-06-01")

    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    _insert_entity(conn, "ENT002", "E002", impl="中国能建集团")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 1
    assert q.items[0].both_sides_visible is False


# ──────────────────────────────────────────────
# 台账去重
# ──────────────────────────────────────────────


def test_ledger_dedup(tmp_path):
    """台账去重：预先 record 该 pair 的 t1_key → 重导出为空队列。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-01-01")

    conn.commit()
    conn.close()

    # 先正常导出，拿到 pair_id
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == 1
    pair_id = data["items"][0]["pair_id"]

    # 把 pair 记入台账
    ledger = load_ledger(ledger_path)
    record(
        ledger,
        pair_id,
        task="T1",
        quarter="2026Q3",
        item_id=pair_id,
        status="accepted_no_change",
        verdict_digest="abc123",
    )
    save_ledger(ledger_path, ledger)

    # 重导出 → 空队列
    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data2 = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q2 = T1QueueFile.model_validate(data2)
    assert len(q2.items) == 0


# ──────────────────────────────────────────────
# 幂等
# ──────────────────────────────────────────────


def test_idempotent(tmp_path):
    """幂等：连跑两次 items 一致（不比较 generated_at）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-01-01")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data1 = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data2 = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))

    assert data1["items"] == data2["items"]


# ──────────────────────────────────────────────
# 出口校验：Pydantic 通过
# ──────────────────────────────────────────────


def test_output_pydantic_valid(tmp_path):
    """出口校验：产物能被 T1QueueFile.model_validate 通过。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-01-01")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    # 不抛即通过
    T1QueueFile.model_validate(data)


# ──────────────────────────────────────────────
# 只读验证：mode=ro 成功导出，不写库
# ──────────────────────────────────────────────


def test_readonly_db(tmp_path):
    """只读验证：用只读 URI 打开 fixture 库能成功导出（导出器绝不写库）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "VNM-PV-0002", pub="2023-01-01")

    conn.commit()
    conn.close()

    mtime_before = db_path.stat().st_mtime

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    # 库文件 mtime 不应改变
    assert db_path.stat().st_mtime == mtime_before

    # 产物存在
    queue_file = out_dir / "2026Q3" / "queue_T1.json"
    assert queue_file.exists()


# ──────────────────────────────────────────────
# source_links JSON 解析失败 → 按空列表（不崩）
# ──────────────────────────────────────────────


def test_malformed_source_links(tmp_path):
    """source_links 字段非合法 JSON → 按空列表处理，不崩溃。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", "越南宁顺省50MW光伏项目", None, "越南", "VNM", "光伏")

    # 故意插入非法 JSON
    conn.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)",
        ("E001", "VNM-PV-0001", "建设中", None, None, "2023-01-01", None, "NOT_JSON"),
    )
    conn.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)",
        ("E002", "VNM-PV-0002", "建设中", None, None, "2023-01-01", None, "NOT_JSON"),
    )

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 1
    # source_links 应降级为空列表
    for side in (q.items[0].a, q.items[0].b):
        for ev in side.events:
            assert ev.source_links == []


# ──────────────────────────────────────────────
# 名称为空的行跳过比对
# ──────────────────────────────────────────────


def test_null_name_skipped(tmp_path):
    """名称为 NULL 的行不参与比对（即使与另一行名称相似，也应跳过）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))

    # 一条正常，一条名称为 NULL
    _insert_project(conn, "VNM-PV-0001", "越南宁顺50MW光伏项目", None, "越南", "VNM", "光伏")
    _insert_project(conn, "VNM-PV-0002", None, None, "越南", "VNM", "光伏")

    _insert_event(conn, "E001", "VNM-PV-0001", pub="2023-01-01")

    conn.commit()
    conn.close()

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t1(
        quarter="2026Q3",
        db=db_path,
        threshold=85,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T1.json").read_text(encoding="utf-8"))
    q = T1QueueFile.model_validate(data)
    assert len(q.items) == 0
