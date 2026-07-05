"""纯查表实体解析器：verbatim → (implementing, subsidiary)，读 company_hierarchy_v2.json。

三层匹配顺序（无包含匹配）：
  1. 精确名（canonical/subsidiaries 全名 及 legal_full_name_cn）
  2. 别名（aliases[] 与 subsidiaries[].legal_full_name_cn）
  3. 去尾缀归一化（输入与表键都做同样的尾缀剥离后精确比对；预构索引，O(1) 查询）
未命中：透传原文 + 写 WARNING 日志（供季度频次补录）。

不变量（下游 ⑨-3 回填依赖）：
  - subsidiary 非空 ⇔ 它是原文名（verbatim）
  - subsidiary 空  ⇔ implementing 即原文名（或 "Unknown"）
"""
import logging
from functools import lru_cache

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

    索引构建规则（显式优先级：canonical > sub_name_cn/sub_legal > alias；同级冲突首见胜）：
      - 集团 canonical_name_cn      → (canonical, "")
      - 子公司 name_cn              → (canonical, name_cn)   ── 路径1
      - 子公司 legal_full_name_cn   → (canonical, legal_full_name_cn)  ── 注册为子公司别名
      - 集团 aliases[]              → (canonical, "")  ── 路径2b：调用方在命中时设 sub=verbatim
    歧义检测：同一键被多组声明时 → WARNING（列出键+两组名）；同级冲突首见胜并 WARNING。
    同时构建去尾缀预索引（_stripped_index），避免 normalize_entity 线性扫描（25k 行回填性能需求）。
    """
    data = _load_hierarchy_v2()
    groups = data.get("groups", [])
    index: dict[str, tuple[str, str]] = {}

    # 第一遍：按优先级收集所有候选（key → list of (priority, canon, sub_key, role_desc)）
    # priority: 0=canonical, 1=sub_name_cn/sub_legal, 2=alias
    candidates: dict[str, list[tuple[int, str, str, str]]] = {}

    for grp in groups:
        canon = grp["canonical_name_cn"]

        # 优先级0：集团 canonical
        candidates.setdefault(canon, []).append((0, canon, "", f"canonical of {canon}"))

        # 优先级1：子公司 name_cn / legal_full_name_cn
        for sub in grp.get("subsidiaries", []):
            name_cn = sub.get("name_cn", "")
            legal   = sub.get("legal_full_name_cn") or ""
            if name_cn:
                candidates.setdefault(name_cn, []).append((1, canon, name_cn, f"sub_name_cn of {canon}"))
            if legal and legal != name_cn:
                candidates.setdefault(legal, []).append((1, canon, legal, f"sub_legal of {canon}"))

        # 优先级2：集团别名
        for alias in grp.get("aliases", []):
            if alias:
                candidates.setdefault(alias, []).append((2, canon, "", f"alias of {canon}"))

    # 第二遍：按优先级选胜者；同键多组声明 → WARNING
    for key, entries in candidates.items():
        # 按 priority 升序，同 priority 按首见（list 顺序已按数据顺序）
        entries_sorted = sorted(entries, key=lambda x: x[0])
        winner_priority, winner_canon, winner_sub, winner_role = entries_sorted[0]

        # 歧义检测：同一键被不同集团声明（winner_canon 不唯一）
        canon_set = {canon for _, canon, _, _ in entries_sorted}
        if len(canon_set) > 1:
            other_roles = [role for pri, canon, sub, role in entries_sorted if canon != winner_canon]
            logger.warning(
                "entity_normalize 歧义键（跨集团）: %r → 采用 %s；冲突来源: %s",
                key, winner_role, other_roles
            )
        elif len(entries_sorted) > 1 and entries_sorted[0][0] == entries_sorted[1][0]:
            # 同级同集团多次注册（如 name_cn == legal_full_name_cn 重复 insert），静默（数据内部冗余）
            pass

        index[key] = (winner_canon, winner_sub)

    # ── 构建去尾缀预索引（stripped_key → 首个命中的 index entry）──────────────
    # 同一 stripped_key 被多个原始键占用 → WARNING（跨集团时）
    stripped: dict[str, tuple[str, str]] = {}
    stripped_owners: dict[str, list[str]] = {}  # stripped_key → list of original keys（for warning）

    for key, (canon, sub_key) in index.items():
        sk = _strip_suffix(key)
        if sk == key:
            continue  # 无尾缀，不进去尾缀索引
        stripped_owners.setdefault(sk, []).append(key)
        if sk not in stripped:
            stripped[sk] = (canon, sub_key)

    # 检测去尾缀碰撞：同一 stripped_key 的原始键属于不同 canon
    for sk, orig_keys in stripped_owners.items():
        canons_for_sk = {index[k][0] for k in orig_keys}
        if len(canons_for_sk) > 1:
            logger.warning(
                "entity_normalize 去尾缀碰撞: 剥缀后 %r 被多组声明 → 首见胜；原始键: %s",
                sk, orig_keys
            )
        elif len(orig_keys) > 1:
            # 同集团内去尾缀碰撞（如 canonical vs sub_name_cn 尾缀不同），也 warning
            logger.warning(
                "entity_normalize 去尾缀碰撞（同集团）: 剥缀后 %r 被同集团不同键声明 → 首见胜；原始键: %s",
                sk, orig_keys
            )

    # 将去尾缀索引存为模块级变量，供 normalize_entity O(1) 查询
    global _stripped_index_cache
    _stripped_index_cache = stripped

    logger.info("entity_normalize v2 索引已构建：%d 条目，去尾缀索引 %d 条（%d 个集团）",
                len(index), len(stripped), len(groups))
    return index


# 去尾缀预构索引（由 _hierarchy_index_v2 构建，normalize_entity 直接查，O(1)）
_stripped_index_cache: dict[str, tuple[str, str]] = {}


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

    # ── 层2/3：去尾缀归一化精确比对（O(1) 预构索引查询）──────────────────────
    stripped_input = _strip_suffix(name)
    if stripped_input != name:  # 有实际剥除才搜索，避免退化为精确匹配重复
        # 确保去尾缀索引已构建（调用 _hierarchy_index_v2 时会同时填充 _stripped_index_cache）
        if stripped_input in _stripped_index_cache:
            canon, sub_key = _stripped_index_cache[stripped_input]
            if sub_key:
                # 路径2d：去尾缀后命中子公司
                return canon, name
            else:
                # 路径2c：去尾缀后命中集团 canonical 或别名，sub=verbatim（变体）
                return canon, name

    # ── 路径3：未命中 ─────────────────────────────────────────────────────────
    logger.warning("entity_normalize 未命中（透传）: %s", name)
    return name, ""
