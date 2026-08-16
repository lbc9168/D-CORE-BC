"""S02A 实体身份键必须保留同集团、同角色下的不同具体子公司。"""

import pandas as pd

from s02a_duplicate_merger.tools.duplicate_merger_tool import _group_into_logical_projects


def test_grouping_uses_parent_subsidiary_and_role_identity():
    """两个子公司都保留，完全相同的第三行仍被去重。"""
    base = {
        "Source Link": "https://example.test/project",
        "Project Name": "测试项目",
        "Country": "测试国",
        "Category": "光伏",
        "Capacity (MW)": "100",
        "Project Stage": "在建",
        "Implementing Chinese Entity": "中国能源建设集团有限公司",
        "Type of involvement of Chinese Entities": "EPC总承包",
    }
    frame = pd.DataFrame(
        [
            {**base, "Subsidiary Name": "中国能建A院"},
            {**base, "Subsidiary Name": "中国能建B院"},
            {**base, "Subsidiary Name": "中国能建A院"},
        ]
    )

    projects = _group_into_logical_projects(frame)

    assert len(projects) == 1
    assert [entity["subsidiary_name"] for entity in projects[0]["entities"]] == [
        "中国能建A院",
        "中国能建B院",
    ]
