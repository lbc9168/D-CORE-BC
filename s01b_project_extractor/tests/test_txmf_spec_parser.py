"""TDD 测试：txmf_spec_parser —— TX/MF 专用指标确定性解析（阶段3 任务3.2）。

对拍用例全部取自真库实测行（见任务简报 R1 对拍表），覆盖：
  - TX：voltage_kv / line_length_km / transmission_capacity_mw 三列独立解析
  - MF：production_capacity_value + production_capacity_unit 成对解析
  - 数据源优先级（capacity_details → project_summary → capacity_mw_raw 裸数字禁用）
  - 负向排除（千伏安≠千伏、兆乏非容量、能量单位不入装机/输送容量列）
  - MF 产能语境守卫、件数类→Unknown、多短语保守规则
"""
from s01b_project_extractor.txmf_spec_parser import parse_tx_event, parse_mf_event


# ══════════════════════════════════════════════════════════════════
# TX 对拍用例（简报 R1 表格，真库实取）
# ══════════════════════════════════════════════════════════════════

def test_tx_sau_0005_total_transmission_and_voltage():
    """SAU-TX-0005-02：details「总输送容量700万千瓦」+ summary「±500千伏」→ tcap=7000, voltage=500。"""
    r = parse_tx_event(
        capacity_details="总输送容量700万千瓦",
        project_summary="国家电网公司所属中国电力技术装备有限公司在沙特签署了中部-西部和中部-南部±500千伏柔性直流换流站项目总承包合同。该项目将在沙特建设4座换流站，总输送容量700万千瓦，计划2026年底完成系统调试。",
        capacity_mw_raw="7000000",
    )
    assert r["transmission_capacity_mw"] == 7000.0
    assert r["voltage_kv"] == 500.0
    assert r["line_length_km"] is None


def test_tx_bra_0004_rated_transmission_voltage_and_length():
    """BRA-TX-0004-01：额定输送容量500万千瓦/±800千伏/1468千米 → tcap=5000, voltage=800, length=1468。"""
    r = parse_tx_event(
        capacity_details="额定输送容量500万千瓦",
        project_summary="中国能建中南院参与设计建设的巴西规模最大输电特许权项目巴西东北部新能源送出±800千伏特高压直流输电项目已于2025年06月30日开工。该项目新建1468千米输电线路及配套换流站等设施，额定输送容量500万千瓦，计划于2029年投入运营。",
        capacity_mw_raw="5000",
    )
    assert r["transmission_capacity_mw"] == 5000.0
    assert r["voltage_kv"] == 800.0
    assert r["line_length_km"] == 1468.0


def test_tx_pak_0014_transmission_capacity_mw_direct_and_voltage():
    """PAK-TX-0014-01：输电容量4000兆瓦/±660千伏 → tcap=4000, voltage=660。"""
    r = parse_tx_event(
        capacity_details="输电容量4000兆瓦",
        project_summary="中南电力设计院有限公司牵头设计了巴基斯坦默蒂亚里—拉合尔±660千伏直流输电工程（默拉直流工程）。该工程由中国电力技术装备有限公司投资建设，是国家电网公司在海外的首个BOOT项目，输电容量4000兆瓦，已于2021年9月1日成功商业运行。",
        capacity_mw_raw="4000",
    )
    assert r["transmission_capacity_mw"] == 4000.0
    assert r["voltage_kv"] == 660.0


def test_tx_tls_0001_nxm_mva_substation():
    """TLS-TX-0001-01：details「3x40MVA变电站」→ tcap=120（3×40，审计先例）。"""
    r = parse_tx_event(
        capacity_details="3x40MVA变电站",
        project_summary="特变电工在东帝汶首都帝力承建科摩罗输变电项目，包括新建一座3x40MVA变电站，并连接现有变电站。该项目旨在缓解首都电力供应压力，满足日益增长的电力需求，已于当地时间1月9日举行开工典礼。",
        capacity_mw_raw="120",
    )
    assert r["transmission_capacity_mw"] == 120.0


