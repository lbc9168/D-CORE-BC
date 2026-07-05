"""
TDD 测试：capacity 后处理在 s2_basic.run_s2() 中的接线正确性（Important #3），
以及写库层 capacity_details 正确合并 parser details（Important #1）。

Important #3：run_s2 输出的每个项目 common_info 应同时含：
  - 'capacity_mw_parsed': float|None（解析结果）
  - 'capacity_mw_raw_phrase': str（LLM 逐字原文短语，未解析前的原值）
  - 'Capacity (MW)' 保持原文短语不变（向下兼容，由写库层决定如何存）

Important #1：写库层 capacity_details 应包含 parser 返回的 details_note（产能/输送类说明）。
"""
import sqlite3
import pandas as pd
import pytest
from s01b_project_extractor.stages.s2_basic import run_s2
from s01b_project_extractor.tools import project_extractor_tool as pet


# ─────────────────────────────────────────────────────────────────
# Important #3：stage 层后处理
# ─────────────────────────────────────────────────────────────────

class _StubLLM:
    """按预设返回固定 S2 输出的桩 LLM。"""
    def __init__(self, payload):
        self._payload = payload

    def complete(self, prompt, *, stage, json_mode=True):
        return self._payload


def test_run_s2_parses_capacity_into_float():
    """run_s2 后：capacity_mw_parsed 应为解析后的 MW float（200MW→200.0）。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "肯尼亚200MW光伏项目",
            "Category": "光伏",
            "Capacity (MW)": "200MW",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") == 200.0


def test_run_s2_preserves_raw_phrase():
    """run_s2 后：capacity_mw_raw_phrase 保留 LLM 逐字原文（供 capacity_mw_raw 落库）。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "肯尼亚200MW光伏项目",
            "Category": "光伏",
            "Capacity (MW)": "200MW",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_raw_phrase") == "200MW"


def test_run_s2_wan_qianwa_converts():
    """600万千瓦 → capacity_mw_parsed=6000.0。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "巴基斯坦600万千瓦风电项目",
            "Category": "风电",
            "Capacity (MW)": "600万千瓦",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") == 6000.0


def test_run_s2_production_type_returns_none_with_details():
    """GWh（产能单位）→ capacity_mw_parsed=None，capacity_details_note 非空。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "某储能项目",
            "Category": "储能",
            "Capacity (MW)": "500GWh",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") is None
    assert projs[0]["common_info"].get("capacity_details_note")  # 非空说明


def test_run_s2_tx_category_forces_null():
    """category='输变电建设' → capacity_mw_parsed=None（TX/MF 规则在 stage 层生效）。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "巴基斯坦500kV输变电工程",
            "Category": "输变电建设",
            "Capacity (MW)": "500kV",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") is None


def test_run_s2_empty_capacity_returns_none():
    """Capacity (MW) 为空字符串 → capacity_mw_parsed=None（无错误抛出）。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "某项目",
            "Category": "光伏",
            "Capacity (MW)": "",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") is None


def test_run_s2_missing_capacity_returns_none():
    """common_info 无 Capacity (MW) 键 → capacity_mw_parsed=None（不抛 KeyError）。"""
    llm = _StubLLM({"projects": [{
        "common_info": {
            "Project Name": "某项目",
            "Category": "光伏",
        }
    }]})
    projs = run_s2("t", "c", "2025-01-01", llm=llm)
    assert projs[0]["common_info"].get("capacity_mw_parsed") is None


# ─────────────────────────────────────────────────────────────────
# Important #1：写库层 capacity_details 合并 parser details
# ─────────────────────────────────────────────────────────────────

@pytest.fixture
def _patch_side_effects(monkeypatch):
    """屏蔽外部副作用：央企查表/翻译。"""
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *a, **k: "EN")


def _make_fake_extract(capacity_raw: str, category: str):
    """构造固定输出的 fake extract_one，LLM 已给 capacity 为逐字短语。"""
    def fake_extract_one(title, content, date, *, client, model="x"):
        return {
            "analysis": {"project_subject": "s", "reasoning": "r"},
            "status": "valid_project",
            "projects": [{
                "project_summary": "",
                "in_scope": True,
                "out_of_scope_reason": "",
                "common_info": {
                    "Project Name": "测试项目",
                    "Category": category,
                    "Capacity (MW)": capacity_raw,
                    "Country": "肯尼亚",
                    "Project Stage": "在建",
                    "capacity_mw_parsed": None,
                    "capacity_mw_raw_phrase": capacity_raw,
                    "capacity_details_note": f"原文：{capacity_raw}（产能单位 GWh，非装机容量）",
                },
                "chinese_entities": [{
                    "Implementing Chinese Entity": "中国电建",
                    "Type of involvement of Chinese Entities": "EPC总承包",
                }],
            }],
        }
    return fake_extract_one


def test_capacity_details_includes_parser_note(tmp_path, monkeypatch, _patch_side_effects):
    """写库层：capacity_details 应含解析器返回的 details_note（如产能类说明）。"""
    pdb = str(tmp_path / "projects.db")
    rdb = str(tmp_path / "review.db")

    monkeypatch.setattr(pet, "extract_one",
                        _make_fake_extract("500GWh", "储能"))

    tool = pet.ProjectExtractorTool()
    tool._process_df(
        pd.DataFrame([{
            "原文链接": "http://x", "文章标题": "标题",
            "正文内容": "500GWh储能电站发电项目MW测试",  # 含预筛关键词使文章不被剔除
            "发布日期": "2026-01-01",
        }]),
        batch_id="b1", ts="20260601_t", client=None,
        country_db={}, projects_db_path=pdb, review_db_path=rdb,
    )

    conn = sqlite3.connect(pdb)
    row = conn.execute("SELECT capacity_details FROM extracted_projects").fetchone()
    conn.close()
    assert row is not None
    assert row[0] and "GWh" in row[0], f"capacity_details 应含 parser note，实际: {row[0]!r}"
