"""csv_to_gold 转换器测试：correct-only 约定（留空沿用 ai 值、空标记转 None、entities 分号转列表）。"""
import csv

from eval import csv_to_gold, gold_loader


def _write_csv(path, rows):
    """写一个最小 draft.csv（含 ai_* 与修正列）供测试。"""
    cols = ["source_link", "article_id", "ai_status", "status",
            "ai_category", "category", "ai_capacity_mw", "capacity_mw",
            "ai_entities", "entities"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def test_blank_keeps_ai_value(tmp_path):
    csv_path = tmp_path / "draft.csv"
    out = tmp_path / "gold.jsonl"
    # 修正列全空 → 沿用 ai 值
    _write_csv(csv_path, [{
        "source_link": "http://a", "article_id": "1",
        "ai_status": "valid", "ai_category": "光伏",
        "ai_capacity_mw": "50", "ai_entities": "中国电建;三峡",
    }])
    n = csv_to_gold.convert(csv_path, out)
    assert n == 1
    recs = gold_loader.load(out)
    lab = recs[0]["labels"]
    assert lab["status"] == "valid"
    assert lab["category"] == "光伏"
    assert lab["capacity_mw"] == "50"
    assert set(lab["entities"]) == {"中国电建", "三峡"}


def test_correction_overrides_ai(tmp_path):
    csv_path = tmp_path / "draft.csv"
    out = tmp_path / "gold.jsonl"
    # category 修正列填了 → 覆盖；entities 修正列填了 → 覆盖
    _write_csv(csv_path, [{
        "source_link": "http://b",
        "ai_status": "valid", "ai_category": "光伏", "category": "风电",
        "ai_capacity_mw": "50", "capacity_mw": "100",
        "ai_entities": "错误公司", "entities": "三峡",
    }])
    csv_to_gold.convert(csv_path, out)
    lab = gold_loader.load(out)[0]["labels"]
    assert lab["category"] == "风电"
    assert lab["capacity_mw"] == "100"
    assert lab["entities"] == ["三峡"]


def test_empty_marker_becomes_none_or_empty(tmp_path):
    csv_path = tmp_path / "draft.csv"
    out = tmp_path / "gold.jsonl"
    # 真实值确为空：capacity_mw 写空标记 -，entities 写空标记
    _write_csv(csv_path, [{
        "source_link": "http://c",
        "ai_status": "valid", "ai_category": "综合能源",
        "ai_capacity_mw": "50", "capacity_mw": "-",
        "ai_entities": "某公司", "entities": "-",
    }])
    csv_to_gold.convert(csv_path, out)
    lab = gold_loader.load(out)[0]["labels"]
    assert lab["capacity_mw"] is None  # 空标记 → None（gold_loader 回填为 None）
    assert lab["entities"] == []


def test_unannotated_status_row_skipped(tmp_path):
    csv_path = tmp_path / "draft.csv"
    out = tmp_path / "gold.jsonl"
    # 负样本行：ai_status 空且 status 未填 → 跳过；另一行正常
    _write_csv(csv_path, [
        {"source_link": "http://neg", "ai_status": "", "status": ""},
        {"source_link": "http://ok", "ai_status": "irrelevant"},
    ])
    n = csv_to_gold.convert(csv_path, out)
    assert n == 1
    recs = gold_loader.load(out)
    assert recs[0]["source_link"] == "http://ok"
    assert recs[0]["labels"]["status"] == "irrelevant"


def test_negative_row_with_human_status(tmp_path):
    csv_path = tmp_path / "draft.csv"
    out = tmp_path / "gold.jsonl"
    # 负样本行人工填了 status=no_chinese → 保留
    _write_csv(csv_path, [
        {"source_link": "http://neg", "ai_status": "", "status": "no_chinese"},
    ])
    csv_to_gold.convert(csv_path, out)
    lab = gold_loader.load(out)[0]["labels"]
    assert lab["status"] == "no_chinese"
    assert lab["entities"] == []
