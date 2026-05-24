"""run_eval 端到端 mini 测试：用 MOCK 提取器（无真实 Gemini）跑全流程并断言报告数值正确。"""
import json
import sqlite3

import pytest

from eval import gold_loader, run_eval


def _make_raw_news_db(db_path):
    """建临时 raw_news 库并插入 3 篇可区分的原文（url a/b/c）。"""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE raw_news ("
            "id INTEGER PRIMARY KEY, url TEXT, title TEXT, content TEXT, "
            "author TEXT, publish_date TEXT, crawl_time TEXT, "
            "passed_keyword_filter INTEGER, batch_id TEXT)"
        )
        conn.executemany(
            "INSERT INTO raw_news (url, title, content, publish_date) VALUES (?,?,?,?)",
            [
                ("http://a", "光伏中标", "某中资光伏项目正文 A", "2024-01-01"),
                ("http://b", "无关新闻", "与中资可再生能源无关的正文 B", "2024-02-01"),
                ("http://c", "风电签约", "某中资风电项目正文 C", "2024-03-01"),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _write_gold(path):
    """写临时 gold_set.jsonl：a=valid(光伏/50/中国电建)、b=irrelevant、c=valid(风电/100/三峡)。"""
    records = [
        {"source_link": "http://a",
         "labels": {"status": "valid", "category": "光伏", "capacity_mw": "50",
                    "entities": ["中国电建"]}},
        {"source_link": "http://b",
         "labels": {"status": "irrelevant", "entities": []}},
        {"source_link": "http://c",
         "labels": {"status": "valid", "category": "风电", "capacity_mw": "100",
                    "entities": ["三峡"]}},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _mock_extract_fn(title, content, date):
    """MOCK 提取器：按原文区分 a/b/c 返回预设原始 result（无任何 LLM 调用）。"""
    blob = (title or "") + (content or "")
    if "正文 A" in blob:  # 文章 a：与 gold a 完全一致
        return {
            "status": "valid_project",
            "projects": [{
                "common_info": {"Category": "光伏", "Capacity (MW)": "50"},
                "chinese_entities": [{
                    "Implementing Chinese Entity": "中国电建",
                    "Type of involvement of Chinese Entities": "EPC总承包",
                }],
            }],
        }
    if "正文 B" in blob:  # 文章 b：irrelevant，与 gold b 一致
        return {"status": "irrelevant"}
    if "正文 C" in blob:  # 文章 c：category 错（光伏 vs gold 风电），capacity+entity 对
        return {
            "status": "valid_project",
            "projects": [{
                "common_info": {"Category": "光伏", "Capacity (MW)": "100"},
                "chinese_entities": [{"Implementing Chinese Entity": "三峡"}],
            }],
        }
    raise AssertionError(f"mock extract_fn 收到未知文章: {title!r}")


def test_run_eval_e2e_with_mock(tmp_path):
    """端到端：mock 提取 3 篇 → 报告应有 status 全对、a/c 计入字段分、category 半对、capacity/entity 全对。"""
    raw_db = tmp_path / "raw_news.db"
    _make_raw_news_db(raw_db)
    gold_path = tmp_path / "gold_set.jsonl"
    _write_gold(gold_path)
    cache_dir = tmp_path / "cache"
    reports_dir = tmp_path / "reports"

    report = run_eval.run_eval(
        str(gold_path),
        extract_fn=_mock_extract_fn,
        extractor_version="e2e_test",
        raw_news_db=str(raw_db),
        cache_dir=str(cache_dir),
        reports_dir=str(reports_dir),
        use_cache=False,
    )

    # 总量与跳过/失败
    assert report["n_gold"] == 3
    assert report["n_skipped"] == 0
    assert report["n_failed"] == 0

    # status 全对
    assert report["status_accuracy"] == 1.0

    # 仅 a、c（valid 且 status 对）计入字段分；b irrelevant 不计
    assert report["n_scored"] == 2

    # category：a 对、c 错 → 1/2
    assert report["fields"]["category"]["correct"] == 1
    assert report["fields"]["category"]["total"] == 2
    assert abs(report["fields"]["category"]["accuracy"] - 0.5) < 1e-9

    # capacity_mw：a(50)、c(100) 均在 ±5% 内 → 2/2
    assert report["fields"]["capacity_mw"]["correct"] == 2
    assert report["fields"]["capacity_mw"]["total"] == 2

    # 实体集合 a、c 均精确匹配 → F1 均值 1.0
    assert abs(report["entity_f1_mean"] - 1.0) < 1e-9

    # 报告 json 落盘：恰好一个，且重新载入与返回 dict 关键字段一致
    written = list(reports_dir.glob("*.json"))
    assert len(written) == 1
    with open(written[0], "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["n_gold"] == report["n_gold"]
    assert on_disk["n_scored"] == report["n_scored"]
    assert on_disk["status_accuracy"] == report["status_accuracy"]
    assert on_disk["extractor_version"] == "e2e_test"
    assert on_disk["fields"]["category"]["correct"] == report["fields"]["category"]["correct"]
    assert on_disk["entity_f1_mean"] == report["entity_f1_mean"]


def test_gold_loader_rejects_malformed_status(tmp_path):
    """gold_loader.load 在某行 status 非法时带行号抛 ValueError（外部数据入口校验）。"""
    gold_path = tmp_path / "gold_set.jsonl"
    with open(gold_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"source_link": "http://ok",
                            "labels": {"status": "valid"}}, ensure_ascii=False) + "\n")
        # 第 2 行 status 非法（不属于 valid/no_chinese/irrelevant）
        f.write(json.dumps({"source_link": "http://bad",
                            "labels": {"status": "maybe"}}, ensure_ascii=False) + "\n")

    with pytest.raises(ValueError) as exc:
        gold_loader.load(str(gold_path))
    assert "第 2 行" in str(exc.value)