def test_tx_bra_0002_qianweian_excludes_voltage():
    """BRA-TX-0002-01：新增变电容量110万千伏安+线路总长度117千米 → tcap=1100, length=117, voltage=NULL（千伏安≠千伏）。"""
    r = parse_tx_event(
        capacity_details="新建4座变电站，新增变电容量110万千伏安；新建7条输电线路，线路总长度117千米",
        project_summary="中国国家电网公司旗下的国网巴电CPFL公司在巴西中标了爱立沁输电特许权项目。该项目将新建4座变电站和7条输电线路，总长117千米，旨在提升南大河州和巴拉那州的输电能力和效率。项目特许经营权为30年。",
        capacity_mw_raw="",
    )
    assert r["transmission_capacity_mw"] == 1100.0
    assert r["line_length_km"] == 117.0
    assert r["voltage_kv"] is None


def test_tx_bra_0001_mvar_excludes_transmission_capacity():
    """BRA-TX-0001-01：新增一组230千伏–3×50兆乏电抗器 → voltage=230, tcap=NULL（兆乏是无功不是容量）。"""
    r = parse_tx_event(
        capacity_details="新增一组230千伏–3×50兆乏电抗器",
        project_summary="中国电建在巴西南马托格罗索州伊诺森西亚市承建了伊诺森西亚变电站电抗器扩建项目，该项目已成功送电投运，提前一个月交付。项目旨在提升当地电力供应的稳定性和输电容量。",
        capacity_mw_raw="",
    )
    assert r["voltage_kv"] == 230.0
    assert r["transmission_capacity_mw"] is None


def test_tx_raw_bare_number_never_used_alone():
    """反例：raw=7000000、details/summary 均无文本 → 三列全 NULL + miss。"""
    r = parse_tx_event(capacity_details="", project_summary="", capacity_mw_raw="7000000")
    assert r["voltage_kv"] is None
    assert r["line_length_km"] is None
    assert r["transmission_capacity_mw"] is None
    assert r["miss"] is True


def test_tx_energy_unit_intercepted():
    """反例：TX 事件 details 含「20GWh储能」→ tcap=NULL（能量单位拦截）。"""
    r = parse_tx_event(capacity_details="20GWh储能", project_summary="", capacity_mw_raw="")
    assert r["transmission_capacity_mw"] is None


def test_tx_no_text_at_all_produces_no_miss_row_flag_but_reports_miss():
    """三列全空但事件有文本（如仅有电抗器兆乏描述被排除）时仍标记 miss=True 供上层判断是否可行动。"""
    r = parse_tx_event(capacity_details="", project_summary="项目进展顺利。", capacity_mw_raw="")
    assert r["miss"] is True


# ── voltage 多值取最大 / 长度语境优先 ──────────────────────────────

def test_tx_voltage_multiple_values_takes_max():
    """多个不同千伏值 → 取最大（线路电压等级）。"""
    r = parse_tx_event(capacity_details="220千伏升压至500千伏", project_summary="", capacity_mw_raw="")
    assert r["voltage_kv"] == 500.0


def test_tx_length_prefers_total_context():
    """多个不同长度值，优先带「总长/全长」语境的。"""
    r = parse_tx_event(
        capacity_details="支线5千米，总长117千米",
        project_summary="",
        capacity_mw_raw="",
    )
    assert r["line_length_km"] == 117.0


def test_tx_length_multiple_without_context_is_null():
    """多个不同长度值且无「总长/全长」语境 → 该字段空。"""
    r = parse_tx_event(capacity_details="一期5千米，二期8千米", project_summary="", capacity_mw_raw="")
    assert r["line_length_km"] is None


def test_tx_length_single_value_fills():
    """唯一长度值直接填。"""
    r = parse_tx_event(capacity_details="线路50千米", project_summary="", capacity_mw_raw="")
    assert r["line_length_km"] == 50.0


def test_tx_kv_direct_transmission_conversion():
    """变电容量 N MVA 直读为 MW（无 NxM 乘积时）。"""
    r = parse_tx_event(capacity_details="新建一座500MVA变电站", project_summary="", capacity_mw_raw="")
    assert r["transmission_capacity_mw"] == 500.0


# ══════════════════════════════════════════════════════════════════
# MF 对拍用例（简报 R1 表格，真库实取）
# ══════════════════════════════════════════════════════════════════

