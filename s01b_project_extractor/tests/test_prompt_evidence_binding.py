"""S3/S4/S34 Prompt 的项目级证据绑定契约锚点。"""

from s01b_project_extractor.prompts.s3_finance import build_s3_prompt
from s01b_project_extractor.prompts.s4_entity import build_s4_prompt
from s01b_project_extractor.prompts.s34_entity_finance import build_s34_prompt


def test_finance_prompts_require_bound_exact_evidence():
    """单独及合并融资 Prompt 都必须要求逐字证据、项目锚点与 Other 范围。"""
    prompts = [
        build_s3_prompt("标题", "正文", "目标项目"),
        build_s34_prompt("标题", "正文", "目标项目", "光伏", []),
    ]
    for prompt in prompts:
        assert '"Financing Evidence"' in prompt
        assert "连续逐字引文" in prompt
        assert "组合层面援助仍填 Unknown" in prompt
        assert "银团属于 credit" in prompt


def test_entity_prompts_require_project_entity_role_binding():
    """单独及合并实体 Prompt 都必须要求逐实体参与证据。"""
    prompts = [
        build_s4_prompt("标题", "正文", "目标项目", "光伏", []),
        build_s34_prompt("标题", "正文", "目标项目", "光伏", []),
    ]
    for prompt in prompts:
        assert '"Participation Evidence"' in prompt
        assert "目标项目名称共享专名、场址或容量" in prompt
        assert "无法把该企业直接连接到目标项目" in prompt
        assert "但无法确认具体角色，角色填「Unknown」" in prompt
