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
    """默认维护入口仅校验，现行主表及后补裁决保持逐字不变。"""
    # 历史初始化来源不能覆盖已确认的后补裁决。
    first_run = V2_FILE.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-B", str(BUILD_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, f"脚本第二次运行失败:\n{result.stderr}"
    second_run = V2_FILE.read_text(encoding="utf-8")
    assert first_run == second_run, "两次运行结果不一致，脚本非幂等"


def test_group_count_retains_bootstrap_and_requires_evidence_for_later_groups():
    """历史组数保留；核实后新组必须有来源，不能由旧生成器计数锁死。"""
    data = load_v2()
    with LEGACY_FILE.open(encoding="utf-8") as f:
        legacy = json.load(f)
    with DRAFT_FILE.open(encoding="utf-8") as f:
        draft = json.load(f)

    expected_legacy = len(legacy["companies"])  # 19
    expected_new = len(draft["new_groups"])       # 97
    expected_total = expected_legacy + expected_new  # 116

    reviewed = [g for g in data["groups"] if g.get("provenance") == "web_verified_2026-09-17"]
    assert len(data["groups"]) == expected_total + len(reviewed)
    assert all(g.get("verification") and g.get("sources") for g in reviewed)


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
    """草稿中 14 条 additions 的子公司/别名均已并入对应组。

    例外（终审 C2，与 build 脚本 ADDITION_SUBS_TO_DROP 同步）：国机集团组的
    「中设集团」条目其 legal 名=CMEC 独立组 canonical，属重复登记，build 时已删除
    （简称改登记为 CMEC 组 alias），此处跳过断言。"""
    dropped = {("中国机械工业集团有限公司", "中设集团")}

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
            if (canonical, sub["name_cn"]) in dropped:
                continue
            if canonical == "阿特斯阳光电力集团股份有限公司" and sub["name_cn"] == "Recurrent Energy, LLC":
                assert any(d.get("fact") == "removed_false_alias_and_CSI_Solar_parent_link"
                           and d.get("source", "").startswith("https://www.sec.gov/")
                           for d in target.get("identity_evidence", []))
                registry = json.loads((SUPPORTING_DOCS.parent / "developer_registry.json").read_text(encoding="utf-8"))
                recurrent = next(c for c in registry["companies"] if c["canonical_cn"] == "Recurrent Energy")
                assert recurrent["identity_status"] == "confirmed"
                canadian = groups_map["Canadian Solar Inc."]
                assert any(s["name_cn"] == sub["name_cn"] for s in canadian["subsidiaries"])
                assert recurrent["entity_id"] is None
                continue
            assert sub["name_cn"] in target_sub_names, (
                f"addition 子公司 {sub['name_cn']!r} 未并入 {canonical!r}"
            )
        for alias in add.get("aliases", []):
            if canonical == "阿特斯阳光电力集团股份有限公司" and alias == "Recurrent Energy":
                assert any(d.get("fact") == "removed_false_alias_and_CSI_Solar_parent_link"
                           and d.get("source", "").startswith("https://www.sec.gov/")
                           for d in target.get("identity_evidence", []))
                assert alias not in target_aliases
                continue
            if canonical == "中国长江三峡集团有限公司" and alias == "中水对外":
                # The historical bootstrap is superseded by an evidenced 2023 handover.
                decisions = target.get("identity_evidence", [])
                assert any(d.get("fact") == "historical_group_link_ended"
                           and d.get("source", "").startswith("https://www.ctg.com.cn/")
                           for d in decisions)
                cccc = groups_map["中国交通建设集团有限公司"]
                matches = [s for s in cccc["subsidiaries"]
                           if alias in s.get("aliases", [])]
                assert len(matches) == 1
                assert matches[0]["legal_full_name_cn"] == "中国水利电力对外有限公司"
                assert matches[0]["parent_entity_id"] == cccc["entity_id"]
                assert matches[0].get("identity_evidence")
                continue
            if alias not in target_aliases:
                moved = [s for s in target.get("subsidiaries", [])
                         if alias in s.get("aliases", []) or alias == s.get("legal_full_name_cn")]
                if moved:
                    assert len(moved) == 1
                    assert moved[0].get("legal_full_name_cn")
                    assert moved[0].get("entity_id") != target.get("entity_id")
                    assert moved[0].get("identity_evidence") or moved[0].get("verification", {}).get("sources"), "Alias-to-child correction requires source evidence"
                    continue
            assert alias in target_aliases, (
                f"addition 别名 {alias!r} 未并入 {canonical!r}"
            )


def test_c2_soe_overrides_applied():
    """终审 C2：中电工程与 CMEC 两组 is_central_soe 应为 true（恢复主库先前口径，
    两家均为 SASAC 央企子集团；用户可复议——若复议推翻，改 build 脚本 LEGACY_SOE_OVERRIDES）。"""
    data = load_v2()
    m = {g["canonical_name_cn"]: g for g in data["groups"]}
    assert m["中国电力工程顾问集团有限公司"]["is_central_soe"] is True
    assert m["中国机械设备工程股份有限公司"]["is_central_soe"] is True


def test_c2_zhongshe_alias_moved_to_cmec():
    """终审 C2：「中设集团」不再是国机集团组的 subsidiary，改为 CMEC 组 alias。"""
    data = load_v2()
    m = {g["canonical_name_cn"]: g for g in data["groups"]}
    sinomach_subs = {s["name_cn"] for s in m["中国机械工业集团有限公司"]["subsidiaries"]}
    assert "中设集团" not in sinomach_subs, "中设集团 不应仍在国机集团组 subsidiaries"
    assert "中设集团" in m["中国机械设备工程股份有限公司"].get("aliases", []), (
        "中设集团 应为 CMEC 组 alias（保留简称解析映射）"
    )


def test_no_sub_legal_duplicates_other_group_canonical():
    """任何组 subsidiaries[].legal_full_name_cn 不得与其他组 canonical 重复
    （此类重复登记会触发 entity_normalize 构建期跨集团歧义 WARNING）。"""
    data = load_v2()
    canonicals = {g["canonical_name_cn"] for g in data["groups"]}
    violations = []
    for g in data["groups"]:
        for s in g.get("subsidiaries", []):
            legal = s.get("legal_full_name_cn")
            if legal and legal in canonicals and legal != g["canonical_name_cn"]:
                violations.append(f"{g['canonical_name_cn']!r} 的 sub_legal {legal!r} 是其他组 canonical")
    assert not violations, "发现 sub_legal 重复登记:\n" + "\n".join(violations)


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
