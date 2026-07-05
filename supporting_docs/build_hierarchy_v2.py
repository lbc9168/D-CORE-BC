"""
一次性转换脚本：合并旧 company_hierarchy.json（19 组）与草稿 company_hierarchy_draft_2026-07-05.json
（97 新组 + 14 追加），生成正式 company_hierarchy_v2.json（schema_version=2）。

运行：python supporting_docs/build_hierarchy_v2.py
可复跑、确定性输出（不依赖外部状态）。
"""

import json
import logging
import sys
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("build_hierarchy_v2")

SCRIPT_DIR = Path(__file__).parent
LEGACY_FILE = SCRIPT_DIR / "company_hierarchy.json"
DRAFT_FILE = SCRIPT_DIR / "company_hierarchy_draft_2026-07-05.json"
OUTPUT_FILE = SCRIPT_DIR / "company_hierarchy_v2.json"

# ────────────────────────────────────────────────────
# 二级集团归属决定（legacy v1 中 parent_company_cn 非 null 的条目）
#
# 旧文件中有 2 个"有 parent"的条目：
#   1. 中国电力工程顾问集团有限公司（parent=中国能源建设集团有限公司）
#      → 决定：保留为独立组（v2 independent group）
#        理由：该集团下有 27 个子公司（电力设计院等），在能源工程设计领域有独立实体身份，
#        旧文件独立列出即表明它在项目数据中作为独立行为主体出现。
#        处理：从母公司（中国能源建设集团）的 subsidiaries 中移除该条目的 canonical 名，
#        避免 canonical 名同时作为别人的 subsidiary（数据语义冲突）。
#   2. 中国机械设备工程股份有限公司（parent=中国机械工业集团有限公司）
#      → 决定：保留为独立组（v2 independent group）
#        理由：同上逻辑——该集团下有自己的子公司（如机械工业第六设计研究院等），
#        保留独立组才不会丢失这些下级关系。
#        处理：同上——从母公司的 subsidiaries 中移除该 canonical 名。
#        另：该集团与母公司共享 4 个子公司（中工国际、机械工业建设集团、中国联合工程、
#        海洋航空集团），v2 中这些子公司保留在母公司（国机集团）下，
#        从该二级集团的 subsidiaries 中移除（子公司只归属一个组，归属上级）。
# ────────────────────────────────────────────────────
SECONDARY_GROUPS_TO_KEEP_INDEPENDENT = {
    "中国电力工程顾问集团有限公司",
    "中国机械设备工程股份有限公司",
}

# 从二级独立集团的 subsidiaries 中移除的子公司名（这些子公司保留在上级/母公司组中）
# 格式：{二级集团 canonical: {要移除的子公司名, ...}}
SECONDARY_GROUP_SUBS_TO_REMOVE: dict[str, set[str]] = {
    "中国机械设备工程股份有限公司": {
        "中工国际工程股份有限公司",
        "中国机械工业建设集团有限公司",
        "中国联合工程有限公司",
        "中国海洋航空集团有限公司",
    },
}


def load_json(path: Path) -> dict:
    """加载 JSON 文件，返回解析后的 dict。"""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"找不到文件: {path}")
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 解析失败 {path}: {e}")


def flatten_legacy_subsidiaries(
    company: dict,
    exclude_names: set[str] | None = None,
) -> list[dict]:
    """将旧 schema 的 subsidiaries_level2/level3 展平为 v2 subsidiaries 列表。

    exclude_names：如果某个子公司名在此集合中，则跳过（不列入该组 subsidiaries）。
    """
    if exclude_names is None:
        exclude_names = set()
    result = []
    for sub2 in company.get("subsidiaries_level2", []):
        name = sub2["name_cn"]
        if name not in exclude_names:
            result.append({"name_cn": name, "legal_full_name_cn": name})
        else:
            logger.info(f"    [去重] 从 {company['company_name_cn']!r} subsidiaries 移除 {name!r}（已是独立组或上级重复）")
        for sub3 in sub2.get("subsidiaries_level3", []):
            name3 = sub3["name_cn"]
            result.append({"name_cn": name3, "legal_full_name_cn": name3})
    return result


def convert_legacy_to_v2(
    company: dict,
    exclude_sub_names: set[str] | None = None,
) -> dict:
    """将旧 schema 单个 company 条目转为 v2 group 格式。"""
    canonical = company["company_name_cn"]
    is_central_soe = company.get("is_central_soe", False)
    subsidiaries = flatten_legacy_subsidiaries(company, exclude_names=exclude_sub_names)
    return {
        "canonical_name_cn": canonical,
        "is_central_soe": is_central_soe,
        "provenance": "legacy_v1",
        "as_of": "2026-03-19",  # 旧文件 generated_on
        "aliases": [],
        "subsidiaries": subsidiaries,
        "sources": [],
    }


