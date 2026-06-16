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
