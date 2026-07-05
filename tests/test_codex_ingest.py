"""测试 codex_review/ingest_verdicts.py：校验、分级信任、15%抽检、dry-run/apply 模式。"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from codex_review.ledger import load_ledger, t2_key, t3_key
from codex_review.schemas import (
    T1QueueItem,
    T2QueueItem,
    T3QueueItem,
    ProjectContext,
    EventContext,
)
import codex_review.ingest_verdicts as iv

# ──────────────────────────────────────────────
# 工厂辅助
# ──────────────────────────────────────────────

QUARTER = "2026Q3"


def _proj(cid: str = "IND-PV-0001", name: str = "测试项目") -> dict:
    """构造最小 ProjectContext dict。"""
    return {
        "canonical_id": cid,
        "project_name": name,
        "country": "印度",
        "country_iso": "IND",
        "category": "PV",
        "events": [{"event_id": "E001", "project_stage": "construction"}],
        "entity_names": [],
    }


def _t1_queue(pair_id: str, cid_a: str = "IND-PV-0001", cid_b: str = "IND-PV-0002") -> dict:
    """构造一条 T1QueueItem dict。"""
    return {
        "pair_id": pair_id,
        "similarity": 0.9,
        "both_sides_visible": True,
        "a": _proj(cid_a, "项目A"),
        "b": _proj(cid_b, "项目B"),
    }


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


def _t1_verdict(pair_id: str, same: bool = False, survivor: str | None = None) -> dict:
    """构造 T1Verdict dict。"""
    v: dict = {
        "pair_id": pair_id,
        "same_project": same,
        "confidence": "high",
        "evidence_quote": "引用文本",
        "reason": "判断原因",
    }
    if same:
        v["survivor_id"] = survivor or "IND-PV-0001"
    else:
        v["survivor_id"] = None
    return v


def _t2_verdict(project_id: str, action: str = "keep") -> dict:
    """构造 T2Verdict dict。"""
    return {
        "project_id": project_id,
        "action": action,
        "reason": "判断原因",
        "evidence_quote": "引用文本",
    }


def _t3_verdict(raw_name: str, classification: str = "not_chinese",
                urls: list[str] | None = None, group: str | None = None) -> dict:
    """构造 T3Verdict dict。"""
    v: dict = {
        "raw_name": raw_name,
        "classification": classification,
        "canonical_group_cn": group,
        "legal_full_name_cn": None,
        "parent_cn": None,
        "is_central_soe": None,
        "source_urls": urls or [],
        "confidence": "high",
        "notes": "备注",
    }
    return v


def _run(
    task: str,
    verdicts_path: Path,
    queue_path: Path,
    ledger_path: Path,
    *,
    apply: bool = False,
    sample_rate: float = 0.15,
) -> iv.IngestResult:
    """调用 ingest_verdicts.run_ingest，返回结果。"""
    return iv.run_ingest(
        task=task,
        quarter=QUARTER,
        verdicts_path=verdicts_path,
        queue_path=queue_path,
        ledger_path=ledger_path,
        apply=apply,
        sample_rate=sample_rate,
    )


# ──────────────────────────────────────────────
# 信封 task 不符 → 非零退出
# ──────────────────────────────────────────────


def test_envelope_task_mismatch_raises(tmp_path):
    """信封 task 与 CLI 不符时，应抛 SystemExit（非零）。"""
    q = tmp_path / "queue_T1.json"
    v = tmp_path / "verdicts_T1.json"
    _write_queue(q, "T1", [])
    # task=T2 写入，但以 T1 导入
    v.write_text(
        json.dumps({"task": "T2", "quarter": QUARTER, "verdicts": []}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        iv.run_ingest("T1", QUARTER, v, q, tmp_path / "ledger.json")
    assert exc_info.value.code != 0


def test_envelope_quarter_mismatch_raises(tmp_path):
    """信封 quarter 与 CLI 不符时，应抛 SystemExit（非零）。"""
    q = tmp_path / "queue_T3.json"
    v = tmp_path / "verdicts_T3.json"
    _write_queue(q, "T3", [])
    v.write_text(
        json.dumps({"task": "T3", "quarter": "2025Q1", "verdicts": []}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        iv.run_ingest("T3", QUARTER, v, q, tmp_path / "ledger.json")
    assert exc_info.value.code != 0


# ──────────────────────────────────────────────
# 拒收：schema 非法（枚举外 action）
# ──────────────────────────────────────────────


def test_reject_invalid_action(tmp_path):
    """T2 verdict action 为非法枚举值时，该条被拒收，其余正常处理。"""
    pid_good = "IND-PV-0001"
    pid_bad = "EGY-WD-0002"
    # 队列含两条
    q_items = [
        {"item_id": t2_key(pid_good, "fixed_list"), "project_id": pid_good,
         "trigger": "fixed_list", "audit_note": None, "project": _proj(pid_good)},
        {"item_id": t2_key(pid_bad, "fixed_list"), "project_id": pid_bad,
         "trigger": "fixed_list", "audit_note": None, "project": _proj(pid_bad)},
    ]
    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", q_items)

    bad_verdict = {"project_id": pid_bad, "action": "INVALID_ACTION",
                   "reason": "ok", "evidence_quote": "ok"}
    good_verdict = _t2_verdict(pid_good, "keep")

    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", [bad_verdict, good_verdict])

    result = _run("T2", v, q, tmp_path / "ledger.json")

    assert any(r["id"] == pid_bad for r in result.rejected)
    assert any(a["id"] == pid_good for a in result.auto_accepted)


# ──────────────────────────────────────────────
# 拒收：id 不在队列
# ──────────────────────────────────────────────


def test_reject_id_not_in_queue(tmp_path):
    """T3 verdict raw_name 不在队列时，该条被拒收。"""
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [])  # 空队列

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [_t3_verdict("不存在的实体")])

    result = _run("T3", v, q, tmp_path / "ledger.json")

    assert len(result.rejected) == 1
    assert "not_in_queue" in result.rejected[0]["reason"]


# ──────────────────────────────────────────────
# 拒收：重复 id（第一条有效，后续拒收）
# ──────────────────────────────────────────────


def test_reject_duplicate_id(tmp_path):
    """相同 raw_name 的第二条 T3 verdict 被拒收（reason=duplicate）。"""
    raw = "中国某公司"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 5, "registry_groups": []}
    ])

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [
        _t3_verdict(raw, "not_chinese"),
        _t3_verdict(raw, "not_company"),  # 重复
    ])

    result = _run("T3", v, q, tmp_path / "ledger.json")

    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"] == "duplicate"
    # 第一条应自动收
    assert len(result.auto_accepted) == 1


# ──────────────────────────────────────────────
# 拒收：T1 survivor_id 不属于 pair
# ──────────────────────────────────────────────


def test_reject_t1_survivor_not_in_pair(tmp_path):
    """T1 same_project=True 但 survivor_id 不是 a/b 的 canonical_id，应拒收。"""
    from codex_review.ledger import t1_key as _t1k
    from codex_review.schemas import ProjectContext

    cid_a, cid_b = "IND-PV-0001", "IND-PV-0002"
    a = ProjectContext(**_proj(cid_a, "项目A"))
    b = ProjectContext(**_proj(cid_b, "项目B"))
    pair_id = _t1k(a, b)

    q = tmp_path / "queue_T1.json"
    _write_queue(q, "T1", [_t1_queue(pair_id, cid_a, cid_b)])

    # survivor_id 不属于 a/b
    bad = _t1_verdict(pair_id, same=True, survivor="EGY-WD-9999")
    v = tmp_path / "verdicts_T1.json"
    _write_verdicts(v, "T1", [bad])

    result = _run("T1", v, q, tmp_path / "ledger.json")

    assert len(result.rejected) == 1
    assert "survivor" in result.rejected[0]["reason"]


# ──────────────────────────────────────────────
# 拒收：T3 归属类 source_url 非 http
# ──────────────────────────────────────────────


def test_reject_t3_invalid_source_url(tmp_path):
    """T3 归属类 source_urls 含非 http/https URL，应拒收（reason=invalid_source_url）。"""
    raw = "中国电建"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 3, "registry_groups": []}
    ])

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [
        _t3_verdict(raw, "alias_of_existing",
                    urls=["ftp://bad.com"],  # 非 http/https
                    group="中国电力建设集团")
    ])

    result = _run("T3", v, q, tmp_path / "ledger.json")

    assert len(result.rejected) == 1
    assert result.rejected[0]["reason"] == "invalid_source_url"


# ──────────────────────────────────────────────
# 分级信任：自动收（不改数据）
# ──────────────────────────────────────────────


def test_auto_accept_t1_false_apply(tmp_path):
    """T1 same_project=False → --apply 后台账有 accepted_no_change 记录。"""
    from codex_review.ledger import t1_key as _t1k
    from codex_review.schemas import ProjectContext

    cid_a, cid_b = "IND-PV-0001", "IND-PV-0002"
    a = ProjectContext(**_proj(cid_a))
    b = ProjectContext(**_proj(cid_b))
    pair_id = _t1k(a, b)

    q = tmp_path / "queue_T1.json"
    _write_queue(q, "T1", [_t1_queue(pair_id, cid_a, cid_b)])

    v = tmp_path / "verdicts_T1.json"
    _write_verdicts(v, "T1", [_t1_verdict(pair_id, same=False)])

    ledger_path = tmp_path / "ledger.json"
    _run("T1", v, q, ledger_path, apply=True)

    ledger = load_ledger(ledger_path)
    assert pair_id in ledger["entries"]
    assert ledger["entries"][pair_id]["status"] == "accepted_no_change"


def test_auto_accept_t2_keep_apply(tmp_path):
    """T2 action=keep → --apply 后台账有 accepted_no_change 记录。"""
    pid = "IND-PV-0001"
    item_id = t2_key(pid, "fixed_list")
    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", [
        {"item_id": item_id, "project_id": pid, "trigger": "fixed_list",
         "audit_note": None, "project": _proj(pid)}
    ])

    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", [_t2_verdict(pid, "keep")])

    ledger_path = tmp_path / "ledger.json"
    _run("T2", v, q, ledger_path, apply=True)

    ledger = load_ledger(ledger_path)
    assert item_id in ledger["entries"]
    assert ledger["entries"][item_id]["status"] == "accepted_no_change"


def test_auto_accept_t3_not_chinese_apply(tmp_path):
    """T3 classification=not_chinese → --apply 后台账有 accepted_no_change 记录。"""
    raw = "某外企"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 2, "registry_groups": []}
    ])

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [_t3_verdict(raw, "not_chinese")])

    ledger_path = tmp_path / "ledger.json"
    _run("T3", v, q, ledger_path, apply=True)

    ledger = load_ledger(ledger_path)
    assert key in ledger["entries"]
    assert ledger["entries"][key]["status"] == "accepted_no_change"


# ──────────────────────────────────────────────
# 分级信任：uncertain → 跳过（不入台账，报告有 skipped_uncertain）
# ──────────────────────────────────────────────


def test_t3_uncertain_skipped(tmp_path):
    """T3 uncertain → 台账无记录，报告有 skipped_uncertain 条目。"""
    raw = "未知公司X"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 1, "registry_groups": []}
    ])

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [_t3_verdict(raw, "uncertain")])

    ledger_path = tmp_path / "ledger.json"
    result = _run("T3", v, q, ledger_path, apply=True)

    ledger = load_ledger(ledger_path)
    assert key not in ledger["entries"]
    assert any(s == raw or s.get("id") == raw or raw in str(s) for s in result.skipped_uncertain)


# ──────────────────────────────────────────────
# 分级信任：改数据类（gated）→ 进 apply_plan，台账无记录
# ──────────────────────────────────────────────


def test_gated_t1_merge_no_ledger_entry(tmp_path):
    """T1 same_project=True → --apply 后台账无记录，apply_plan 文件存在。"""
    from codex_review.ledger import t1_key as _t1k
    from codex_review.schemas import ProjectContext

    cid_a, cid_b = "IND-PV-0001", "IND-PV-0002"
    a = ProjectContext(**_proj(cid_a))
    b = ProjectContext(**_proj(cid_b))
    pair_id = _t1k(a, b)

    q = tmp_path / "queue_T1.json"
    _write_queue(q, "T1", [_t1_queue(pair_id, cid_a, cid_b)])

    v = tmp_path / "verdicts_T1.json"
    _write_verdicts(v, "T1", [_t1_verdict(pair_id, same=True, survivor=cid_a)])

    ledger_path = tmp_path / "ledger.json"
    out_dir = tmp_path / "runs"
    result = iv.run_ingest(
        task="T1",
        quarter=QUARTER,
        verdicts_path=v,
        queue_path=q,
        ledger_path=ledger_path,
        apply=True,
        sample_rate=1.0,  # 100% 抽检
        runs_dir=out_dir,
    )

    ledger = load_ledger(ledger_path)
    assert pair_id not in ledger["entries"]
    # apply_plan 文件存在
    plan_file = out_dir / QUARTER / "apply_plan_T1.json"
    assert plan_file.exists()


def test_gated_t2_exclude_no_ledger_entry(tmp_path):
    """T2 action=exclude → --apply 后台账无记录，apply_plan 文件存在。"""
    pid = "IND-PV-0001"
    item_id = t2_key(pid, "fixed_list")
    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", [
        {"item_id": item_id, "project_id": pid, "trigger": "fixed_list",
         "audit_note": None, "project": _proj(pid)}
    ])

    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", [_t2_verdict(pid, "exclude")])

    ledger_path = tmp_path / "ledger.json"
    out_dir = tmp_path / "runs"
    iv.run_ingest(
        task="T2",
        quarter=QUARTER,
        verdicts_path=v,
        queue_path=q,
        ledger_path=ledger_path,
        apply=True,
        sample_rate=1.0,
        runs_dir=out_dir,
    )

    ledger = load_ledger(ledger_path)
    assert item_id not in ledger["entries"]
    plan_file = out_dir / QUARTER / "apply_plan_T2.json"
    assert plan_file.exists()


def test_gated_t3_alias_no_ledger_entry(tmp_path):
    """T3 alias_of_existing → --apply 后台账无记录，apply_plan 文件存在。"""
    raw = "中国电建子公司"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 5, "registry_groups": ["中国电力建设集团有限公司"]}
    ])

    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [
        _t3_verdict(raw, "alias_of_existing",
                    urls=["https://example.com"],
                    group="中国电力建设集团有限公司")
    ])

    ledger_path = tmp_path / "ledger.json"
    out_dir = tmp_path / "runs"
    iv.run_ingest(
        task="T3",
        quarter=QUARTER,
        verdicts_path=v,
        queue_path=q,
        ledger_path=ledger_path,
        apply=True,
        sample_rate=1.0,
        runs_dir=out_dir,
    )

    ledger = load_ledger(ledger_path)
    assert key not in ledger["entries"]
    plan_file = out_dir / QUARTER / "apply_plan_T3.json"
    assert plan_file.exists()


# ──────────────────────────────────────────────
# dry-run 不落任何持久文件（台账/apply_plan/manual_check），但报告生成
# ──────────────────────────────────────────────


def test_dry_run_no_persistent_writes(tmp_path):
    """dry-run 模式：台账不变、无 apply_plan、无 manual_check，但报告生成。"""
    pid = "IND-PV-0001"
    item_id = t2_key(pid, "fixed_list")
    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", [
        {"item_id": item_id, "project_id": pid, "trigger": "fixed_list",
         "audit_note": None, "project": _proj(pid)}
    ])

    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", [_t2_verdict(pid, "exclude")])

    ledger_path = tmp_path / "ledger.json"
    out_dir = tmp_path / "runs"

    iv.run_ingest(
        task="T2",
        quarter=QUARTER,
        verdicts_path=v,
        queue_path=q,
        ledger_path=ledger_path,
        apply=False,
        runs_dir=out_dir,
    )

    # 台账不存在（未创建）
    assert not ledger_path.exists()
    # 无 apply_plan
    assert not (out_dir / QUARTER / "apply_plan_T2.json").exists()
    # 无 manual_check
    assert not any((out_dir / QUARTER).glob("manual_check_*.csv")) if (out_dir / QUARTER).exists() else True
    # 报告生成
    report = out_dir / QUARTER / "ingest_report_T2.md"
    assert report.exists()


# ──────────────────────────────────────────────
# 幂等：--apply 跑两次，第二次自动收新增 0
# ──────────────────────────────────────────────


def test_idempotent_apply(tmp_path):
    """--apply 跑两次，第二次自动收新增 0，台账条目数不变。"""
    raw = "某自动收实体"
    key = t3_key(raw)
    q = tmp_path / "queue_T3.json"
    _write_queue(q, "T3", [
        {"item_id": key, "raw_name": raw, "freq": 1, "registry_groups": []}
    ])
    v = tmp_path / "verdicts_T3.json"
    _write_verdicts(v, "T3", [_t3_verdict(raw, "not_company")])

    ledger_path = tmp_path / "ledger.json"
    out_dir = tmp_path / "runs"

    r1 = iv.run_ingest("T3", QUARTER, v, q, ledger_path, apply=True, runs_dir=out_dir)
    ledger_after_1 = load_ledger(ledger_path)
    count_1 = len(ledger_after_1["entries"])

    r2 = iv.run_ingest("T3", QUARTER, v, q, ledger_path, apply=True, runs_dir=out_dir)
    ledger_after_2 = load_ledger(ledger_path)
    count_2 = len(ledger_after_2["entries"])

    assert count_1 == count_2
    assert r2.skipped_already_settled == 1  # 第二次整条跳过


# ──────────────────────────────────────────────
# 抽样：确定性 + ceil + 至少 1
# ──────────────────────────────────────────────


def test_sampling_deterministic(tmp_path):
    """相同 task+quarter 两次 --apply 抽中的 id 集合一致。"""
    # 构造 5 条 gated T2 verdicts（exclude）
    pids = [f"IND-PV-000{i}" for i in range(1, 6)]
    q_items = [
        {"item_id": t2_key(p, "fixed_list"), "project_id": p, "trigger": "fixed_list",
         "audit_note": None, "project": _proj(p)}
        for p in pids
    ]
    verdicts = [_t2_verdict(p, "exclude") for p in pids]

    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", q_items)
    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", verdicts)
    out_dir = tmp_path / "runs"

    def _sampled_ids(ledger_path: Path) -> set[str]:
        iv.run_ingest("T2", QUARTER, v, q, ledger_path, apply=True,
                      sample_rate=0.4, runs_dir=out_dir)
        csv_path = out_dir / QUARTER / "manual_check_T2.csv"
        if not csv_path.exists():
            return set()
        with open(csv_path, newline="", encoding="utf-8") as f:
            return {row["id"] for row in csv.DictReader(f)}

    ids_1 = _sampled_ids(tmp_path / "ledger1.json")
    ids_2 = _sampled_ids(tmp_path / "ledger2.json")
    assert ids_1 == ids_2


def test_sampling_ceil_at_least_one(tmp_path):
    """n=1, rate=0.15 → 抽 1（ceil(0.15)=1，至少 1 条）。"""
    pid = "IND-PV-0001"
    item_id = t2_key(pid, "fixed_list")
    q = tmp_path / "queue_T2.json"
    _write_queue(q, "T2", [
        {"item_id": item_id, "project_id": pid, "trigger": "fixed_list",
         "audit_note": None, "project": _proj(pid)}
    ])
    v = tmp_path / "verdicts_T2.json"
    _write_verdicts(v, "T2", [_t2_verdict(pid, "exclude")])

    out_dir = tmp_path / "runs"
    iv.run_ingest("T2", QUARTER, v, q, tmp_path / "ledger.json",
                  apply=True, sample_rate=0.15, runs_dir=out_dir)

    csv_path = out_dir / QUARTER / "manual_check_T2.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
