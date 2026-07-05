"""
最小验证测试：company_hierarchy_v2.json 的正确性断言。
运行：pytest supporting_docs/test_hierarchy_v2.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

SUPPORTING_DOCS = Path(__file__).parent
LEGACY_FILE = SUPPORTING_DOCS / "company_hierarchy.json"
DRAFT_FILE = SUPPORTING_DOCS / "company_hierarchy_draft_2026-07-05.json"
V2_FILE = SUPPORTING_DOCS / "company_hierarchy_v2.json"
BUILD_SCRIPT = SUPPORTING_DOCS / "build_hierarchy_v2.py"


def load_v2() -> dict:
    """加载 v2 输出文件。"""
    with V2_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def test_v2_file_exists():
    """company_hierarchy_v2.json 应存在且可解析。"""
    assert V2_FILE.exists(), f"输出文件不存在: {V2_FILE}"
    data = load_v2()
    assert isinstance(data, dict)
    assert data.get("schema_version") == 2
    assert "groups" in data


def test_build_script_is_idempotent(tmp_path):
    """转换脚本可重复运行，输出确定性（两次运行结果相同）。"""
    # 第一次已运行生成了 v2 文件，再跑一次检查结果一致
    first_run = V2_FILE.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"脚本第二次运行失败:\n{result.stderr}"
    second_run = V2_FILE.read_text(encoding="utf-8")
    assert first_run == second_run, "两次运行结果不一致，脚本非幂等"


def test_group_count_equals_legacy_plus_new_groups():
    """总组数 = 旧 19 组（全部保留）+ 草稿 97 新组 = 116。"""
    data = load_v2()
    with LEGACY_FILE.open(encoding="utf-8") as f:
        legacy = json.load(f)
    with DRAFT_FILE.open(encoding="utf-8") as f:
        draft = json.load(f)

    expected_legacy = len(legacy["companies"])  # 19
    expected_new = len(draft["new_groups"])       # 97
    expected_total = expected_legacy + expected_new  # 116

    actual = len(data["groups"])
    assert actual == expected_total, (
        f"总组数应为 {expected_total}（旧{expected_legacy}+新{expected_new}），实际={actual}"
    )


def test_no_duplicate_canonical_names():
    """canonical_name_cn 在 groups 列表中不得重复。"""
    data = load_v2()
    canonicals = [g["canonical_name_cn"] for g in data["groups"]]
    seen: dict[str, int] = {}
    for c in canonicals:
        seen[c] = seen.get(c, 0) + 1
    dups = {c: cnt for c, cnt in seen.items() if cnt > 1}
    assert not dups, f"canonical 重复: {dups}"


def test_no_cross_group_name_conflicts():
    """子公司名/别名不得归属两个不同组（跨组冲突检查）。"""
    data = load_v2()
    name_to_group: dict[str, str] = {}
    conflicts: list[str] = []

    for g in data["groups"]:
        canonical = g["canonical_name_cn"]
        names = [(canonical, "canonical")]
        for a in g.get("aliases", []):
            names.append((a, "alias"))
        for s in g.get("subsidiaries", []):
            names.append((s["name_cn"], "subsidiary"))

        for name, src in names:
            if not name:
                continue
            if name in name_to_group and name_to_group[name] != canonical:
                conflicts.append(
                    f"{name!r}({src}): {name_to_group[name]!r} vs {canonical!r}"
                )
            else:
                name_to_group[name] = canonical

    assert not conflicts, f"发现跨组冲突:\n" + "\n".join(f"  {c}" for c in conflicts)


def test_secondary_groups_not_in_any_subsidiaries():
    """旧文件的二级集团（有 parent）保留为独立组，不应作为任何组的 subsidiary 出现。"""
    with LEGACY_FILE.open(encoding="utf-8") as f:
        legacy = json.load(f)
    secondary_canonicals = {
        c["company_name_cn"]
        for c in legacy["companies"]
        if c.get("parent_company_cn") is not None
    }

    data = load_v2()
    sub_names: set[str] = set()
    for g in data["groups"]:
        for s in g.get("subsidiaries", []):
            sub_names.add(s["name_cn"])

    violations = secondary_canonicals & sub_names
    assert not violations, (
        f"以下二级集团名称出现在 subsidiaries 中（应是独立组）: {violations}"
    )


def test_all_draft_new_groups_present():
    """草稿中所有 97 个新组都应在 v2 中存在。"""
    with DRAFT_FILE.open(encoding="utf-8") as f:
        draft = json.load(f)
    data = load_v2()
    v2_canonicals = {g["canonical_name_cn"] for g in data["groups"]}

    missing = []
    for ng in draft["new_groups"]:
        canonical = ng["canonical_name_cn"]
        if canonical not in v2_canonicals:
            missing.append(canonical)

    assert not missing, f"草稿新组未出现在 v2 中: {missing}"


def test_all_additions_merged():
    """草稿中 14 条 additions 的子公司/别名均已并入对应组。"""
    with DRAFT_FILE.open(encoding="utf-8") as f:
        draft = json.load(f)
    data = load_v2()
    groups_map = {g["canonical_name_cn"]: g for g in data["groups"]}

    for add in draft["additions_to_existing_groups"]:
        canonical = add["canonical_name_cn"]
        assert canonical in groups_map, f"addition 目标组 {canonical!r} 不在 v2 中"
        target = groups_map[canonical]
        target_sub_names = {s["name_cn"] for s in target.get("subsidiaries", [])}
        target_aliases = set(target.get("aliases", []))

        for sub in add.get("subsidiaries", []):
            assert sub["name_cn"] in target_sub_names, (
                f"addition 子公司 {sub['name_cn']!r} 未并入 {canonical!r}"
            )
        for alias in add.get("aliases", []):
            assert alias in target_aliases, (
                f"addition 别名 {alias!r} 未并入 {canonical!r}"
            )


def test_legacy_provenance_marked():
    """旧文件转换的条目 provenance 应标为 legacy_v1。"""
    with LEGACY_FILE.open(encoding="utf-8") as f:
        legacy = json.load(f)
    legacy_names = {c["company_name_cn"] for c in legacy["companies"]}
    data = load_v2()

    for g in data["groups"]:
        canonical = g["canonical_name_cn"]
        if canonical in legacy_names:
            assert g.get("provenance") == "legacy_v1", (
                f"{canonical!r} 是旧文件条目，provenance 应为 legacy_v1，"
                f"实际={g.get('provenance')!r}"
            )
