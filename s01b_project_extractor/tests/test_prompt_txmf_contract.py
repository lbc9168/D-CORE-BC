"""S1–S5 与旧版单体 Prompt 的 TX/MF、标题证据和领域容量口径回归测试。"""
from s01b_project_extractor.knowledge import main_equipment
from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt
from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt
from s01b_project_extractor.prompts.s5_summary import build_s5_prompt
from s01b_project_extractor.tools.project_extractor_tool import _build_prompt


def test_s1_scope_distinguishes_power_storage_tx_and_mf():
    """S1 明确独立储能合法但非发电源，并写清 TX/MF 范围边界。"""
    prompt = build_s1_prompt("标题", "正文", "2026-08-14")
    for phrase in ("电力项目（发电或独立储能）", "不是发电源", "不得另拆为 TX 项目", "传统蓄电池制造"):
        assert phrase in prompt


def test_s2_has_four_atomic_fields_and_domain_capacity_guardrails():
    """S2 输出四个原子证据字段并禁止 TX/MF 共用发电装机。"""
    prompt = build_s2_prompt("标题", "正文", "2026-08-14")
    for field in (
        "TX Voltage Evidence", "TX Line Length Evidence",
        "TX Transmission Capacity Evidence", "MF Production Capacity Evidence",
    ):
        assert field in prompt
    assert "TX/MF 强制为空" in prompt
    assert "未提及不要求提供证据" in prompt
    assert "电池工厂 GWh/年是生产产能，不是 BESS 储能能量" in prompt


def test_s3_s4_s34_and_s5_accept_title_and_use_domain_specs():
    """后续阶段统一允许标题事实，S5 按 Category 选择规格单位。"""
    assert "标题或正文" in build_s3_prompt("t", "c", "p")
    assert "标题或正文事实" in build_s4_prompt("t", "c", "p", "输变电建设", ["变压器"])
    assert "标题或正文事实" in build_s34_prompt("t", "c", "p", "新能源制造业", [])
    summary = build_s5_prompt({"common_info": {"Category": "输变电建设"}})
    assert "TX 写电压 kV、线路 km、输送 MW/MVA" in summary
    assert "禁止把 TX/MF 规格描述成发电装机" in summary


def test_equipment_context_only_adds_storage_for_storage_tags():
    """综合能源基础设备无电池，带储标签后才追加储能设备。"""
    base = main_equipment("综合能源")
    tagged = main_equipment("综合能源", ("风光储",))
    assert "电池" not in base
    assert "电池" in tagged
    assert "PCS" in tagged
    assert main_equipment("新能源制造业") == []


def test_legacy_prompt_carries_same_atomic_contract():
    """未使用的旧单体 Prompt 同步正式字段，避免将来误启旧口径。"""
    prompt = _build_prompt("标题", "正文", "2026-08-14")
    assert "TX Voltage Evidence" in prompt
    assert "MF Production Capacity Evidence" in prompt
    assert "发电写装机，储能写MW/MWh，TX写kV/km/MW或MVA，MF写年产能" in prompt
