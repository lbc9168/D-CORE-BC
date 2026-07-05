"""测试 codex_review/export_queue.py 的 T3 实体归属补录队列导出功能。"""
import json
import textwrap
from pathlib import Path

import pytest

from codex_review import export_queue as eq
from codex_review.ledger import load_ledger, record, save_ledger, t3_key
from codex_review.schemas import T3QueueFile

# ──────────────────────────────────────────────
# 构造假输入的工厂函数
# ──────────────────────────────────────────────

_FAKE_CSV_CONTENT = textwrap.dedent("""\
    name,count
    华为技术有限公司,100
    小米科技有限责任公司,50
    字节跳动有限公司,25
""")

_FAKE_REGISTRY = {
    "schema_version": 2,
    "generated_on": "2026-07-06",
    "notes": [],
    "groups": [
        {
            "canonical_name_cn": "腾讯控股有限公司",
            "is_central_soe": False,
            "aliases": ["腾讯", "Tencent"],
            "subsidiaries": [
                {"name_cn": "微信支付", "legal_full_name_cn": "财付通支付科技有限公司"}
            ],
            "sources": [],
        },
        {
            "canonical_name_cn": "阿里巴巴集团控股有限公司",
            "is_central_soe": False,
            "aliases": ["阿里巴巴", "Alibaba"],
            "subsidiaries": [
                {"name_cn": "支付宝", "legal_full_name_cn": "支付宝（中国）网络技术有限公司"}
            ],
            "sources": [],
        },
    ],
}


def _write_csv(path: Path, content: str = _FAKE_CSV_CONTENT) -> Path:
    """向 path 写假 CSV 并返回路径。"""
    path.write_text(content, encoding="utf-8")
    return path


def _write_registry(path: Path, data: dict = None) -> Path:
    """向 path 写假 registry JSON 并返回路径。"""
    if data is None:
        data = _FAKE_REGISTRY
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ──────────────────────────────────────────────
# 正常导出
# ──────────────────────────────────────────────


def test_normal_export(tmp_path):
    """正常导出：3 行 CSV + 假 registry（与 CSV 名字不重合）→ 队列文件存在、通过校验、items=3、freq 降序。"""
    csv_path = _write_csv(tmp_path / "misses.csv")
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    queue_file = out_dir / "2026Q3" / "queue_T3.json"
    assert queue_file.exists()

    data = json.loads(queue_file.read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    assert q.task == "T3"
    assert q.quarter == "2026Q3"
    assert len(q.items) == 3

    # freq 降序
    freqs = [item.freq for item in q.items]
    assert freqs == sorted(freqs, reverse=True)

    # guide 已复制
    guide_file = out_dir / "2026Q3" / "guide_T3.md"
    assert guide_file.exists()
    assert guide_file.stat().st_size > 0


def test_normal_export_registry_groups_populated(tmp_path):
    """正常导出：每条 item 的 registry_groups 非空，且与 registry canonical 名匹配。"""
    csv_path = _write_csv(tmp_path / "misses.csv")
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    expected_groups = ["腾讯控股有限公司", "阿里巴巴集团控股有限公司"]
    for item in q.items:
        assert item.registry_groups == expected_groups


# ──────────────────────────────────────────────
# registry 覆盖过滤
# ──────────────────────────────────────────────


def test_registry_filter_canonical_name(tmp_path):
    """registry 覆盖过滤：canonical_name_cn 命中 → 该名字被跳过。"""
    csv_content = "name,count\n华为技术有限公司,100\n腾讯控股有限公司,50\n"
    csv_path = _write_csv(tmp_path / "misses.csv", csv_content)
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    names = [item.raw_name for item in q.items]
    assert "腾讯控股有限公司" not in names
    assert "华为技术有限公司" in names


def test_registry_filter_alias(tmp_path):
    """registry 覆盖过滤：aliases 命中 → 该名字被跳过。"""
    csv_content = "name,count\n华为技术有限公司,100\n腾讯,50\n"
    csv_path = _write_csv(tmp_path / "misses.csv", csv_content)
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    names = [item.raw_name for item in q.items]
    assert "腾讯" not in names
    assert "华为技术有限公司" in names


def test_registry_filter_subsidiary_name_cn(tmp_path):
    """registry 覆盖过滤：subsidiaries[].name_cn 命中 → 该名字被跳过。"""
    csv_content = "name,count\n华为技术有限公司,100\n微信支付,50\n"
    csv_path = _write_csv(tmp_path / "misses.csv", csv_content)
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    names = [item.raw_name for item in q.items]
    assert "微信支付" not in names


def test_registry_filter_subsidiary_legal_full_name_cn(tmp_path):
    """registry 覆盖过滤：subsidiaries[].legal_full_name_cn 命中 → 该名字被跳过。"""
    csv_content = "name,count\n华为技术有限公司,100\n财付通支付科技有限公司,50\n"
    csv_path = _write_csv(tmp_path / "misses.csv", csv_content)
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    names = [item.raw_name for item in q.items]
    assert "财付通支付科技有限公司" not in names


# ──────────────────────────────────────────────
# 台账去重
# ──────────────────────────────────────────────


def test_ledger_dedup(tmp_path):
    """台账去重：预先 record 其中 1 个 name → 重导出 items 数相应减一。"""
    csv_path = _write_csv(tmp_path / "misses.csv")
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    # 预先把 "华为技术有限公司" 记入台账
    ledger = load_ledger(ledger_path)
    key = t3_key("华为技术有限公司")
    record(
        ledger,
        key,
        task="T3",
        quarter="2026Q2",
        item_id=key,
        status="accepted_no_change",
        verdict_digest="abc123",
    )
    save_ledger(ledger_path, ledger)

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    data = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)
    assert len(q.items) == 2
    names = [item.raw_name for item in q.items]
    assert "华为技术有限公司" not in names


