"""developer_check 确定性守卫：developer 与同事件中方实体重名时按角色置空/保留。

fixture 全部取自 2026-07-04 验证抽样的真实案例
（supporting_docs/developer_sampling_verdicts_20260704.csv）。
"""
from s01b_project_extractor.guardrails.developer_check import clean_developer


def _ent(impl, role, sub=""):
    """按 row 组装处的实体 dict 形状构造夹具。"""
    return {
        "Implementing Chinese Entity": impl,
        "Subsidiary Name": sub,
        "Type of involvement of Chinese Entities": role,
    }


# ── 剔除：纯承包/设备商重名 ────────────────────────────────────────────────────

def test_epc_overlap_dropped_via_subsidiary():
    """THA-TH-0005：developer=EPC 子公司名 → 整值置空（子公司名也参与重名匹配）。"""
    cleaned, dropped = clean_developer(
        "东方国际",
        [_ent("中国东方电气集团有限公司", "EPC总承包", sub="中国东方国际集团有限公司")],
        "火电",
    )
    assert cleaned == ""
    assert len(dropped) == 1 and dropped[0][0] == "东方国际"


def test_equipment_overlap_dropped():
    """JPN-HB-0002：developer=同事件设备供应商 → 置空。"""
    cleaned, dropped = clean_developer(
        "晶科能源", [_ent("晶科能源股份有限公司", "设备供应商")], "综合能源",
    )
    assert cleaned == ""
    assert "设备供应商" in dropped[0][1]


def test_design_service_role_dropped():
    """设计/施工服务方与 EPC 同属承包角色 → 剔除。"""
    cleaned, dropped = clean_developer(
        "中国电力工程顾问集团有限公司",
        [_ent("中国能建国际集团", "设计/施工服务方", sub="中国电力工程顾问集团有限公司")],
        "输变电建设",
    )
    assert cleaned == ""


def test_partial_drop_rejoins_with_semicolon():
    """NGA-PV-0006 变体：多部件只剔承包方，余下部件以 '; ' 拼回。"""
    cleaned, dropped = clean_developer(
        "中国电建;阳光电源",
        [_ent("中国电力建设集团有限公司", "EPC总承包", sub="中国电建集团尼日利亚代表处")],
        "光伏",
    )
    assert cleaned == "阳光电源"
    assert [p for p, _ in dropped] == ["中国电建"]


# ── 保留：投资类角色 / 制造业豁免 / 无重名 ────────────────────────────────────

def test_invest_role_kept():
    """BGD-HB-0005：投融资+EPC（ICO 投资方）是合法业主 → 保留。"""
    cleaned, dropped = clean_developer(
        "中国机械设备工程股份有限公司",
        [_ent("中国机械工业集团有限公司", "投融资+EPC", sub="中国机械设备工程股份有限公司")],
        "综合能源",
    )
    assert cleaned == "中国机械设备工程股份有限公司"
    assert dropped == []


def test_invest_role_trumps_contractor_role():
    """BFA-HB-0001：同一实体同时挂投融资+EPC 与 EPC总承包 → 投资信号优先，保留。"""
    ents = [
        _ent("威海国际经济技术合作股份有限公司", "投融资+EPC"),
        _ent("威海国际经济技术合作股份有限公司", "EPC总承包"),
    ]
    cleaned, dropped = clean_developer("威海国际经济技术合作股份有限公司", ents, "综合能源")
    assert cleaned == "威海国际经济技术合作股份有限公司"
    assert dropped == []


def test_equipment_kept_for_manufacturing_category():
    """ESP-MF-0003：新能源制造业项目，厂主=设备商自己投资建厂 → 豁免不剔。"""
    cleaned, dropped = clean_developer(
        "Hygreen Energy", [_ent("Hygreen Energy", "设备供应商")], "新能源制造业",
    )
    assert cleaned == "Hygreen Energy"
    assert dropped == []


def test_financier_overlap_kept_conservative():
    """纯融资方重名不在剔除集（无抽样证据，保守保留，交给 prompt 负面清单）。"""
    cleaned, dropped = clean_developer(
        "国家开发银行", [_ent("国家开发银行", "融资方")], "水电",
    )
    assert cleaned == "国家开发银行"
    assert dropped == []


def test_no_overlap_returns_verbatim():
    """SAU-ES-0011：外方业主与中方实体不重名 → 原样返回（含原分隔符风格不改写）。"""
    ents = [_ent("南瑞继保", "一体化方案提供商")]
    assert clean_developer("SEC", ents, "储能")[0] == "SEC"
    assert clean_developer("A公司、B公司", ents, "储能")[0] == "A公司、B公司"


def test_short_part_not_matched():
    """部件短于 3 字符不做互含匹配（防琐碎误杀）。"""
    cleaned, _ = clean_developer("东方", [_ent("东方电气集团", "EPC总承包")], "火电")
    assert cleaned == "东方"


def test_empty_inputs_noop():
    assert clean_developer("", [_ent("某公司", "EPC总承包")], "光伏") == ("", [])
    assert clean_developer("某业主", [], "光伏") == ("某业主", [])
