# 初筛测试：结构清洗（去重/噪音/来源无关）、关键词门、LLM 判定合成与容错、增量幂等
import json

from s01a2_multi_crawler.filter import (
    FilterRules,
    GeminiFilterClient,
    LLMSettings,
    LLMVerdict,
    decide,
    run_filter,
    save_verdicts,
    stage_keyword,
    stage_llm,
    stage_structural,
    unfiltered_articles,
)
from s01a2_multi_crawler.state import connect, save_articles, save_baseline

RULES = FilterRules(
    re_keywords=["光伏", "风电", "储能"],
    per_site_noise={"bjx": ["价格"], "imsilkroad": ["新华丝路数据库"]},
)


def _conn(tmp_path):
    return connect(tmp_path / "t.db")


def _add(conn, site, url, title, content=None, date="2026-07-01"):
    save_articles(conn, site, [{"url": url, "status": "fetched", "title": title,
                                "publish_date": date, "content_text": content}])


# ---------- Stage 0 ----------

def test_structural_kills_site_noise_and_ignores_unknown_site(tmp_path):
    """按来源噪音规则只对配置过的来源生效；未知来源（如未来 RSS）不误杀"""
    conn = _conn(tmp_path)
    _add(conn, "bjx", "http://b/1", "硅片价格持稳")
    _add(conn, "wechat_rss", "http://w/1", "某公众号价格评论", "正文" * 30)
    alive, killed = stage_structural(conn, unfiltered_articles(conn), RULES)
    assert [k["url"] for k in killed] == ["http://b/1"]
    assert killed[0]["reason"] == "noise:价格"
    assert [a["url"] for a in alive] == ["http://w/1"]


def test_structural_dedups_identical_content_keeps_first(tmp_path):
    """跨条目相同正文只留最早一条，其余标 duplicate_of"""
    conn = _conn(tmp_path)
    body = "完全相同的正文" * 20
    _add(conn, "a", "http://a/1", "t1", body, date="2026-06-01")
    _add(conn, "b", "http://b/2", "t2", body, date="2026-06-05")
    alive, killed = stage_structural(conn, unfiltered_articles(conn), RULES)
    assert [a["url"] for a in alive] == ["http://a/1"]
    assert killed[0] == {"url": "http://b/2", "verdict": "rejected", "stage": "structural",
                         "reason": "duplicate_of:http://a/1"}


def test_structural_empty_content_not_treated_as_duplicate(tmp_path):
    """无正文条目（bjx 仅标题）不能因同为空而互判重复"""
    conn = _conn(tmp_path)
    save_baseline(conn, "bjx", [{"url": "http://b/1", "title": "风电项目并网"}])
    save_baseline(conn, "bjx", [{"url": "http://b/2", "title": "光伏项目开工"}])
    alive, killed = stage_structural(conn, unfiltered_articles(conn), RULES)
    assert len(alive) == 2 and killed == []


# ---------- Stage 1 ----------

def test_keyword_gate():
    """命中新能源关键词才存活；标题或正文命中均可"""
    arts = [
        {"url": "u1", "title": "沙特光伏项目并网", "content_text": None},
        {"url": "u2", "title": "榴莲奔赴中国", "content_text": "水果贸易"},
        {"url": "u3", "title": "某公司中标海外项目", "content_text": "包含 500MW 风电 机组"},
    ]
    alive, killed = stage_keyword(arts, RULES)
    assert [a["url"] for a in alive] == ["u1", "u3"]
    assert killed[0] == {"url": "u2", "verdict": "rejected", "stage": "keyword", "reason": "no_re_keyword"}


# ---------- Stage 2 ----------

def test_decide_keeps_unknowns_rejects_clear_negatives():
    """判不出一律保留；明确国内/明确无中企/非项目事件/非可再生淘汰"""
    base = dict(id=0, location="unknown", chinese_entity="unknown", is_project_event=True, energy_type="PV")
    assert decide(LLMVerdict(**base)) == "relevant"
    assert decide(LLMVerdict(**{**base, "location": "domestic"})) == "rejected"
    assert decide(LLMVerdict(**{**base, "chinese_entity": "no"})) == "rejected"
    assert decide(LLMVerdict(**{**base, "is_project_event": False})) == "rejected"
    assert decide(LLMVerdict(**{**base, "energy_type": "NOT_RE"})) == "rejected"
    assert decide(LLMVerdict(**{**base, "location": "overseas", "chinese_entity": "yes"})) == "relevant"


