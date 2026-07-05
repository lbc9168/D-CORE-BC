"""测试 codex_review/export_queue.py 的 T2 范围复核队列导出功能。"""
import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

from codex_review import export_queue as eq
from codex_review.ledger import load_ledger, record, save_ledger, t2_key
from codex_review.schemas import T2QueueFile


# ──────────────────────────────────────────────
# Fixture：最小三表 SQLite 数据库（复用 T1 建表结构）
# ──────────────────────────────────────────────

_PLACEHOLDER = "未具名中企"  # bri_enums.UNNAMED_ENTITY_PLACEHOLDER


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


def _insert_project(conn, pid, name, country="印度", iso="IND", cat="PV", excluded=0):
    """向 canonical_projects 插入一行。"""
    conn.execute(
        "INSERT INTO canonical_projects VALUES (?,?,?,?,?,?,?)",
        (pid, name, None, country, iso, cat, excluded),
    )


def _insert_event(conn, eid, cid, stage="建设中", pub=None, summary=None):
    """向 project_events 插入一行。"""
    conn.execute(
        "INSERT INTO project_events VALUES (?,?,?,?,?,?,?,?)",
        (eid, cid, stage, None, None, pub, summary, json.dumps([])),
    )


def _insert_entity(conn, eid, event_id, impl=None, sub=None):
    """向 chinese_entities 插入一行。"""
    conn.execute(
        "INSERT INTO chinese_entities VALUES (?,?,?,?)",
        (eid, event_id, impl, sub),
    )


# ──────────────────────────────────────────────
# Fixture：审计 CSV
# ──────────────────────────────────────────────

def _write_audit_csv(path: Path, rows: list[dict]) -> Path:
    """写一份最小审计 CSV（utf-8-sig），列名与真实 CSV 对齐。"""
    header = "序号,event_id,project_id,项目名,类别,字段,现值,建议值,动作,置信度,判断依据,原文证据,附带备注\n"
    lines = [header]
    for i, row in enumerate(rows, start=1):
        fields = [
            str(i),
            row.get("event_id", ""),
            row.get("project_id", ""),
            row.get("项目名", ""),
            row.get("类别", ""),
            row.get("字段", ""),
            row.get("现值", ""),
            row.get("建议值", ""),
            row.get("动作", ""),
            row.get("置信度", ""),
            row.get("判断依据", ""),
            row.get("原文证据", ""),
            row.get("附带备注", ""),
        ]
        lines.append(",".join(fields) + "\n")
    content = "".join(lines)
    path.write_bytes(b"\xef\xbb\xbf" + content.encode("utf-8"))  # utf-8-sig（BOM）
    return path


# ──────────────────────────────────────────────
# 辅助：调用 export_t2
# ──────────────────────────────────────────────

def _export(tmp_path, db_path, audit_csv_path, extra_ids=None, quarter="2026Q3"):
    """封装 export_t2 调用，返回 T2QueueFile。"""
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"
    eq.export_t2(
        quarter=quarter,
        db=db_path,
        audit_csv=audit_csv_path,
        extra_ids=extra_ids or [],
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data = json.loads((out_dir / quarter / "queue_T2.json").read_text(encoding="utf-8"))
    return T2QueueFile.model_validate(data), out_dir


# ──────────────────────────────────────────────
# 来源①：固定清单
# ──────────────────────────────────────────────


def test_fixed_list_basic(tmp_path):
    """来源①：audit CSV 中 2 行含"范围复核"→ 这 2 项 trigger=fixed_list，audit_note 来自备注。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_project(conn, "IND-WD-0001", "印度风电项目B", cat="WD")
    # IND-PV-0002 有具名实体，不含"范围复核"备注，不应被固定清单拾取
    _insert_project(conn, "IND-PV-0002", "印度光伏项目C")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "IND-WD-0001", pub="2023-02-01")
    _insert_event(conn, "E003", "IND-PV-0002", pub="2023-03-01")
    # IND-PV-0002 加具名实体，避免被来源②（无中企扫描）捕获
    _insert_entity(conn, "ENT003", "E003", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为国家规划"},
        {"project_id": "IND-WD-0001", "附带备注": "范围复核：无中企"},
        {"project_id": "IND-PV-0002", "附带备注": "容量核对"},  # 不含"范围复核"
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert q.task == "T2"
    item_ids = {it.project_id for it in q.items}
    # IND-PV-0001 和 IND-WD-0001 以 fixed_list 入队
    assert "IND-PV-0001" in item_ids
    assert "IND-WD-0001" in item_ids
    assert "IND-PV-0002" not in item_ids

    for it in q.items:
        if it.project_id in {"IND-PV-0001", "IND-WD-0001"}:
            assert it.trigger == "fixed_list"
            assert "范围复核" in (it.audit_note or "")


def test_fixed_list_audit_note_from_remark(tmp_path):
    """来源①：audit_note 字段来自 CSV 的"附带备注"列。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为国家规划"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 1
    assert "范围复核：疑为国家规划" in (q.items[0].audit_note or "")


def test_fixed_list_extra_ids(tmp_path):
    """来源①：--extra-ids 追加 1 个 → 该 id 入队，trigger=fixed_list。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_project(conn, "ISR-HB-0001", "以色列储能项目", country="以色列", iso="ISR", cat="HB")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "ISR-HB-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为国家规划"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv, extra_ids=["ISR-HB-0001"])
    item_ids = {it.project_id for it in q.items}
    assert "ISR-HB-0001" in item_ids
    extra_item = next(it for it in q.items if it.project_id == "ISR-HB-0001")
    assert extra_item.trigger == "fixed_list"


def test_fixed_list_excluded_skipped(tmp_path):
    """来源①：固定清单里 excluded=1 的 project_id 被跳过，不入队。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "已排除项目", excluded=1)
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：已排除"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 0


