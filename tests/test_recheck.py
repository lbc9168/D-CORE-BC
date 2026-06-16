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
    c.execute("""CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, project_name TEXT,
                 category TEXT, capacity_mw TEXT, capacity_mw_raw TEXT, involvement_type TEXT,
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
