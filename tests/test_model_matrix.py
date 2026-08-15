"""模型矩阵测试：固定分层样本、临时路由环境与 profile 清单均不调用真实 API。"""

import os
import hashlib
import json
import sqlite3

from eval.article_store import cache_key, cache_put
from eval.archived_gold import _normalize_eval_contract, build_archived_development_set
from eval.gold_loader import load
from eval.run_model_matrix import (
    PROFILES,
    ModelProfile,
    article_locked_diagnostics,
    profile_environment,
    select_stratified_sample,
)


def _records() -> list[dict]:
    """构造含三种文章状态的最小 Gold 样本。"""
    return [
        *({"source_link": f"valid-{i}", "status": "valid", "projects": []} for i in range(8)),
        *({"source_link": f"irrelevant-{i}", "status": "irrelevant", "projects": []} for i in range(2)),
        *({"source_link": f"no-chinese-{i}", "status": "no_chinese", "projects": []} for i in range(2)),
    ]


def test_stratified_sample_is_reproducible_and_covers_negative_statuses():
    """同一 seed 产生同一样本，并为两个负状态各保留至少一篇。"""
    first = select_stratified_sample(_records(), 6, 42)
    second = select_stratified_sample(_records(), 6, 42)
    assert first == second
    assert len(first) == 6
    assert {record["status"] for record in first} == {"valid", "irrelevant", "no_chinese"}


def test_profile_environment_applies_and_restores(monkeypatch):
    """实验模型变量仅在上下文内生效，不污染生产默认进程。"""
    monkeypatch.setenv("S01B_PROVIDER", "gemini")
    monkeypatch.delenv("S01B_STAGE_MODELS", raising=False)
    profile = ModelProfile(
        "mixed-test",
        "openai",
        "gpt-5-nano",
        stage_models="s2:gpt-5.4-nano",
    )
    with profile_environment(profile):
        assert os.environ["S01B_PROVIDER"] == "openai"
        assert os.environ["OPENAI_MODEL"] == "gpt-5-nano"
        assert os.environ["S01B_STAGE_MODELS"] == "s2:gpt-5.4-nano"
    assert os.environ["S01B_PROVIDER"] == "gemini"
    assert "S01B_STAGE_MODELS" not in os.environ


def test_default_matrix_contains_requested_models():
    """默认矩阵覆盖生产基线、Gemini 3.5、两个 Nano 及两种固定混合组合。"""
    assert set(PROFILES) == {
        "baseline-gemini25-deepseek",
        "gemini35-deepseek",
        "gpt5nano-deepseek",
        "gpt54nano-deepseek",
        "gemini35s1-gemini25s2-gpt54nano",
        "mixed-nanos-deepseek",
    }
    assert "s5:openai" in PROFILES["mixed-nanos-deepseek"].stage_providers
    requested = PROFILES["gemini35s1-gemini25s2-gpt54nano"]
    assert requested.default_model == "gemini-2.5-flash-lite"
    assert requested.stage_providers == "s34:openai,s5:openai"
    assert requested.stage_models == (
        "s1:gemini-3.5-flash-lite,s34:gpt-5.4-nano,s5:gpt-5.4-nano"
    )


def test_article_locked_diagnostics_scores_single_project_without_name_gate(tmp_path):
    """同 URL 且各一项目时即使项目名低于旧阈值，也可独立审计字段与实体。"""
    gold = [{
        "source_link": "https://example.test/a",
        "status": "valid",
        "projects": [{
            "project_name": "短名称",
            "category": "光伏",
            "project_stage": "中标",
            "financing_model": "Unknown",
            "capacity_mw": "100",
            "total_investment_usd": None,
            "entities": [{"name": "中国电建", "involvement_type": "EPC总承包"}],
        }],
    }]
    raw = {
        "status": "valid_project",
        "projects": [{
            "common_info": {
                "Project Name": "完全不同但同一文章内唯一的很长项目名称",
                "Category": "光伏",
                "Project Stage": "中标",
                "Financing Model": "Unknown",
                "capacity_mw_parsed": 100,
            },
            "chinese_entities": [{
                "Implementing Chinese Entity": "中国电建",
                "Subsidiary Name": "",
                "Type of involvement of Chinese Entities": "EPC总承包",
            }],
        }],
    }
    version = "v1"
    cache_put(cache_key(gold[0]["source_link"], version), raw, tmp_path)
    diagnostic = article_locked_diagnostics(gold, tmp_path, version)
    assert diagnostic["pair_recall"] == 1.0
    assert diagnostic["fields"]["category"]["accuracy"] == 1.0
    assert diagnostic["entity_name_f1_mean"] == 1.0
    assert diagnostic["policy_stable_fields"]["accuracy"] == 1.0


def test_archived_development_set_uses_confirmed_snapshot_and_excludes_unconfirmed(tmp_path):
    """封存适配器只消费 confirmed 草稿，并从快照生成冻结原文库。"""
    review_db = tmp_path / "review.db"
    connection = sqlite3.connect(review_db)
    connection.executescript(
        """
        CREATE TABLE drafts (
            article_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, payload_json TEXT NOT NULL,
            confirmed INTEGER NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE article_snapshots (
            article_id INTEGER PRIMARY KEY, article_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        """
    )
    confirmed_article = {
        "id": 1, "url": "https://example.test/confirmed", "title": "标题证据",
        "content": "冻结正文", "publish_date": "2026-01-01"
    }
    unconfirmed_article = {
        "id": 2, "url": "https://example.test/unconfirmed", "title": "未确认",
        "content": "不应进入评测", "publish_date": "2026-01-02"
    }
    payload = {"article_status": "no_chinese", "non_project_reason": "无中国企业参与", "projects": []}
    for article, confirmed in ((confirmed_article, 1), (unconfirmed_article, 0)):
        connection.execute(
            "INSERT INTO drafts VALUES (?,?,?,?,?)",
            (article["id"], 3, json.dumps(payload, ensure_ascii=False), confirmed, "2026-08-14T00:00:00Z"),
        )
        connection.execute(
            "INSERT INTO article_snapshots VALUES (?,?,?)",
            (
                article["id"],
                hashlib.sha256(article["content"].encode("utf-8")).hexdigest(),
                json.dumps({"article": article}, ensure_ascii=False),
            ),
        )
    connection.commit()
    connection.close()

    result = build_archived_development_set(review_db, tmp_path / "benchmark")
    records = load(result["gold_path"])
    assert [record["source_link"] for record in records] == [confirmed_article["url"]]
    assert result["article_count"] == 1
    assert result["excluded_articles"] == [{"article_id": 2, "revision": 3, "reason": "unconfirmed"}]
    frozen = sqlite3.connect(result["raw_news_path"]).execute(
        "SELECT title,content FROM raw_news WHERE url=?", (confirmed_article["url"],)
    ).fetchone()
    assert frozen == ("标题证据", "冻结正文")


def test_archived_financing_not_mentioned_maps_to_current_unknown_enum():
    """封存 null 与当前 Unknown 在 evaluator 传输层等义，不制造假性融资错误。"""
    record = {"projects": [{"financing_model": None}, {"financing_model": "BOT"}]}
    assert _normalize_eval_contract(record) == {
        "projects": [{"financing_model": "Unknown"}, {"financing_model": "BOT"}]
    }