def test_mf_sau_0003_gw_production_context():
    """「6GW高效异质结组件生产基地」→ (6, GW/年)。"""
    r = parse_mf_event(
        capacity_details="6GW高效异质结组件生产基地",
        project_summary="鸿钧新能源与沙特合作伙伴将在沙特阿拉伯共同建设一座6GW的高效异质结组件生产基地。该项目旨在满足中东地区日益增长的清洁能源需求，并助力沙特2030愿景。目前项目处于签约阶段。",
        capacity_mw_raw="6000",
    )
    assert r["production_capacity_value"] == 6.0
    assert r["production_capacity_unit"] == "GW/年"


def test_mf_egy_0002_jiwa_nianchanneng():
    """「年产能5吉瓦」→ (5, GW/年)。"""
    r = parse_mf_event(
        capacity_details="年产能5吉瓦",
        project_summary="中国协鑫集团与埃及凯米特公司合作，在埃及建设一座年产能5吉瓦的大型太阳能电池及光伏组件产业园区，总投资额达5亿美元。该项目旨在推动埃及可再生能源制造本土化，并已签署技术转让协议。",
        capacity_mw_raw="5000",
    )
    assert r["production_capacity_value"] == 5.0
    assert r["production_capacity_unit"] == "GW/年"


def test_mf_svk_0001_gwh_planned_capacity():
    """「20GWh (一期规划产能)」→ (20, GWh/年)。"""
    r = parse_mf_event(
        capacity_details="20GWh (一期规划产能)",
        project_summary="国轩高科在斯洛伐克苏拉尼市建设的电池超级工厂项目已正式启幕。该项目占地65公顷，一期规划产能为20GWh，计划于2026年试生产，2027年正式生产，产品主要出口欧盟市场。",
        capacity_mw_raw="",
    )
    assert r["production_capacity_value"] == 20.0
    assert r["production_capacity_unit"] == "GWh/年"


def test_mf_usa_0001_wanton_electrolyte():
    """「年产20万吨电解液」→ (20, 万吨/年)。"""
    r = parse_mf_event(
        capacity_details="年产20万吨电解液",
        project_summary="天赐材料在美国德州贝敦投资建设北美首座电解液工厂，总投资约2亿美元，旨在形成年产20万吨电解液产能，该项目已于2025年11月21日正式破土动工，是公司全球化战略的重要节点。",
        capacity_mw_raw="",
    )
    assert r["production_capacity_value"] == 20.0
    assert r["production_capacity_unit"] == "万吨/年"


def test_mf_egy_0001_multi_value_battery_and_module():
    """「高效光伏电池2GW、光伏组件3GW」→ 全空 + miss(multi_value)（不猜不加和）。"""
    r = parse_mf_event(
        capacity_details="高效光伏电池2GW、光伏组件3GW",
        project_summary="博达新能（埃及）在埃及泰达合作区建设并投产了一个光伏制造基地，设计总产能达5GW，主要面向中东、非洲、欧洲及北美市场。该基地是博达新能ELITE Solar在海外布局的重要制造枢纽。",
        capacity_mw_raw="5000",
    )
    assert r["production_capacity_value"] is None
    assert r["production_capacity_unit"] is None
    assert r["miss"] is True
    assert r["miss_reason"] == "multi_value"


def test_mf_ind_0001_jianshu_leaves_unit_unknown_not_miss():
    """「年产1500支叶片」→ (NULL, Unknown)，非 miss（summary 中「4吉瓦风电项目」是下游项目容量，
    details 已给出确定性件数信号，按数据源优先级不下探 summary）。"""
    r = parse_mf_event(
        capacity_details="年产1500支叶片",
        project_summary="远景能源在印度古吉拉特邦开工建设风电叶片工厂，该工厂年产1500支叶片，将为当地4吉瓦风电项目提供配套。该项目是远景能源在印度的第三家制造工厂，旨在深化其在印度的业务。",
        capacity_mw_raw="1500",
    )
    assert r["production_capacity_value"] is None
    assert r["production_capacity_unit"] == "Unknown"
    assert r["miss"] is False