class _FakeGenaiClient:
    """伪 Gemini SDK client：按预设文本序列应答"""

    def __init__(self, texts):
        self._texts = list(texts)
        self.models = self

    def generate_content(self, **_kw):
        class R:
            pass
        r = R()
        r.text = self._texts.pop(0)
        return r


def _client(texts):
    return GeminiFilterClient(LLMSettings(max_retries=2, backoff_base=0), _client=_FakeGenaiClient(texts))


def test_stage_llm_parses_and_composes_verdicts():
    """LLM 输出解析为 verdict 行；字段落库映射正确"""
    arts = [{"url": "u1", "title": "沙特光伏并网", "content_text": "x"}]
    resp = json.dumps([{"id": 0, "location": "overseas", "chinese_entity": "yes",
                        "is_project_event": True, "energy_type": "PV", "country": "沙特", "reason": "中企海外光伏"}])
    verdicts, failed = stage_llm(arts, RULES, _client([resp]))
    assert failed == 0
    v = verdicts[0]
    assert (v["url"], v["verdict"], v["stage"], v["country"], v["energy_type"]) == ("u1", "relevant", "llm", "沙特", "PV")


def test_stage_llm_invalid_item_skipped_not_crash():
    """单条输出非法只弃该条（留待下轮），其余正常"""
    arts = [{"url": "u1", "title": "a", "content_text": None}, {"url": "u2", "title": "b", "content_text": None}]
    resp = json.dumps([
        {"id": 0, "location": "火星", "chinese_entity": "yes", "is_project_event": True, "energy_type": "PV"},
        {"id": 1, "location": "domestic", "chinese_entity": "yes", "is_project_event": True, "energy_type": "PV"},
    ])
    verdicts, failed = stage_llm(arts, RULES, _client([resp]))
    assert failed == 1
    assert [v["url"] for v in verdicts] == ["u2"]
    assert verdicts[0]["verdict"] == "rejected"


def test_stage_llm_saves_per_batch():
    """save 回调每批调用一次（崩溃不丢已判进度）"""
    arts = [{"url": f"u{i}", "title": "t", "content_text": None} for i in range(3)]
    rules = FilterRules(re_keywords=["光伏"], llm=LLMSettings(batch_size=2))
    item = {"location": "overseas", "chinese_entity": "yes", "is_project_event": True, "energy_type": "PV"}
    resps = [json.dumps([{**item, "id": 0}, {**item, "id": 1}]), json.dumps([{**item, "id": 0}])]
    saved_calls = []
    verdicts, failed = stage_llm(arts, rules, _client(resps), save=saved_calls.append)
    assert failed == 0 and len(verdicts) == 3
    assert [len(c) for c in saved_calls] == [2, 1]


def test_stage_llm_retry_then_success():
    """首次返回非法 JSON 时重试成功"""
    resp = json.dumps([{"id": 0, "location": "overseas", "chinese_entity": "unknown",
                        "is_project_event": True, "energy_type": "WD"}])
    verdicts, failed = stage_llm([{"url": "u1", "title": "t", "content_text": None}], RULES,
                                 _client(["not json", resp]))
    assert failed == 0 and verdicts[0]["verdict"] == "relevant"


# ---------- 漏斗与增量 ----------

def test_run_filter_incremental_and_idempotent(tmp_path):
    """已筛 URL 不再进入漏斗；dry-run 不写库"""
    conn = _conn(tmp_path)
    _add(conn, "s", "http://s/1", "榴莲快讯", "水果")
    stats = run_filter(conn, RULES, dry_run=True)
    assert stats == {"total": 1, "structural_killed": 0, "keyword_killed": 1, "llm_input": 0,
                     "llm_relevant": 0, "llm_rejected": 0, "llm_failed": 0}
    assert conn.execute("SELECT COUNT(*) FROM article_filter").fetchone()[0] == 0
    stats = run_filter(conn, RULES)  # 真跑：关键词杀掉，无 LLM 输入
    assert conn.execute("SELECT COUNT(*) FROM article_filter").fetchone()[0] == 1
    stats = run_filter(conn, RULES)  # 再跑：无未筛文章
    assert stats["total"] == 0


def test_save_verdicts_idempotent(tmp_path):
    """重复写同 URL 的 verdict 走 REPLACE，不产生重复行"""
    conn = _conn(tmp_path)
    row = {"url": "u", "verdict": "rejected", "stage": "keyword", "reason": "no_re_keyword"}
    save_verdicts(conn, [row])
    save_verdicts(conn, [{**row, "verdict": "relevant", "stage": "llm"}])
    got = conn.execute("SELECT verdict, stage, COUNT(*) FROM article_filter GROUP BY url").fetchone()
    assert got == ("relevant", "llm", 1)
