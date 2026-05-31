"""s02a: 占位实体排除测试 — _entity_overlap 不得把 UNNAMED_ENTITY_PLACEHOLDER 当实体信号。

背景（E.2）：
  两个不同物理项目均只有"未具名中企"这条实体时，entity_overlap 不能返回 True，
  否则去重器会将它们错并为同一项目。占位实体代表"暂不知道公司名"，
  不是真实的中方主体，不能作为同一项目的实体匹配信号。
"""
import sys
from pathlib import Path

# 与 test_numeric_schema.py 相同的 sys.path shim：把仓库根（含 bri_enums.py）加进路径
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s02a_duplicate_merger.tools.duplicate_merger_tool import _entity_overlap
import bri_enums


def _make_proj(entities: list[str]) -> dict:
    """构造最小项目 dict，entities 为 implementing_entity 字符串列表。"""
    return {
        "project_name": "测试项目",
        "entities": [{"implementing_entity": e} for e in entities],
    }


class TestEntityOverlapExcludesPlaceholder:
    """占位实体不作为 entity_overlap 信号的守护测试。"""

    def test_only_placeholder_shared_returns_false(self):
        """两个项目的唯一共同实体是占位符 → overlap 应返回 False（不错并）。"""
        proj_a = _make_proj([bri_enums.UNNAMED_ENTITY_PLACEHOLDER])
        proj_b = _make_proj([bri_enums.UNNAMED_ENTITY_PLACEHOLDER])
        assert _entity_overlap(proj_a, proj_b) is False, (
            "占位实体不得作为实体重叠信号，两个 unnamed 项目不应被判断为同一项目"
        )

    def test_real_entity_shared_returns_true(self):
        """正向对照：两个项目共享真实公司名 → overlap 应返回 True（守护不过宽）。"""
        proj_a = _make_proj(["中国电建"])
        proj_b = _make_proj(["中国电建"])
        assert _entity_overlap(proj_a, proj_b) is True, (
            "两个项目共享真实实体时，_entity_overlap 仍应返回 True"
        )

    def test_placeholder_plus_different_real_entities_returns_false(self):
        """A 有占位+实体甲，B 有占位+实体乙 → 共同实体仅为占位 → False。"""
        proj_a = _make_proj([bri_enums.UNNAMED_ENTITY_PLACEHOLDER, "中国电建"])
        proj_b = _make_proj([bri_enums.UNNAMED_ENTITY_PLACEHOLDER, "中国能建"])
        assert _entity_overlap(proj_a, proj_b) is False, (
            "唯一共同实体为占位符时仍不应视为 overlap"
        )

    def test_real_entity_plus_placeholder_overlap_with_real_returns_true(self):
        """A 有占位+中国电建，B 也有中国电建 → 真实实体相交 → True。"""
        proj_a = _make_proj([bri_enums.UNNAMED_ENTITY_PLACEHOLDER, "中国电建"])
        proj_b = _make_proj(["中国电建"])
        assert _entity_overlap(proj_a, proj_b) is True, (
            "有真实实体相交时仍应返回 True，不因含占位符而受影响"
        )