# ──────────────────────────────────────────────
# 非法输入
# ──────────────────────────────────────────────


def test_invalid_misses_csv_not_found(tmp_path):
    """非法输入：misses CSV 路径不存在 → 入口抛错。"""
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    with pytest.raises((SystemExit, ValueError, FileNotFoundError)):
        eq.export_t3(
            quarter="2026Q3",
            misses_csv=tmp_path / "nonexistent.csv",
            registry=reg_path,
            out_dir=out_dir,
            ledger_path=ledger_path,
        )


def test_invalid_quarter_q5(tmp_path):
    """非法输入：quarter=2026Q5 → 抛错。"""
    csv_path = _write_csv(tmp_path / "misses.csv")
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    with pytest.raises((SystemExit, ValueError)):
        eq.export_t3(
            quarter="2026Q5",
            misses_csv=csv_path,
            registry=reg_path,
            out_dir=out_dir,
            ledger_path=ledger_path,
        )


# ──────────────────────────────────────────────
# 幂等
# ──────────────────────────────────────────────


def test_idempotent(tmp_path):
    """幂等：连跑两次，两次 queue_T3.json 的 items 完全一致（items 字段不含 generated_at）。"""
    csv_path = _write_csv(tmp_path / "misses.csv")
    reg_path = _write_registry(tmp_path / "registry.json")
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data1 = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=csv_path,
        registry=reg_path,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )
    data2 = json.loads((out_dir / "2026Q3" / "queue_T3.json").read_text(encoding="utf-8"))

    assert data1["items"] == data2["items"]


# ──────────────────────────────────────────────
# 真实冒烟（真实文件，不依赖网络/库）
# ──────────────────────────────────────────────

_WORKTREE = Path(__file__).resolve().parent.parent
_REAL_CSV = _WORKTREE / "supporting_docs" / "entity_reparse_misses_20260706_023835.csv"
_REAL_REGISTRY = _WORKTREE / "supporting_docs" / "company_hierarchy_v2.json"


@pytest.mark.skipif(
    not _REAL_CSV.exists() or not _REAL_REGISTRY.exists(),
    reason="真实文件不在 worktree，跳过冒烟测试",
)
def test_smoke_real_files(tmp_path):
    """真实冒烟：用真实 misses CSV + registry 跑一次，断言成功、items<50、registry_groups 非空。"""
    out_dir = tmp_path / "out"
    ledger_path = tmp_path / "ledger.json"

    eq.export_t3(
        quarter="2026Q3",
        misses_csv=_REAL_CSV,
        registry=_REAL_REGISTRY,
        out_dir=out_dir,
        ledger_path=ledger_path,
    )

    queue_file = out_dir / "2026Q3" / "queue_T3.json"
    assert queue_file.exists()

    data = json.loads(queue_file.read_text(encoding="utf-8"))
    q = T3QueueFile.model_validate(data)

    # 覆盖过滤生效：条数应远少于 50
    assert len(q.items) < 50

    # 每条 registry_groups 非空
    for item in q.items:
        assert len(item.registry_groups) > 0
