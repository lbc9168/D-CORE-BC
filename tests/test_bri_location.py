"""bri_location：音译地名三信号候选 + LLM 批量确认（fake client）+ Union-Find 映射。"""
import json
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import bri_location


class _FakeResp:
    def __init__(self, text):
        self.text = text


class _FakeClient:
    """鸭子类型 genai client：固定返回 payload；也可注入异常。"""
    def __init__(self, payload=None, raise_exc=None):
        self._payload, self._raise = payload, raise_exc
        self.models = self

    def generate_content(self, model, contents):
        if self._raise:
            raise self._raise
        return _FakeResp(json.dumps(self._payload, ensure_ascii=False))


def test_positional_signal_catches_transliteration_variant():
    """达瓦德密 vs 达旺德米：token/partial 均不及阈，等长逐位信号必须命中。"""
    cands = bri_location.find_location_candidates([("达瓦德密", 3), ("达旺德米", 1)])
    assert len(cands) == 1
    assert "positional" in cands[0]["signals"]


def test_suffix_variant_caught_by_partial():
    """信德 vs 信德省：行政后缀差异由 partial 信号命中。"""
    cands = bri_location.find_location_candidates([("信德", 5), ("信德省", 2)])
    assert len(cands) == 1


def test_unrelated_locations_not_candidates():
    cands = bri_location.find_location_candidates([("信德省", 5), ("旁遮普省", 4)])
    assert cands == []


def test_build_union_mapping_selects_canonical():
    names = [("达瓦德密", 3), ("达旺德米", 1)]
    confirmed = [{"name_a": "达瓦德密", "name_b": "达旺德米",
                  "is_same": True, "canonical_name": "达瓦德密"}]
    mapping = bri_location.build_union_mapping(names, confirmed)
    assert mapping == {"达旺德米": "达瓦德密"}


def test_build_location_mapping_per_country():
    """按国家独立归一：fake client 确认唯一候选对；单地名国家直接跳过。"""
    client = _FakeClient(payload=[{"id": 0, "is_same": True, "canonical_name": "达瓦德密"}])
    by_country = {
        "巴基斯坦": [("达瓦德密", 3), ("达旺德米", 1)],
        "肯尼亚": [("加里萨", 2)],
    }
    result = bri_location.build_location_mapping(by_country, client)
    assert result == {"巴基斯坦": {"达旺德米": "达瓦德密"}}


def test_llm_failure_is_conservative(monkeypatch):
    """LLM 三次全挂 → 保守不合并，映射为空（sleep 打桩避免真实退避等待）。"""
    monkeypatch.setattr(bri_location.time, "sleep", lambda s: None)
    client = _FakeClient(raise_exc=RuntimeError("boom"))
    result = bri_location.build_location_mapping(
        {"巴基斯坦": [("达瓦德密", 3), ("达旺德米", 1)]}, client
    )
    assert result == {}


def test_llm_rejects_yields_no_mapping():
    """候选存在但 LLM 判定不同 → 该国无映射（区别于 LLM 宕机路径）。"""
    client = _FakeClient(payload=[{"id": 0, "is_same": False, "canonical_name": ""}])
    result = bri_location.build_location_mapping(
        {"巴基斯坦": [("达瓦德密", 3), ("达旺德米", 1)]}, client
    )
    assert result == {}


def test_build_union_mapping_transitive():
    """A=B 且 B=C → 三者并入同一规范名。"""
    names = [("达瓦德密", 3), ("达旺德米", 1), ("达瓦德蜜", 1)]
    confirmed = [
        {"name_a": "达瓦德密", "name_b": "达旺德米", "is_same": True, "canonical_name": "达瓦德密"},
        {"name_a": "达旺德米", "name_b": "达瓦德蜜", "is_same": True, "canonical_name": "达瓦德密"},
    ]
    mapping = bri_location.build_union_mapping(names, confirmed)
    assert mapping == {"达旺德米": "达瓦德密", "达瓦德蜜": "达瓦德密"}


class _CountingClient:
    """记录 generate_content 调用次数；对每批所有 id 返回 is_same=False。"""
    def __init__(self):
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents):
        self.calls += 1
        # 批内有多少对就返回多少个 false 结论（id 0..N-1，覆盖最大批）
        results = [{"id": i, "is_same": False, "canonical_name": ""} for i in range(bri_location.LLM_BATCH_SIZE)]
        return _FakeResp(json.dumps(results))


def test_build_location_mapping_batches_over_limit():
    """候选对数 > LLM_BATCH_SIZE → 分批多次调用 LLM。"""
    # 构造 > 25 个候选对：N 个两两都成候选的同前缀等长地名 → C(N,2) 对
    # 用 8 个 → C(8,2)=28 > 25，确保跨两批
    names = [(f"达{c}德密", 1) for c in "瓦旺娃蛙洼凹漥窪"]
    cands = bri_location.find_location_candidates(names)
    assert len(cands) > bri_location.LLM_BATCH_SIZE   # 前置确认确实跨批
    client = _CountingClient()
    bri_location.build_location_mapping({"巴基斯坦": names}, client)
    assert client.calls >= 2
