"""母子公司/曾用名归一：文中实体名 → (顶级集团规范名, 子公司规范名)；复用 company_hierarchy.json。

JSON 结构（确认自 supporting_docs/company_hierarchy.json）:
  companies[]:
    company_name_cn     — 公司全称（顶级集团 parent_company_cn=null，二级集团有值）
    parent_company_cn   — null 表示顶级；非 null 指向上级集团 company_name_cn
    subsidiaries_level2[]: {name_cn, subsidiaries_level3?: [{name_cn}]}
  无 aliases 字段。
"""
import logging
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _hierarchy_index() -> dict[str, tuple[str, str]]:
    """构建「任意公司名 → (顶级集团规范名, 子公司规范名)」索引，复用 company_hierarchy.json。

    索引规则：
      - 顶级集团自身 → (parent, "")
      - 二级集团 company_name_cn（有 parent_company_cn）→ 追溯到根顶级 → (root, "")
      - subsidiaries_level2.name_cn → (root_parent, sub2_name)
      - subsidiaries_level3.name_cn → (root_parent, sub3_name)
    """
    from s01b_project_extractor.tools.project_extractor_tool import _load_hierarchy_data

    data = _load_hierarchy_data()
    companies = data.get("companies", [])

    # 1. 建立 company_name_cn → parent_company_cn 的快速查找（用于追溯根节点）
    parent_map: dict[str, str | None] = {
        c["company_name_cn"]: c.get("parent_company_cn")
        for c in companies
    }

    def _find_root(name: str) -> str:
        """沿 parent_company_cn 链追溯到顶级集团（parent=null）的规范名。"""
        visited: set[str] = set()
        current = name
        while True:
            if current in visited:
                logger.warning("母子公司层级存在循环依赖，停止追溯: %s", current)
                return current
            visited.add(current)
            p = parent_map.get(current)
            if p is None:
                return current
            current = p

    index: dict[str, tuple[str, str]] = {}

    for company in companies:
        cn = company["company_name_cn"]
        root = _find_root(cn)

        # 公司自身（顶级或二级集团）→ (root, "")；子公司规范名由下级条目填充
        if cn not in index:
            index[cn] = (root, "")

        # 处理 subsidiaries_level2
        for sub2 in company.get("subsidiaries_level2", []):
            sub2_name = sub2["name_cn"]
            # sub2 的根：若 sub2 本身也是 companies 条目则用 _find_root，否则直接用 root
            sub2_root = _find_root(sub2_name) if sub2_name in parent_map else root
            if sub2_name not in index:
                index[sub2_name] = (sub2_root, sub2_name)

            # 处理 subsidiaries_level3
            for sub3 in sub2.get("subsidiaries_level3", []):
                sub3_name = sub3["name_cn"]
                if sub3_name not in index:
                    index[sub3_name] = (sub2_root, sub3_name)

    logger.info("母子公司归一索引已构建：%d 条目", len(index))
    return index


def normalize_entity(name: str) -> tuple[str, str]:
    """实体名归一为 (顶级集团, 子公司)；空→('Unknown','')；未匹配→原名透传（子公司空）。

    匹配优先级：
      1. 精确匹配索引键
      2. 包含匹配兜底（文中名含规范键 或 规范键含文中名），取最长键避免短键误匹配
      3. 未匹配：原名透传，sub=""
    """
    name = (name or "").strip()
    if not name:
        return "Unknown", ""

    idx = _hierarchy_index()

    # 1. 精确匹配
    if name in idx:
        return idx[name]

    # 2. 包含匹配兜底（取最长匹配键，避免 2 字前缀误匹配）
    best_key: str | None = None
    best_val: tuple[str, str] | None = None
    for key, val in idx.items():
        if key and (key in name or name in key):
            if best_key is None or len(key) > len(best_key):
                best_key = key
                best_val = val
    if best_val is not None:
        return best_val

    # 3. 透传
    return name, ""