def merge_group(base: dict, addition: dict) -> dict:
    """将 addition 的 aliases 与 subsidiaries 追加合并进 base，去重。"""
    existing_aliases = set(base.get("aliases", []))
    for a in addition.get("aliases", []):
        existing_aliases.add(a)
    base["aliases"] = sorted(existing_aliases)

    existing_sub_names = {s["name_cn"] for s in base.get("subsidiaries", [])}
    for sub in addition.get("subsidiaries", []):
        if sub["name_cn"] not in existing_sub_names:
            base["subsidiaries"].append(sub)
            existing_sub_names.add(sub["name_cn"])

    # 追加 sources（去重）
    existing_sources = set(base.get("sources", []))
    for src in addition.get("sources", []):
        existing_sources.add(src)
    base["sources"] = sorted(existing_sources)

    return base


def check_conflicts(groups: list[dict]) -> None:
    """冲突检查断言：canonical 唯一 + 跨组子公司/别名不冲突（违反则抛错，列出全部冲突）。"""
    canonical_counts: dict[str, int] = {}
    # name → group canonical（子公司名/别名 → 所属集团）
    name_to_group: dict[str, str] = {}
    conflicts: list[str] = []

    for g in groups:
        canonical = g["canonical_name_cn"]

        # 1. canonical 唯一性
        canonical_counts[canonical] = canonical_counts.get(canonical, 0) + 1

        # 2. 收集该组所有名称集合：canonical + aliases + sub names
        group_names: list[tuple[str, str]] = []  # (name, source_type)
        group_names.append((canonical, "canonical"))
        for a in g.get("aliases", []):
            group_names.append((a, "alias"))
        for s in g.get("subsidiaries", []):
            group_names.append((s["name_cn"], "subsidiary"))

        for name, src_type in group_names:
            if not name:
                continue
            if name in name_to_group and name_to_group[name] != canonical:
                conflicts.append(
                    f"跨组冲突: 名称 {name!r}({src_type}) 同时归属于 "
                    f"{name_to_group[name]!r} 和 {canonical!r}"
                )
            else:
                name_to_group[name] = canonical

    # 检查 canonical 重复
    dup_canonicals = [c for c, cnt in canonical_counts.items() if cnt > 1]
    if dup_canonicals:
        raise AssertionError(
            f"canonical_name_cn 重复（共 {len(dup_canonicals)} 个）:\n"
            + "\n".join(f"  {c}" for c in dup_canonicals)
        )

    if conflicts:
        raise AssertionError(
            f"发现跨组名称冲突（共 {len(conflicts)} 个）:\n"
            + "\n".join(f"  {c}" for c in conflicts)
        )

    logger.info("冲突检查通过：无 canonical 重复，无跨组名称冲突。")


def find_provenance_buling(groups: list[dict]) -> list[str]:
    """找出 provenance 含"补漏"关键词的组（用于报告，不阻塞）。"""
    result = []
    for g in groups:
        prov = g.get("provenance", "")
        if "补漏" in prov or "buling" in prov.lower():
            result.append(g["canonical_name_cn"])
    return result


