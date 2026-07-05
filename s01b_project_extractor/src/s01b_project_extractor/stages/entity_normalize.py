"""纯查表实体解析器：verbatim → (implementing, subsidiary)，读 company_hierarchy_v2.json。

三层匹配顺序（无包含匹配）：
  1. 精确名（canonical/subsidiaries 全名 及 legal_full_name_cn）
  2. 别名（aliases[] 与 subsidiaries[].legal_full_name_cn）
  3. 去尾缀归一化（输入与表键都做同样的尾缀剥离后精确比对）
未命中：透传原文 + 写 WARNING 日志（供季度频次补录）。

不变量（下游 ⑨-3 回填依赖）：
  - subsidiary 非空 ⇔ 它是原文名（verbatim）
  - subsidiary 空  ⇔ implementing 即原文名（或 "Unknown"）
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# 尾缀列表：剥离后做精确比对（两端均剥离，尾缀顺序从长到短避免贪心截断）
_CORP_SUFFIXES = (
    "集团股份有限公司",
    "集团有限责任公司",
    "集团有限公司",
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团公司",
    "集团",
)


def _strip_suffix(name: str) -> str:
    """剥除常见公司尾缀后返回核心名（剥第一个匹配就停）。"""
    for sfx in _CORP_SUFFIXES:
        if name.endswith(sfx):
            return name[: len(name) - len(sfx)]
    return name


@lru_cache(maxsize=1)
def _hierarchy_index_v2() -> dict[str, tuple[str, str]]:
    """构建 v2 查表索引：「任意名称 → (集团 canonical_name_cn, sub_name_or_empty)」。

    索引构建规则：
      - 集团 canonical_name_cn      → (canonical, "")
      - 集团 aliases[]              → (canonical, "")  ── 路径2b：verbatim≠canonical，
                                                            调用方在 normalize_entity 中覆盖 sub=verbatim
      - 子公司 name_cn              → (canonical, name_cn)   ── 路径1
      - 子公司 legal_full_name_cn   → (canonical, legal_full_name_cn)  ── 注册为子公司别名
    """
    data = _load_hierarchy_v2()
    groups = data.get("groups", [])
    index: dict[str, tuple[str, str]] = {}

    for grp in groups:
        canon = grp["canonical_name_cn"]

        # 集团本身
        if canon not in index:
            index[canon] = (canon, "")

        # 集团别名（路径2b 候选；调用方将 sub 设为 verbatim）
        for alias in grp.get("aliases", []):
            if alias and alias not in index:
                index[alias] = (canon, "")  # sub 由调用方填 verbatim

        # 子公司条目
        for sub in grp.get("subsidiaries", []):
            name_cn = sub.get("name_cn", "")
            legal   = sub.get("legal_full_name_cn") or ""

            if name_cn and name_cn not in index:
                index[name_cn] = (canon, name_cn)

            if legal and legal != name_cn and legal not in index:
                index[legal] = (canon, legal)

    logger.info("entity_normalize v2 索引已构建：%d 条目（%d 个集团）", len(index), len(groups))
    return index


def _load_hierarchy_v2() -> dict:
    """加载 company_hierarchy_v2.json（schema_version=2）；文件不在时返回空结构。"""
    from s01b_project_extractor.tools.project_extractor_tool import _load_hierarchy_v2_data
    return _load_hierarchy_v2_data()


def normalize_entity(verbatim: str) -> tuple[str, str]:
    """将文中实体原文解析为 (implementing, subsidiary)；空 → ('Unknown','')；未命中透传。

    契约（四路径）：
      1. 子公司命中           → (集团 canonical, verbatim)
      2a. 集团精确命中        → (canonical, "")
      2b. 集团别名命中        → (canonical, verbatim)
      2c. 去尾缀后集团命中   → (canonical, verbatim)
      2d. 去尾缀后子公司命中 → (集团 canonical, verbatim)
      3.  未命中              → (verbatim, "") + WARNING 日志
    不变量：subsidiary 非空 ⇔ verbatim；subsidiary 空 ⇔ implementing==verbatim。
    """
    name = (verbatim or "").strip()
    if not name:
        return "Unknown", ""

    idx = _hierarchy_index_v2()

    # ── 层1：精确名匹配（canonical / sub name_cn / sub legal_full_name_cn）──
    if name in idx:
        stored_canon, stored_sub = idx[name]
        if stored_sub:
            # 子公司命中（路径1）：sub = verbatim（原文）
            return stored_canon, name
        else:
            # 集团自身或别名精确命中
            if name == stored_canon:
                # 路径2a：verbatim == canonical
                return stored_canon, ""
            else:
                # 路径2b：verbatim 是别名（≠ canonical）
                return stored_canon, name

    # ── 层2/3：去尾缀归一化精确比对 ──────────────────────────────────────────
    stripped_input = _strip_suffix(name)
    if stripped_input != name:  # 有实际剥除才搜索，避免退化为精确匹配重复
        for key, (canon, sub_key) in idx.items():
            stripped_key = _strip_suffix(key)
            if stripped_key == stripped_input:
                # 去尾缀后命中
                if sub_key:
                    # 路径2d：去尾缀后命中子公司
                    return canon, name
                else:
                    # 路径2c：去尾缀后命中集团 canonical 或别名
                    if key == canon:
                        return canon, name  # 集团命中，sub=verbatim（变体）
                    else:
                        return canon, name  # 别名命中，sub=verbatim

    # ── 路径3：未命中 ─────────────────────────────────────────────────────────
    logger.warning("entity_normalize 未命中（透传）: %s", name)
    return name, ""
