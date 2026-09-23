"""2026-09-06 MF audit regressions: project products, mixed news, accepted boundaries."""
import pytest

from s01b_project_extractor.guardrails.nonenergy_check import check_nonenergy
from s01b_project_extractor.prompts.policy_fragments import MANUFACTURING_SCOPE_RULE
from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt


@pytest.mark.parametrize("name", [
    "印度77万吨每年烧碱工程", "印尼邦唐30万吨纯碱项目", "南山溴素及氢溴酸项目",
    "伊拉克安巴尔磷肥项目", "摩洛哥埃尔加迪达磷酸盐项目", "巴西第三氮肥厂",
    "沙特绿色低碳厚板工厂", "哈萨克斯坦阿拜州铝制品厂",
    "印尼旭阳伟山新能源煤焦堆场", "越南600万条半钢子午线轮胎厂",
    "印尼DKV印刷厂", "蜀汉包装一期", "印尼食品加工产业园", "埃及美的洗碗机厂",
    "土耳其PM6造纸厂", "印尼塑料管道厂", "泰国通信光模块厂", "金像电子PCB二期",
    "泰国富乔玻纤厂", "巴西玲珑轮胎及光伏项目",
])
def test_ordinary_factory_not_protected_by_ancillary_power(name):
    reason = check_nonenergy(name, "企业同时从事新能源业务，另签光伏电站项目。",
                             "新能源制造业", None, "屋顶光伏35MW")
    assert reason and "普通工业制造" in reason


def test_opaque_factory_uses_name_bound_products():
    assert check_nonenergy("泰国泰跃科技项目", "泰国泰跃科技项目生产通信光模块。另建光伏项目。",
                           "新能源制造业", None, "")
    assert check_nonenergy("印尼联塑二期厂房项目", "印尼联塑二期厂房项目建设光伏生产车间。另有塑料管道厂。",
                           "新能源制造业", None, "") is None


@pytest.mark.parametrize("name", [
    "光伏玻璃工厂", "光伏铝制品工厂", "风电塔筒工厂", "磷酸铁锂化工厂",
    "正极材料冶炼项目", "镍矿前驱体工厂", "多晶硅冶金项目", "盛拓锂盐工厂",
    "Mariana盐湖项目", "乌尤尼碳酸锂项目", "阿根廷盐湖提锂项目", "SDLA项目",
    "泰国比亚迪工厂", "长安汽车泰国工厂", "宏发继电器厂", "富士智能厂房",
    "巴西可持续航空燃料工厂", "加拿大生物质黑颗粒厂", "理士极板厂", "天岳新材料基地",
])
def test_accepted_category_three_and_actual_energy_products_remain(name):
    assert check_nonenergy(name, "", "新能源制造业", None, "3万吨/年") is None


def test_separate_power_asset_still_allowed():
    assert check_nonenergy("玲珑轮胎厂35MW光伏电站", "", "光伏", 35, "35MW") is None


@pytest.mark.parametrize("name", [
    "比亚迪轮胎厂", "比亚迪充电站", "宏发家电厂", "长安汽车轮胎厂", "天岳包装厂",
    "新能源汽车充换电站", "比亚迪新能源汽车充电站",
])
def test_brand_or_accepted_product_does_not_bypass_hard_exclusions(name):
    assert check_nonenergy(name, "", "新能源制造业", None, "") is not None


def test_project_bound_actual_product_clarifies_generic_factory():
    assert check_nonenergy("某地玻璃化工厂项目", "某地玻璃化工厂项目生产光伏玻璃。",
                           "新能源制造业", None, "") is None
    assert check_nonenergy("某地PCB厂项目", "某地PCB厂项目客户生产光伏玻璃。",
                           "新能源制造业", None, "") is not None
    assert check_nonenergy("某地玻璃化工厂项目", "某地玻璃化工厂项目客户生产光伏玻璃。",
                           "新能源制造业", None, "") is not None


@pytest.mark.parametrize("builder", [build_s1_prompt, build_s2_prompt])
def test_both_stages_share_scope_and_record_object_policy(builder):
    prompt = builder("", "", "2026-09-06")
    assert MANUFACTURING_SCOPE_RULE in prompt
    assert "用户确认纳入：锂资源与锂盐" in prompt
    assert "不虚构当地制造基地" in prompt