def main() -> None:
    """主函数：加载两份源文件，转换合并，冲突检查，输出 v2。"""
    logger.info("加载源文件...")
    legacy_data = load_json(LEGACY_FILE)
    draft_data = load_json(DRAFT_FILE)

    # ── Step 1: 分析旧 19 组，确定二级集团情况 ───────────────
    legacy_companies = legacy_data.get("companies", [])
    logger.info(f"旧文件共 {len(legacy_companies)} 个条目")

    top_level: list[dict] = []
    has_parent: list[dict] = []
    for c in legacy_companies:
        if c.get("parent_company_cn") is None:
            top_level.append(c)
        else:
            has_parent.append(c)

    logger.info(f"  顶级集团: {len(top_level)} 个")
    logger.info(f"  有 parent 的条目: {len(has_parent)} 个")

    # 二级集团集合——这些名称不应出现在任何组的 subsidiaries 里（因为它们有独立组）
    secondary_group_names = set(hp["company_name_cn"] for hp in has_parent
                                if hp["company_name_cn"] in SECONDARY_GROUPS_TO_KEEP_INDEPENDENT)

    for hp in has_parent:
        name = hp["company_name_cn"]
        parent = hp["parent_company_cn"]
        decision = "保留为独立组" if name in SECONDARY_GROUPS_TO_KEEP_INDEPENDENT else "未预期"
        logger.info(f"  二级集团 {name!r} (parent={parent!r}) → {decision}")

    # ── Step 2: 转换旧条目为 v2 格式 ────────────────────────
    v2_groups_map: dict[str, dict] = {}  # canonical → group

    for c in legacy_companies:
        canonical = c["company_name_cn"]

        # 计算需要从该组 subsidiaries 移除的名称：
        # a. 二级独立集团的 canonical 名（不应作为别人的 subsidiary）
        # b. 该集团特定配置中要移除的子公司名
        exclude_names: set[str] = set()
        # 规则 a：从 subsidiaries 中移除那些在旧文件中被定义为独立二级集团的名称
        exclude_names.update(secondary_group_names)
        # 规则 b：该二级集团特定要移除的子公司（避免与母公司重复）
        if canonical in SECONDARY_GROUP_SUBS_TO_REMOVE:
            exclude_names.update(SECONDARY_GROUP_SUBS_TO_REMOVE[canonical])

        g = convert_legacy_to_v2(c, exclude_sub_names=exclude_names)

        if canonical in v2_groups_map:
            raise AssertionError(f"旧文件内部 canonical 重复: {canonical!r}")
        v2_groups_map[canonical] = g
        logger.info(f"  旧→v2: {canonical!r}（子公司 {len(g['subsidiaries'])} 个）")

    # ── Step 3: 草稿 97 新组 ─────────────────────────────────
    new_groups = draft_data.get("new_groups", [])
    logger.info(f"草稿新组: {len(new_groups)} 个")
    for ng in new_groups:
        canonical = ng["canonical_name_cn"]
        if canonical in v2_groups_map:
            raise AssertionError(
                f"草稿新组 {canonical!r} 与已有组（旧文件转换）重名，"
                "应放 additions_to_existing_groups 而非 new_groups"
            )
        v2_groups_map[canonical] = ng
        logger.info(f"  新组: {canonical!r}（子公司 {len(ng.get('subsidiaries', []))} 个）")

    # ── Step 4: 14 条 additions 并入对应组 ───────────────────
    additions = draft_data.get("additions_to_existing_groups", [])
    logger.info(f"草稿追加: {len(additions)} 条")
    for add in additions:
        canonical = add["canonical_name_cn"]
        if canonical not in v2_groups_map:
            raise AssertionError(
                f"additions 中 canonical={canonical!r} 在已有组中找不到匹配，"
                "请检查拼写或调整到 new_groups"
            )
        before_subs = len(v2_groups_map[canonical].get("subsidiaries", []))
        v2_groups_map[canonical] = merge_group(v2_groups_map[canonical], add)
        after_subs = len(v2_groups_map[canonical].get("subsidiaries", []))
        logger.info(
            f"  追加到 {canonical!r}: "
            f"+aliases={add.get('aliases', [])} "
            f"子公司 {before_subs}→{after_subs}"
        )

    # ── Step 5: 组合 groups 列表（保持旧→新顺序）────────────
    groups_list = list(v2_groups_map.values())

    # ── Step 6: 冲突检查 ─────────────────────────────────────
    logger.info("执行冲突检查...")
    check_conflicts(groups_list)

    # ── Step 7: 统计 ─────────────────────────────────────────
    total_groups = len(groups_list)
    total_subs = sum(len(g.get("subsidiaries", [])) for g in groups_list)
    total_aliases = sum(len(g.get("aliases", [])) for g in groups_list)
    central_soe_count = sum(1 for g in groups_list if g.get("is_central_soe") is True)
    buling_groups = find_provenance_buling(groups_list)

    logger.info("=" * 60)
    logger.info(
        f"统计：总组数={total_groups}  子公司数={total_subs}  "
        f"别名数={total_aliases}  央企组数={central_soe_count}"
    )
    if buling_groups:
        logger.info(f"★ 补漏组（{len(buling_groups)} 个，待用户复看）: {buling_groups}")
    logger.info("=" * 60)

    # ── Step 8: 输出 ─────────────────────────────────────────
    output = {
        "schema_version": 2,
        "generated_on": str(date.today()),
        "notes": [
            "由 build_hierarchy_v2.py 自动生成，合并旧 company_hierarchy.json (19 组) + "
            "company_hierarchy_draft_2026-07-05.json (97 新组 + 14 追加)。",
            "旧版二级集团（中国电力工程顾问集团有限公司、中国机械设备工程股份有限公司）"
            "均保留为独立组（见脚本注释说明）。",
            "冲突处理：二级集团的 canonical 名已从其母公司 subsidiaries 中移除；"
            "国机设备与国机集团的共享子公司（中工国际等 4 家）保留在国机集团，"
            "从国机设备的 subsidiaries 中移除。",
            "legacy_v1 条目：aliases=[], sources=[]；provenance='legacy_v1'；as_of='2026-03-19'。",
        ],
        "groups": groups_list,
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)

    logger.info(f"已输出: {OUTPUT_FILE}")
    logger.info(
        f"最终统计: 总组数={total_groups}, 子公司数={total_subs}, "
        f"别名数={total_aliases}, 央企组数={central_soe_count}"
    )


if __name__ == "__main__":
    main()
