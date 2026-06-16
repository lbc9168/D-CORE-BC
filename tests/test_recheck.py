"""recheck：第二遍带原文复核——critic recheck prompt / 候选加载 / 编排分流。"""
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from review import critic


class _FakeLLM:
    """鸭子类型 LLMClient：complete() 固定返回注入的 dict；记录最后 prompt。"""
    def __init__(self, payload):
        self._payload = payload
        self.last_prompt = ""
    def complete(self, prompt, *, stage, json_mode=True):
        self.last_prompt = prompt
        return self._payload


_ARTICLE = {"title": "某光伏电站签约", "content": "中企A在巴基斯坦承建100MW光伏电站。"}
_PROJECT = {"project_name": "巴基斯坦100MW光伏", "category": "光伏", "capacity_mw": "",
            "involvement_type": "Unknown", "implementing_entity": "中企A", "source_link": "http://a"}
_PRIOR = [{"field": "capacity_mw", "prior_reason": "low_confidence_field",
           "prior_suggested": None, "prior_conf": 0.4, "prior_evidence": "未找到容量",
           "current_value": ""}]


def test_recheck_prompt_includes_prior_and_force_decision():
    """recheck prompt 必须含第一遍上下文 + 强制决策措辞 + 合法枚举。"""
    p = critic._build_recheck_prompt(article=_ARTICLE, project=_PROJECT, suspicious_with_prior=_PRIOR)
    assert "第二遍" in p or "上一遍" in p
    assert "capacity_mw" in p
    assert "0.4" in p                      # 第一遍置信回喂
    assert "必须" in p                      # 强制决策
    assert "光伏" in p                      # 合法 category 枚举注入


def test_recheck_project_parses_verdicts():
    """recheck_project 用 fake LLM 返回 → 解析为经校验的 Verdict 列表。"""
    llm = _FakeLLM({"verdicts": [
        {"field": "capacity_mw", "verdict": "wrong", "suggested_value": "100",
         "confidence": 0.95, "evidence": "原文写 100MW"}]})
    out = critic.recheck_project(article=_ARTICLE, project=_PROJECT,
                                 suspicious_with_prior=_PRIOR, llm=llm)
    assert len(out) == 1
    assert out[0].field == "capacity_mw" and out[0].verdict == "wrong"
    assert out[0].suggested_value == "100"
    assert llm.last_prompt  # prompt 真的被发出


import sqlite3
from review import queue as _queue


def _seed_projects_db(path):
    """建最小 extracted_projects（id/字段/source_link）。"""
    c = sqlite3.connect(path)
    # capacity_mw 用 REAL（与真实 extracted_projects schema 一致；v2 规则：数值字段 REAL + *_raw 原文列）
    c.execute("""CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, project_name TEXT,
                 category TEXT, capacity_mw REAL, capacity_mw_raw TEXT, involvement_type TEXT,
                 implementing_entity TEXT, source_link TEXT, excluded INTEGER DEFAULT 0,
                 exclude_source TEXT, exclude_reason TEXT)""")
    c.executemany("INSERT INTO extracted_projects (id,project_name,category,capacity_mw,involvement_type,implementing_entity,source_link) VALUES (?,?,?,?,?,?,?)",
                  [(10, "项目甲", "光伏", "", "Unknown", "中企A", "http://a"),
                   (20, "项目乙", "油气", "50", "EPC总承包", "中企B", "http://b")])
    c.commit(); c.close()


