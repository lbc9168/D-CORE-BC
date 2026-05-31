"""
Layer 1 格式校验测试 — format_checks.py

测试策略：
  每个测试只修改被测字段，其他字段保持合法默认值（来自 conftest.make_record）。
  检验 run_format_checks 在正常 / 边界 / 异常输入下的行为。
"""

import pytest

from s01b_project_extractor.guardrails.format_checks import (
    VALID_RECORD_TYPES,
    run_format_checks,
    valid_categories_set,
    valid_financing_set,
    valid_involvement_set,
    valid_stages_set,
)

# 从 bri_enums 动态获取合法枚举集（避免测试自身硬编码同一份数据）。
VALID_CATEGORIES = valid_categories_set()
VALID_PROJECT_STAGES = valid_stages_set()
VALID_INVOLVEMENT_TYPES = valid_involvement_set()
VALID_FINANCING_MODELS = valid_financing_set()
from conftest import make_record, make_records
import bri_enums


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def errors_for(record: dict) -> list[str]:
    return run_format_checks([record])


def has_error(record: dict, keyword: str) -> bool:
    return any(keyword in e for e in errors_for(record))


# ─────────────────────────────────────────────────────────────────────────────
# 1. 合法记录应零错误
# ─────────────────────────────────────────────────────────────────────────────