def test_mf_idn_0010_powerplant_only_context_is_miss():
    """「配套发电厂2000MW」（无其他产能信号）→ 全空 + miss（发电厂语境不算产能）。"""
    r = parse_mf_event(
        capacity_details="配套发电厂2000MW",
        project_summary="中国红狮控股集团计划在印尼投资50亿美元，分阶段建设工业园区，生产硅、多晶硅、电池及组件，并配套建设一座2GW的发电厂。该项目处于投资规划阶段。",
        capacity_mw_raw="2000",
    )
    assert r["production_capacity_value"] is None
    assert r["production_capacity_unit"] is None
    assert r["miss"] is True
    assert r["miss_reason"] != "multi_value"


# ── MF 单位换算补充断言（用户拍板换算表）───────────────────────────

def test_mf_mwh_converts_to_gwh_per_year():
    """N MWh / N兆瓦时 → (N/1000, GWh/年)。"""
    r = parse_mf_event(capacity_details="2000MWh电池产能", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 2.0
    assert r["production_capacity_unit"] == "GWh/年"


def test_mf_zhaowashi_converts_to_gwh_per_year():
    """中文兆瓦时同理换算。"""
    r = parse_mf_event(capacity_details="年产能2000兆瓦时电池", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 2.0
    assert r["production_capacity_unit"] == "GWh/年"


def test_mf_ton_per_year_converts_to_wanton():
    """N吨/年 → (N/10000, 万吨/年)。"""
    r = parse_mf_event(capacity_details="正极材料产能50000吨/年", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 5.0
    assert r["production_capacity_unit"] == "万吨/年"


def test_mf_tons_per_year_english_converts():
    """英文 tons/year 同理换算。"""
    r = parse_mf_event(capacity_details="capacity of 20000 tons/year", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 2.0
    assert r["production_capacity_unit"] == "万吨/年"


def test_mf_wanqianwa_production_context_converts_to_gw():
    """N万千瓦（产能语境）→ (N/100, GW/年)。"""
    r = parse_mf_event(capacity_details="年产能100万千瓦组件产能", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 1.0
    assert r["production_capacity_unit"] == "GW/年"


def test_mf_mw_production_context_converts_to_gw():
    """N MW（产能语境，如「年产能2000MW电池」）→ (N/1000, GW/年)。"""
    r = parse_mf_event(capacity_details="年产能2000MW电池", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_value"] == 2.0
    assert r["production_capacity_unit"] == "GW/年"


def test_mf_gw_negative_lookahead_does_not_eat_gwh():
    """正则注意 GW 勿吞 GWh：「20GWh」不应被 GW 规则误读为 20（应走 GWh 规则=20, GWh/年）。"""
    r = parse_mf_event(capacity_details="20GWh电池产能", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_unit"] == "GWh/年"
    assert r["production_capacity_value"] == 20.0


def test_mf_jiwa_negative_lookahead_does_not_eat_jiwashi():
    """吉瓦 勿吞 吉瓦时：「20吉瓦时」应走吉瓦时规则。"""
    r = parse_mf_event(capacity_details="年产能20吉瓦时电池", project_summary="", capacity_mw_raw="")
    assert r["production_capacity_unit"] == "GWh/年"
    assert r["production_capacity_value"] == 20.0


def test_mf_identical_repeated_phrase_counts_as_one():
    """完全相同的重复短语算一个，不触发 multi_value。"""
    r = parse_mf_event(
        capacity_details="年产能5GW组件，产能5GW",
        project_summary="",
        capacity_mw_raw="",
    )
    assert r["production_capacity_value"] == 5.0
    assert r["production_capacity_unit"] == "GW/年"
    assert r["miss"] is False


def test_mf_no_signal_at_all_is_miss():
    """details/summary 均无任何产能信号 → 全空 + miss(no_signal)。"""
    r = parse_mf_event(capacity_details="", project_summary="项目已开工建设。", capacity_mw_raw="")
    assert r["production_capacity_value"] is None
    assert r["production_capacity_unit"] is None
    assert r["miss"] is True
    assert r["miss_reason"] == "no_signal"


def test_mf_empty_text_no_miss_flag_for_csv_but_reports_miss_true():
    """双列全空且事件无任何文本时 miss=True 但 miss_reason 标注 no_signal（CLI 层决定是否入 CSV）。"""
    r = parse_mf_event(capacity_details="", project_summary="", capacity_mw_raw="")
    assert r["miss"] is True
    assert r["miss_reason"] == "no_signal"
