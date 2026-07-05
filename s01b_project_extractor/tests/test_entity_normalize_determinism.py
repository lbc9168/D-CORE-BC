"""v2 索引构建确定性测试（property）：索引结果不得依赖登记册 groups 文件行序。

背景：同一键被多组同优先级声明时，若胜者=首见=文件行序，则登记册重排会静默改变
实体归属。要求 _build_index_v2 对 groups 的任意排列输出完全相同的索引。
"""
import random

from s01b_project_extractor.stages.entity_normalize import _build_index_v2, _load_hierarchy_v2


def _real_groups() -> list[dict]:
    """加载真实登记册 groups（company_hierarchy_v2.json）。"""
    groups = _load_hierarchy_v2().get("groups", [])
    assert len(groups) > 100, "真实登记册应有 100+ 组，加载失败？"
    return groups


def test_index_identical_when_groups_reversed():
    """property：groups 整体倒序重建索引，全部键的解析结果与原顺序一致。"""
    groups = _real_groups()
    idx_fwd, stripped_fwd = _build_index_v2(groups)
    idx_rev, stripped_rev = _build_index_v2(list(reversed(groups)))
    assert idx_fwd == idx_rev, (
        "倒序后索引不一致，说明胜者依赖文件行序；差异键: "
        f"{ {k: (idx_fwd[k], idx_rev[k]) for k in idx_fwd if idx_rev.get(k) != idx_fwd[k]} }"
    )
    assert stripped_fwd == stripped_rev, (
        "倒序后去尾缀索引不一致；差异键: "
        f"{ {k: (stripped_fwd[k], stripped_rev[k]) for k in stripped_fwd if stripped_rev.get(k) != stripped_fwd[k]} }"
    )


def test_index_identical_when_groups_shuffled():
    """property：groups 随机洗牌（固定种子×5 轮）重建索引，结果均与原顺序一致。"""
    groups = _real_groups()
    idx_ref, stripped_ref = _build_index_v2(groups)
    rng = random.Random(20260706)
    for round_no in range(5):
        shuffled = groups[:]
        rng.shuffle(shuffled)
        idx, stripped = _build_index_v2(shuffled)
        assert idx == idx_ref, f"第 {round_no} 轮洗牌后索引不一致（胜者依赖行序）"
        assert stripped == stripped_ref, f"第 {round_no} 轮洗牌后去尾缀索引不一致"


def test_same_priority_tiebreak_is_canonical_lexicographic():
    """合成用例：两组同优先级（sub 名）声明同一键，胜者=canonical 字典序最小（任意但确定），
    与两组在列表中的先后无关。"""
    grp_a = {
        "canonical_name_cn": "乙集团有限公司",
        "aliases": [],
        "subsidiaries": [{"name_cn": "争议子公司有限公司", "legal_full_name_cn": "争议子公司有限公司"}],
    }
    grp_b = {
        "canonical_name_cn": "甲集团有限公司",
        "aliases": [],
        "subsidiaries": [{"name_cn": "争议子公司有限公司", "legal_full_name_cn": "争议子公司有限公司"}],
    }
    expected_winner = min("乙集团有限公司", "甲集团有限公司")  # 字典序最小的 canonical
    for order in ([grp_a, grp_b], [grp_b, grp_a]):
        idx, _ = _build_index_v2(order)
        canon, sub = idx["争议子公司有限公司"]
        assert canon == expected_winner, (
            f"同优先级并列应按 canonical 字典序决胜（期望 {expected_winner!r}），"
            f"顺序 {[g['canonical_name_cn'] for g in order]} 下得到 {canon!r}"
        )
        assert sub == "争议子公司有限公司"