def test_fixed_list_unknown_id_logs_warning(tmp_path, caplog):
    """来源①：audit CSV 中查无此 project_id 时 log warning，不崩溃。"""
    import logging
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "GHOST-PV-9999", "附带备注": "范围复核：不存在"},
    ])

    with caplog.at_level(logging.WARNING, logger="codex_review.export_queue"):
        q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 0
    assert any("GHOST-PV-9999" in r.message for r in caplog.records)


# ──────────────────────────────────────────────
# 来源②：无中企扫描
# ──────────────────────────────────────────────


def test_no_entity_scan_zero_rows(tmp_path):
    """来源②：事件存在但 chinese_entities 无任何行 → 命中（视为无具名中企）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏无实体")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    # 不插入任何 chinese_entities
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" in item_ids
    hit = next(it for it in q.items if it.project_id == "IND-PV-0001")
    assert hit.trigger == "no_chinese_entity_scan"


def test_no_entity_scan_only_placeholder(tmp_path):
    """来源②：所有实体名均为"未具名中企"→ 命中。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏仅占位符")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_entity(conn, "ENT001", "E001", impl=_PLACEHOLDER)
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" in item_ids


def test_no_entity_scan_named_entity_not_hit(tmp_path):
    """来源②：存在具名中企 → 不命中。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏有具名实体")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" not in item_ids


def test_no_entity_scan_audit_note(tmp_path):
    """来源②：audit_note 固定为"无中企扫描：全部事件无具名中企实体"。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏无实体")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    hit = next((it for it in q.items if it.project_id == "IND-PV-0001"), None)
    assert hit is not None
    assert hit.audit_note == "无中企扫描：全部事件无具名中企实体"


# ──────────────────────────────────────────────
# 来源③：MF 边界扫描
# ──────────────────────────────────────────────


def test_mf_scan_summary_hit(tmp_path):
    """来源③：事件 project_summary 含"蓝氨"→ 命中，trigger=mf_boundary_scan。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度蓝氨项目")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01", summary="该项目生产蓝氨")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    hit = next((it for it in q.items if it.project_id == "IND-PV-0001"), None)
    assert hit is not None
    assert hit.trigger == "mf_boundary_scan"
    assert "蓝氨" in (hit.audit_note or "")


def test_mf_scan_project_name_hit(tmp_path):
    """来源③：project_name 含"电解铝"→ 命中。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度电解铝项目")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" in item_ids


def test_mf_scan_lithium_battery_not_hit(tmp_path):
    """来源③：project_summary 含"锂电池"（非关键词）→ 不命中（防关键词过宽）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度储能项目")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01", summary="配套锂电池储能装置")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" not in item_ids


def test_mf_scan_lithium_ore_hit(tmp_path):
    """来源③：project_name 含"锂矿"→ 命中。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度锂矿开采项目")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    item_ids = {it.project_id for it in q.items}
    assert "IND-PV-0001" in item_ids


# ──────────────────────────────────────────────
# 合并去重：同一 project 命中多来源
# ──────────────────────────────────────────────


def test_merge_dedup_fixed_and_no_entity(tmp_path):
    """合并去重：同一 project 命中固定清单+无中企 → 一条，trigger=fixed_list，note 拼接两来源说明。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏无实体且在清单")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    # 无 chinese_entities → 命中来源②
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为规划"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 1
    item = q.items[0]
    assert item.project_id == "IND-PV-0001"
    assert item.trigger == "fixed_list"  # 优先级 fixed_list > 其他
    assert "范围复核" in (item.audit_note or "")
    assert "无中企扫描" in (item.audit_note or "")


def test_merge_dedup_priority_order(tmp_path):
    """合并去重：fixed_list > mf_boundary_scan > no_chinese_entity_scan 优先级。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    # 命中 mf_boundary_scan（含"蓝氨"） 且 无实体（命中 no_chinese_entity_scan）
    _insert_project(conn, "IND-PV-0001", "印度蓝氨无实体项目")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01", summary="生产蓝氨")
    # 无实体行
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 1
    assert q.items[0].trigger == "mf_boundary_scan"  # mf > no_entity