def _seed_review_db(path):
    """建 review_queue 并塞高/中/低三档 severity 行（severity 列由 tag 脚本加，这里直接建带 severity 的表）。"""
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE review_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_id TEXT,
                 source_link TEXT, reason_type TEXT NOT NULL, field TEXT, current_value TEXT,
                 suggested_value TEXT, critic_confidence REAL, evidence TEXT,
                 state TEXT NOT NULL DEFAULT 'pending', created_at TEXT, severity TEXT)""")
    c.executemany("INSERT INTO review_queue (canonical_id,source_link,reason_type,field,suggested_value,critic_confidence,evidence,severity) VALUES (?,?,?,?,?,?,?,?)",
                  [("10", "http://a", "low_confidence_field", "capacity_mw", None, 0.4, "未找到容量", "中"),
                   ("20", "http://b", "exclude_suggested", "status", "exclude", 0.7, "疑似油气", "高"),
                   ("10", "http://a", "low_confidence_field", "involvement_type", None, 0.3, "角色不清", "低")])
    c.commit(); c.close()


def test_load_recheck_candidates_filters_and_aggregates(tmp_path):
    """只取高+中、按 canonical_id 聚合、带回第一遍上下文 + 当前库值 + source_link。"""
    from review import recheck
    pdb, rdb = str(tmp_path / "p.db"), str(tmp_path / "r.db")
    _seed_projects_db(pdb); _seed_review_db(rdb)
    cands = recheck.load_recheck_candidates(rdb, pdb)
    by_id = {c["row_id"]: c for c in cands}
    assert set(by_id) == {10, 20}                       # 低 severity 的 involvement 行不单独成候选记录
    # id=10 只保留高/中字段（capacity_mw），低的 involvement 不进
    fields_10 = {s["field"] for s in by_id[10]["suspicious_with_prior"]}
    assert fields_10 == {"capacity_mw"}
    assert by_id[10]["suspicious_with_prior"][0]["prior_conf"] == 0.4
    assert by_id[10]["suspicious_with_prior"][0]["current_value"] == ""   # 来自 extracted_projects
    assert by_id[10]["source_link"] == "http://a"
    # id=20 是 exclude_suggested（field=status，高）
    assert by_id[20]["suspicious_with_prior"][0]["field"] == "status"
    assert by_id[20]["full_project"]["project_name"] == "项目乙"


import csv as _csv


def test_run_recheck_routes_and_writes_residual(tmp_path, monkeypatch):
    """e2e：capacity 高置信有效→自动改；exclude conf=1.0→自动排除；其余→残留 CSV。"""
    from review import recheck
    pdb, rdb = str(tmp_path / "p.db"), str(tmp_path / "r.db")
    _seed_projects_db(pdb); _seed_review_db(rdb)

    # fake article_fn：恒返回原文；fake critic_fn：按 row_id 给不同 verdict
    def fake_article(url, db_path=None):
        return {"title": "t", "content": "c"}

    def fake_critic(*, article, project, suspicious_with_prior, llm):
        from review.critic import Verdict
        if project["project_name"] == "项目甲":      # id=10：capacity 有效高置信 → 自动改
            return [Verdict(field="capacity_mw", verdict="wrong", suggested_value="100",
                            confidence=0.95, evidence="原文100MW")]
        return [Verdict(field="status", verdict="exclude", suggested_value=None,
                        confidence=1.0, evidence="确系油气")]              # id=20：exclude 自动

    residual = str(tmp_path / "residual.csv")
    rep = recheck.run_recheck(pdb, rdb, raw_news_db="unused", llm=object(),
                              critic_fn=fake_critic, article_fn=fake_article,
                              dry_run=False, residual_csv=residual, ts="20260617_000000")
    assert rep["n_candidates"] == 2
    assert rep["n_auto_corrected"] == 1      # capacity_mw=100 自动改
    assert rep["n_auto_excluded"] == 1       # exclude conf=1.0
    # 库已改
    c = sqlite3.connect(pdb)
    assert c.execute("SELECT capacity_mw FROM extracted_projects WHERE id=10").fetchone()[0] == 100.0
    assert c.execute("SELECT excluded FROM extracted_projects WHERE id=20").fetchone()[0] == 1
    c.close()


def test_run_recheck_dry_run_no_write(tmp_path):
    """dry_run：不改库，would_change 记录到 report。"""
    from review import recheck
    from review.critic import Verdict
    pdb, rdb = str(tmp_path / "p.db"), str(tmp_path / "r.db")
    _seed_projects_db(pdb); _seed_review_db(rdb)
    rep = recheck.run_recheck(
        pdb, rdb, raw_news_db="unused", llm=object(),
        critic_fn=lambda **k: [Verdict(field="capacity_mw", verdict="wrong",
                                       suggested_value="100", confidence=0.95, evidence="x")],
        article_fn=lambda url, db_path=None: {"title": "t", "content": "c"},
        dry_run=True, residual_csv=str(tmp_path / "r.csv"), ts="20260617_000001")
    c = sqlite3.connect(pdb)
    assert c.execute("SELECT capacity_mw FROM extracted_projects WHERE id=10").fetchone()[0] in (None, "")
    c.close()
    assert rep["dry_run"] is True and rep.get("would_change")


def test_run_recheck_concurrency_matches_serial(tmp_path):
    """concurrency>1：并发取原文+critic、库写仍串行；分流计数与串行一致。"""
    from review import recheck
    from review.critic import Verdict
    pdb, rdb = str(tmp_path / "p.db"), str(tmp_path / "r.db")
    _seed_projects_db(pdb); _seed_review_db(rdb)

    def fake_article(url, db_path=None):
        return {"title": "t", "content": "c"}

    def fake_critic(*, article, project, suspicious_with_prior, llm):
        if project["project_name"] == "项目甲":
            return [Verdict(field="capacity_mw", verdict="wrong", suggested_value="100",
                            confidence=0.95, evidence="x")]
        return [Verdict(field="status", verdict="exclude", suggested_value=None,
                        confidence=1.0, evidence="x")]

    rep = recheck.run_recheck(pdb, rdb, raw_news_db="unused", llm=object(),
                              critic_fn=fake_critic, article_fn=fake_article,
                              dry_run=False, residual_csv=str(tmp_path / "r.csv"),
                              concurrency=4, ts="20260617_000002")
    assert rep["n_candidates"] == 2
    assert rep["n_auto_corrected"] == 1
    assert rep["n_auto_excluded"] == 1
    c = sqlite3.connect(pdb)
    assert c.execute("SELECT capacity_mw FROM extracted_projects WHERE id=10").fetchone()[0] == 100.0
    assert c.execute("SELECT excluded FROM extracted_projects WHERE id=20").fetchone()[0] == 1
    c.close()


def test_build_llm_from_args_uses_provider_model():
    """_build_llm 按 provider/model 走注入的 client 工厂（不真建连，验证 provider/model 装配正确）。"""
    from review import recheck
    captured = {}
    def fake_factory(cfg):
        captured["provider"], captured["model"] = cfg.provider, cfg.model
        return object()
    llm = recheck._build_llm(provider="deepseek", model="deepseek-v4-pro", _factory=fake_factory)
    assert captured["provider"] == "deepseek" and captured["model"] == "deepseek-v4-pro"
    assert llm is not None