class TestValidRecord:
    def test_fully_valid_record_passes(self):
        assert errors_for(make_record()) == []

    def test_batch_of_valid_records_passes(self):
        assert run_format_checks(make_records(20)) == []

    def test_empty_batch_passes(self):
        assert run_format_checks([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. 必填字段
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiredFields:
    @pytest.mark.parametrize("field", [
        "project_name", "country", "category", "implementing_entity"
    ])
    def test_missing_required_field_raises_error(self, field):
        rec = make_record({field: None})
        assert has_error(rec, f"缺少必填字段: {field}")

    @pytest.mark.parametrize("empty_val", ["", "  ", "nan", "NA", "None", "null"])
    def test_empty_like_values_treated_as_missing(self, empty_val):
        rec = make_record({"project_name": empty_val})
        assert has_error(rec, "缺少必填字段: project_name")


# ─────────────────────────────────────────────────────────────────────────────
# 3. 项目名称必须含中文
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectName:
    def test_chinese_name_passes(self):
        rec = make_record({"project_name": "巴基斯坦光伏项目"})
        assert not has_error(rec, "名称未使用中文")

    def test_pure_english_name_fails(self):
        rec = make_record({"project_name": "Pakistan Solar Project"})
        assert has_error(rec, "名称未使用中文")

    def test_mixed_name_with_chinese_passes(self):
        rec = make_record({"project_name": "Pakistan 光伏 Project"})
        assert not has_error(rec, "名称未使用中文")


# ─────────────────────────────────────────────────────────────────────────────
# 4. 国家 ISO 编码
# ─────────────────────────────────────────────────────────────────────────────

class TestCountryISO:
    @pytest.mark.parametrize("iso", ["PAK", "CHN", "ETH", "ZZZ"])
    def test_valid_three_letter_iso_passes(self, iso):
        rec = make_record({"country_iso": iso})
        assert not has_error(rec, "国家ISO无效")

    def test_none_iso_triggers_error(self):
        """str(None)="None"，不匹配 [A-Z]{3}，应报错；建议 DB 写入时统一存 '' 或 'NA'"""
        rec = make_record({"country_iso": None})
        assert has_error(rec, "国家ISO无效")

    def test_empty_string_iso_passes(self):
        """空字符串被 'if iso' 过滤，不报错"""
        rec = make_record({"country_iso": ""})
        assert not has_error(rec, "国家ISO无效")

    def test_na_iso_passes(self):
        rec = make_record({"country_iso": "NA"})
        assert not has_error(rec, "国家ISO无效")

    @pytest.mark.parametrize("bad_iso", ["PK", "PAKK", "pak", "12A", "P K"])
    def test_invalid_iso_fails(self, bad_iso):
        rec = make_record({"country_iso": bad_iso})
        assert has_error(rec, "国家ISO无效")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Category 枚举（重点：Unknown 必须通过）
# ─────────────────────────────────────────────────────────────────────────────

class TestCategory:
    @pytest.mark.parametrize("cat", sorted(VALID_CATEGORIES))
    def test_all_valid_categories_pass(self, cat):
        rec = make_record({"category": cat})
        assert not has_error(rec, "分类不合法"), f"'{cat}' 应该合法但报错"

    def test_unknown_category_passes(self):
        """Fix 3 的核心验证：Unknown 必须通过"""
        rec = make_record({"category": "Unknown"})
        assert not has_error(rec, "分类不合法")

    @pytest.mark.parametrize("bad_cat", ["油气", "太阳能", "新能源", "抽水蓄能", "unknown"])
    def test_invalid_categories_fail(self, bad_cat):
        rec = make_record({"category": bad_cat})
        assert has_error(rec, "分类不合法")

    def test_none_category_triggers_both_errors(self):
        """
        str(None)="None"：
        - _is_empty(None) → 必填字段缺失
        - "None" not in VALID_CATEGORIES → 分类不合法
        两个错误同时出现（代码当前行为）。
        """
        rec = make_record({"category": None})
        errs = errors_for(rec)
        assert any("缺少必填字段: category" in e for e in errs)
        assert any("分类不合法" in e for e in errs)


# ─────────────────────────────────────────────────────────────────────────────
# 6. 装机容量
# ─────────────────────────────────────────────────────────────────────────────

class TestCapacity:
    @pytest.mark.parametrize("cap", ["100", "0.5", "49999", "1000.5"])
    def test_valid_capacity_passes(self, cap):
        rec = make_record({"capacity_mw": cap})
        assert not has_error(rec, "容量")

    def test_none_capacity_passes(self):
        rec = make_record({"capacity_mw": None})
        assert not has_error(rec, "容量")

    @pytest.mark.parametrize("bad_cap", ["-1", "0", "50000", "99999"])
    def test_out_of_range_capacity_fails(self, bad_cap):
        rec = make_record({"capacity_mw": bad_cap})
        assert has_error(rec, "容量异常")

    @pytest.mark.parametrize("bad_cap", ["abc", "100MW", "N/A"])
    def test_non_numeric_capacity_fails(self, bad_cap):
        rec = make_record({"capacity_mw": bad_cap})
        assert has_error(rec, "容量无法解析")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Project Stage 枚举
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectStage:
    @pytest.mark.parametrize("stage", sorted(VALID_PROJECT_STAGES))
    def test_all_valid_stages_pass(self, stage):
        rec = make_record({"project_stage": stage})
        assert not has_error(rec, "阶段不合法")

    @pytest.mark.parametrize("bad", ["规划中", "融资关闭", "建设中", "完工"])
    def test_invalid_stages_fail(self, bad):
        rec = make_record({"project_stage": bad})
        assert has_error(rec, "阶段不合法")

    def test_none_stage_triggers_error(self):
        """str(None)="None"，不在枚举集合中，应报错；DB 写入时应存 'Unknown' 或 ''"""
        rec = make_record({"project_stage": None})
        assert has_error(rec, "阶段不合法")

    def test_empty_string_stage_passes(self):
        """空字符串被 'if stage' 过滤，不报错"""
        rec = make_record({"project_stage": ""})
        assert not has_error(rec, "阶段不合法")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Involvement Type 枚举
# ─────────────────────────────────────────────────────────────────────────────

class TestInvolvementType:
    @pytest.mark.parametrize("inv", sorted(VALID_INVOLVEMENT_TYPES))
    def test_all_valid_types_pass(self, inv):
        rec = make_record({"involvement_type": inv})
        assert not has_error(rec, "参与方式不合法")

    @pytest.mark.parametrize("bad", ["业主", "合作方", "施工方", "投资方"])
    def test_invalid_types_fail(self, bad):
        rec = make_record({"involvement_type": bad})
        assert has_error(rec, "参与方式不合法")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Financing Model 枚举
# ─────────────────────────────────────────────────────────────────────────────

class TestFinancingModel:
    @pytest.mark.parametrize("fin", sorted(VALID_FINANCING_MODELS))
    def test_all_valid_models_pass(self, fin):
        rec = make_record({"financing_model": fin})
        assert not has_error(rec, "融资模式不合法")

    def test_none_financing_model_passes(self):
        rec = make_record({"financing_model": None})
        assert not has_error(rec, "融资模式不合法")

    @pytest.mark.parametrize("bad", ["银行贷款", "自有资金", "PPP"])
    def test_invalid_model_fails(self, bad):
        rec = make_record({"financing_model": bad})
        assert has_error(rec, "融资模式不合法")


# ─────────────────────────────────────────────────────────────────────────────
# 10. 日期格式 YYYY-MM-DD
# ─────────────────────────────────────────────────────────────────────────────

class TestDateFormat:
    @pytest.mark.parametrize("field", ["planned_start_date", "planned_complete_date"])
    def test_valid_date_passes(self, field):
        rec = make_record({field: "2026-03-15"})
        assert not has_error(rec, "日期格式错误")

    @pytest.mark.parametrize("field", ["planned_start_date", "planned_complete_date"])
    def test_none_date_passes(self, field):
        rec = make_record({field: None})
        assert not has_error(rec, "日期格式错误")

    @pytest.mark.parametrize("bad_date", ["2026/03/15", "15-03-2026", "2026-3-5", "20260315"])
    def test_invalid_date_format_fails(self, bad_date):
        rec = make_record({"planned_start_date": bad_date})
        assert has_error(rec, "日期格式错误")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Record Type 枚举
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordType:
    @pytest.mark.parametrize("rtype", sorted(VALID_RECORD_TYPES))
    def test_all_valid_types_pass(self, rtype):
        rec = make_record({"record_type": rtype})
        assert not has_error(rec, "记录类型不合法")

    @pytest.mark.parametrize("bad", ["项目", "合同", "订单"])
    def test_invalid_type_fails(self, bad):
        rec = make_record({"record_type": bad})
        assert has_error(rec, "记录类型不合法")


# ─────────────────────────────────────────────────────────────────────────────
# 12. 多条记录的错误 ID 归属
# ─────────────────────────────────────────────────────────────────────────────

class TestMultipleRecords:
    def test_error_contains_correct_record_id(self):
        records = [
            make_record({"id": 100, "category": "油气"}),
            make_record({"id": 200}),  # 合法
        ]
        errs = run_format_checks(records)
        assert any("#100" in e for e in errs), "错误应包含 #100"
        assert not any("#200" in e for e in errs), "#200 不应有错误"

    def test_multiple_errors_on_same_record(self):
        rec = make_record({
            "id": 42,
            "category": "油气",
            "project_stage": "规划中",
            "involvement_type": "业主",
        })
        errs = errors_for(rec)
        assert len(errs) >= 3, "应有至少3个错误"
        assert all("#42" in e for e in errs)


# ─────────────────────────────────────────────────────────────────────────────
# 13. chinese_unnamed 占位记录守护（E.2 回归）
# ─────────────────────────────────────────────────────────────────────────────

class TestChineseUnnamedPassesFormatChecks:
    """
    守护：chinese_unnamed 状态产出的占位实体记录（implementing_entity=未具名中企，
    involvement_type=Unknown，category=光伏）必须零错误通过 run_format_checks。

    Part B 确认结论：format_checks 对 status 字段无感知，
    "Unknown" involvement 也在合法枚举集内，故此测试预期立即通过，无需改动 format_checks。
    """

    def test_unnamed_placeholder_record_passes_format_checks(self):
        """占位记录应零错误通过 format_checks（不产生误报）。"""
        rec = make_record({
            "implementing_entity": bri_enums.UNNAMED_ENTITY_PLACEHOLDER,
            "involvement_type": "Unknown",
            "category": "光伏",
        })
        assert errors_for(rec) == [], (
            "chinese_unnamed 占位记录不应被 format_checks 拦截"
        )
