"""
Layer 3 批次统计校验测试 — batch_checks.py

测试策略：
  验证 run_batch_checks 在各种批次统计场景下的 hard_errors / warnings 输出。
  不涉及 AI 调用，纯 Python 逻辑。
"""

import pytest

from s01b_project_extractor.guardrails.batch_checks import run_batch_checks
from conftest import make_record, make_records


# ─────────────────────────────────────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────────────────────────────────────

def check(records, total_input=0):
    return run_batch_checks(records, total_input_articles=total_input)


# ─────────────────────────────────────────────────────────────────────────────
# 1. 正常情况 — 无错误无告警
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalBatch:
    def test_healthy_batch_no_errors(self):
        records = make_records(10)
        hard, warnings = check(records, total_input=50)
        assert hard == []
        assert warnings == []

    def test_empty_records_with_zero_input_passes(self):
        hard, warnings = check([], total_input=0)
        assert hard == []

    def test_small_batch_threshold_not_triggered(self):
        """total_input ≤ 5 时，即使 n_valid=0 也不触发 hard_error"""
        hard, warnings = check([], total_input=5)
        assert hard == []


# ─────────────────────────────────────────────────────────────────────────────
# 2. Hard Error：输入 > 5 条但有效项目为 0
# ─────────────────────────────────────────────────────────────────────────────

class TestZeroValidProjects:
    def test_zero_valid_with_many_inputs_is_hard_error(self):
        hard, _ = check([], total_input=6)
        assert len(hard) == 1
        assert "有效项目为 0" in hard[0]

    def test_zero_valid_with_exactly_five_inputs_no_error(self):
        """边界值：total_input == 5，不触发"""
        hard, _ = check([], total_input=5)
        assert hard == []

    def test_zero_valid_with_six_inputs_triggers(self):
        """边界值：total_input == 6，触发"""
        hard, _ = check([], total_input=6)
        assert hard != []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Warning：有效率过低（< 5%）
# ─────────────────────────────────────────────────────────────────────────────

