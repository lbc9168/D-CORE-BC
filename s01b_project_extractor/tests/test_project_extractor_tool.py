"""
project_extractor_tool 基础逻辑测试
"""

import pytest

from s01b_project_extractor.tools.project_extractor_tool import (
    _normalize_implementing_entity,
)


class TestNormalizeImplementingEntity:
    @pytest.mark.parametrize("value", [None, "", "  ", "\n\t"])
    def test_empty_like_values_become_unknown(self, value):
        assert _normalize_implementing_entity(value) == "Unknown"

    def test_non_empty_value_is_trimmed(self):
        assert _normalize_implementing_entity("  中国电建  ") == "中国电建"

    def test_literal_unknown_is_kept(self):
        assert _normalize_implementing_entity("Unknown") == "Unknown"