# ──────────────────────────────────────────────
# 台账去重与幂等
# ──────────────────────────────────────────────


def test_ledger_dedup(tmp_path):
    """台账去重：预先 record 该条目的 t2_key → 重导出时被跳过，队列为空。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为规划"},
    ])

    # 先导出确认有 1 条
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"
    eq.export_t2(
        quarter="2026Q3",
        db=db_path,
        audit_csv=audit_csv,
        extra_ids=[],
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data = json.loads((out_dir / "2026Q3" / "queue_T2.json").read_text(encoding="utf-8"))
    assert len(data["items"]) == 1

    # 写入台账（使用 fixed_list trigger）
    ledger = load_ledger(ledger_path)
    key = t2_key("IND-PV-0001", "fixed_list")
    record(
        ledger,
        key,
        task="T2",
        quarter="2026Q3",
        item_id=key,
        status="accepted_no_change",
        verdict_digest="abc123",
    )
    save_ledger(ledger_path, ledger)

    # 重导出 → 空队列
    eq.export_t2(
        quarter="2026Q3",
        db=db_path,
        audit_csv=audit_csv,
        extra_ids=[],
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data2 = json.loads((out_dir / "2026Q3" / "queue_T2.json").read_text(encoding="utf-8"))
    q2 = T2QueueFile.model_validate(data2)
    assert len(q2.items) == 0


def test_idempotent(tmp_path):
    """幂等：连跑两次 items 一致（不比较 generated_at）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：疑为规划"},
    ])

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t2(
        quarter="2026Q3", db=db_path, audit_csv=audit_csv,
        extra_ids=[], out_dir=out_dir, ledger_path=ledger_path,
    )
    data1 = json.loads((out_dir / "2026Q3" / "queue_T2.json").read_text(encoding="utf-8"))

    eq.export_t2(
        quarter="2026Q3", db=db_path, audit_csv=audit_csv,
        extra_ids=[], out_dir=out_dir, ledger_path=ledger_path,
    )
    data2 = json.loads((out_dir / "2026Q3" / "queue_T2.json").read_text(encoding="utf-8"))

    assert data1["items"] == data2["items"]


# ──────────────────────────────────────────────
# 出口 Pydantic 校验通过
# ──────────────────────────────────────────────


def test_output_pydantic_valid(tmp_path):
    """出口校验：产物能被 T2QueueFile.model_validate 通过（含事件 + 实体名）。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    _insert_project(conn, "IND-PV-0001", "印度光伏项目A")
    _insert_event(conn, "E001", "IND-PV-0001", pub="2023-01-01")
    _insert_entity(conn, "ENT001", "E001", impl="中国电建集团")
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "IND-PV-0001", "附带备注": "范围复核：测试"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert q.task == "T2"
    assert len(q.items) == 1
    assert q.items[0].project.canonical_id == "IND-PV-0001"
    assert "中国电建集团" in q.items[0].project.entity_names


# ──────────────────────────────────────────────
# 排序：fixed_list 在前
# ──────────────────────────────────────────────


def test_sort_fixed_list_first(tmp_path):
    """排序：fixed_list 项目排在 no_chinese_entity_scan 项目前面。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    # ZZZ-PV-0001 字典序更大，但在固定清单里
    _insert_project(conn, "ZZZ-PV-0001", "Z项目在清单")
    _insert_project(conn, "AAA-PV-0001", "A项目无实体", country="阿根廷", iso="AAA")
    _insert_event(conn, "E001", "ZZZ-PV-0001", pub="2023-01-01")
    _insert_event(conn, "E002", "AAA-PV-0001", pub="2023-01-01")
    # AAA-PV-0001 无实体 → 来源②
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [
        {"project_id": "ZZZ-PV-0001", "附带备注": "范围复核：测试"},
    ])

    q, _ = _export(tmp_path, db_path, audit_csv)
    assert len(q.items) == 2
    assert q.items[0].trigger == "fixed_list"
    assert q.items[0].project_id == "ZZZ-PV-0001"


# ──────────────────────────────────────────────
# guide 文件被复制
# ──────────────────────────────────────────────


def test_guide_copied(tmp_path):
    """指南文件 guide_T2.md 被复制到 quarter 子目录。"""
    db_path = _make_db(tmp_path / "test.db")
    conn = sqlite3.connect(str(db_path))
    conn.commit()
    conn.close()

    audit_csv = _write_audit_csv(tmp_path / "audit.csv", [])

    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"
    eq.export_t2(
        quarter="2026Q3", db=db_path, audit_csv=audit_csv,
        extra_ids=[], out_dir=out_dir, ledger_path=ledger_path,
    )

    guide_dst = out_dir / "2026Q3" / "guide_T2.md"
    assert guide_dst.exists(), "guide_T2.md 应被复制到输出 quarter 目录"