class TestLowValidRatio:
    def test_low_ratio_triggers_warning(self):
        """1/21 ≈ 4.8% < 5%，触发告警"""
        records = make_records(1)
        _, warnings = check(records, total_input=21)
        assert any("有效项目占比过低" in w for w in warnings)

    def test_exactly_five_percent_no_warning(self):
        """5% 刚好不触发（ratio < 0.05 为条件）"""
        records = make_records(1)
        _, warnings = check(records, total_input=20)  # 1/20 = 5%，不满足 < 0.05
        assert not any("占比过低" in w for w in warnings)

    def test_threshold_not_triggered_with_small_input(self):
        """total_input ≤ 20 时不检查比例"""
        records = make_records(1)
        _, warnings = check(records, total_input=20)
        assert not any("占比过低" in w for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Warning：单一 Category 占比过高（> 80%）
# ─────────────────────────────────────────────────────────────────────────────

class TestCategoryConcentration:
    def test_concentrated_category_triggers_warning(self):
        """11 条全是光伏，100% > 80%"""
        records = make_records(11, {"category": "光伏"})
        _, warnings = check(records)
        assert any("分类过于集中" in w for w in warnings)

    def test_diverse_categories_no_warning(self):
        """分布均匀，不触发"""
        cats = ["光伏", "风电", "水电", "储能", "火电",
                "光伏", "风电", "水电", "储能", "火电", "核电"]
        records = [make_record({"id": i + 1, "category": c}) for i, c in enumerate(cats)]
        _, warnings = check(records)
        assert not any("分类过于集中" in w for w in warnings)

    def test_threshold_not_triggered_with_ten_or_fewer(self):
        """n_valid ≤ 10 不检查分类集中度"""
        records = make_records(10, {"category": "光伏"})
        _, warnings = check(records)
        assert not any("分类过于集中" in w for w in warnings)

    def test_exactly_80_percent_no_warning(self):
        """8/10 = 80%，不满足 > 0.8，不触发（但记录数 ≤ 10 也不检查，用 11 条测）"""
        records = [
            *make_records(9, {"category": "光伏"}),    # 9/11 ≈ 81.8%，触发
            make_record({"id": 10, "category": "风电"}),
            make_record({"id": 11, "category": "风电"}),
        ]
        _, warnings = check(records)
        # 9/11 ≈ 81.8% > 80%，应触发
        assert any("分类过于集中" in w for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Warning：具体项目容量缺失率过高（> 50%）
# ─────────────────────────────────────────────────────────────────────────────

class TestCapacityMissingRate:
    def test_high_capacity_missing_rate_triggers_warning(self):
        """6 条具体项目，4 条容量缺失 ≈ 67% > 50%"""
        records = [
            make_record({"id": 1, "record_type": "具体项目", "capacity_mw": None}),
            make_record({"id": 2, "record_type": "具体项目", "capacity_mw": None}),
            make_record({"id": 3, "record_type": "具体项目", "capacity_mw": None}),
            make_record({"id": 4, "record_type": "具体项目", "capacity_mw": None}),
            make_record({"id": 5, "record_type": "具体项目", "capacity_mw": "100"}),
            make_record({"id": 6, "record_type": "具体项目", "capacity_mw": "200"}),
        ]
        _, warnings = check(records)
        assert any("容量缺失率过高" in w for w in warnings)

    def test_low_capacity_missing_rate_no_warning(self):
        """6 条具体项目，1 条缺失 ≈ 17%，不触发"""
        records = [
            make_record({"id": i, "record_type": "具体项目", "capacity_mw": "100"})
            for i in range(1, 6)
        ] + [make_record({"id": 6, "record_type": "具体项目", "capacity_mw": None})]
        _, warnings = check(records)
        assert not any("容量缺失率过高" in w for w in warnings)

    def test_non_concrete_records_excluded_from_capacity_check(self):
        """设备订单 / 一般合作 不纳入容量缺失率计算"""
        records = [
            make_record({"id": i, "record_type": "设备订单", "capacity_mw": None})
            for i in range(1, 10)
        ]
        _, warnings = check(records)
        assert not any("容量缺失率过高" in w for w in warnings)

    def test_threshold_not_triggered_with_five_or_fewer(self):
        """具体项目 ≤ 5 条时不检查"""
        records = [
            make_record({"id": i, "record_type": "具体项目", "capacity_mw": None})
            for i in range(1, 6)
        ]
        _, warnings = check(records)
        assert not any("容量缺失率过高" in w for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Warning：国家缺失率过高（> 30%）
# ─────────────────────────────────────────────────────────────────────────────

class TestCountryMissingRate:
    def test_high_country_missing_rate_triggers_warning(self):
        """6 条记录，3 条缺国家 = 50% > 30%"""
        records = [
            make_record({"id": 1, "country": None}),
            make_record({"id": 2, "country": None}),
            make_record({"id": 3, "country": None}),
            make_record({"id": 4, "country": "巴基斯坦"}),
            make_record({"id": 5, "country": "巴基斯坦"}),
            make_record({"id": 6, "country": "巴基斯坦"}),
        ]
        _, warnings = check(records)
        assert any("国家缺失率过高" in w for w in warnings)

    def test_low_country_missing_rate_no_warning(self):
        """6 条记录，1 条缺 ≈ 17%，不触发"""
        records = [
            *make_records(5, {"country": "巴基斯坦"}),
            make_record({"id": 6, "country": None}),
        ]
        _, warnings = check(records)
        assert not any("国家缺失率过高" in w for w in warnings)

    def test_threshold_not_triggered_with_five_or_fewer(self):
        """n_valid ≤ 5 时不检查"""
        records = [make_record({"id": i, "country": None}) for i in range(1, 6)]
        _, warnings = check(records)
        assert not any("国家缺失率过高" in w for w in warnings)


# ─────────────────────────────────────────────────────────────────────────────
# 7. 返回值结构
# ─────────────────────────────────────────────────────────────────────────────

class TestReturnStructure:
    def test_returns_two_element_tuple(self):
        result = check(make_records(5))
        assert isinstance(result, tuple) and len(result) == 2

    def test_hard_errors_is_list(self):
        hard, _ = check(make_records(5))
        assert isinstance(hard, list)

    def test_warnings_is_list(self):
        _, warnings = check(make_records(5))
        assert isinstance(warnings, list)
