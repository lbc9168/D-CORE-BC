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
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VERIFIED_CHINESE = "verified_chinese"
VERIFIED_NON_CHINESE = "verified_non_chinese"
UNMATCHED_NEEDS_REVIEW = "unmatched_needs_review"


class _RegistryCompany(BaseModel):
    """校验 developer_registry 中与企业国别门禁有关的最小字段。"""

    canonical_cn: str
    aliases: list[str] = Field(default_factory=list)
    is_chinese: bool
    parent_cn: str | None = None


@dataclass(frozen=True)
class EntityResolution:
    """保存实体归一结果及是否具备可发布的中企身份证据。"""

    implementing: str
    subsidiary: str
    verification_status: str
    matched_source: str

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


def _build_index_v2(
    groups: list[dict],
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, str]]]:
    """纯函数：由 groups 列表构建 (精确索引, 去尾缀索引)，供 _hierarchy_index_v2 缓存复用。

    索引构建规则（显式优先级：canonical > sub_name_cn/sub_legal > alias）：
      - 集团 canonical_name_cn      → (canonical, "")
      - 子公司 name_cn              → (canonical, name_cn)   ── 路径1
      - 子公司 legal_full_name_cn   → (canonical, legal_full_name_cn)  ── 注册为子公司别名
      - 集团 aliases[]              → (canonical, "")  ── 路径2b：调用方在命中时设 sub=verbatim
    同优先级并列时按 (canonical, sub 键) 字典序决胜（任意但确定，不依赖文件行序）。
    歧义检测：同一键被多组声明时 → WARNING（列出键+两组名）。输出与 groups 排列顺序无关。
    同时构建去尾缀预索引（_stripped_index），避免 normalize_entity 线性扫描（25k 行回填性能需求）。
    """
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
        # 按 priority 升序；同优先级并列时按 (canonical, sub 键) 字典序取最小——
        # 该决胜规则本身是任意的，但保证确定（索引不依赖登记册文件行序）；
        # 数据侧应显式消歧（build_hierarchy_v2.py），此处仅为未来新数据保底。
        entries_sorted = sorted(entries, key=lambda x: (x[0], x[1], x[2]))
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

    # ── 构建去尾缀预索引（stripped_key → 胜者 index entry）────────────────────
    # 同一 stripped_key 被多个原始键占用 → WARNING（跨集团时）
    stripped: dict[str, tuple[str, str]] = {}
    stripped_owners: dict[str, list[str]] = {}  # stripped_key → list of original keys（for warning）

    for key in index:
        sk = _strip_suffix(key)
        if sk == key:
            continue  # 无尾缀，不进去尾缀索引
        stripped_owners.setdefault(sk, []).append(key)

    # 检测去尾缀碰撞并选胜者：多个原始键竞争时按 (canon, sub 键, 原始键) 字典序取最小——
    # 规则任意但确定，保证去尾缀索引同样不依赖登记册文件行序
    for sk, orig_keys in stripped_owners.items():
        winner_key = min(orig_keys, key=lambda k: (index[k][0], index[k][1], k))
        stripped[sk] = index[winner_key]
        canons_for_sk = {index[k][0] for k in orig_keys}
        if len(canons_for_sk) > 1:
            logger.warning(
                "entity_normalize 去尾缀碰撞: 剥缀后 %r 被多组声明 → 按字典序决胜；原始键: %s",
                sk, orig_keys
            )
        elif len(orig_keys) > 1:
            # 同集团内去尾缀碰撞（如 canonical vs sub_name_cn 尾缀不同），也 warning
            logger.warning(
                "entity_normalize 去尾缀碰撞（同集团）: 剥缀后 %r 被同集团不同键声明 → 按字典序决胜；原始键: %s",
                sk, orig_keys
            )

    return index, stripped


@lru_cache(maxsize=1)
def _hierarchy_index_v2() -> dict[str, tuple[str, str]]:
    """构建 v2 查表索引（进程级缓存）：「任意名称 → (集团 canonical, sub_name_or_empty)」；
    同时把去尾缀索引写入模块级 _stripped_index_cache 供 normalize_entity O(1) 查询。"""
    data = _load_hierarchy_v2()
    groups = data.get("groups", [])
    index, stripped = _build_index_v2(groups)

    global _stripped_index_cache
    _stripped_index_cache = stripped

    logger.info("entity_normalize v2 索引已构建：%d 条目，去尾缀索引 %d 条（%d 个集团）",
                len(index), len(stripped), len(groups))
    return index


# 去尾缀预构索引（由 _hierarchy_index_v2 构建，normalize_entity 直接查，O(1)）
_stripped_index_cache: dict[str, tuple[str, str]] = {}


def _load_hierarchy_v2() -> dict:
    """加载 company_hierarchy_v2.json（schema_version=2）；文件不在时返回空结构。"""
    hierarchy_path = Path(__file__).resolve().parents[4] / "supporting_docs" / "company_hierarchy_v2.json"
    if not hierarchy_path.exists():
        logger.error("中企层级表不存在: %s", hierarchy_path)
        return {"groups": []}
    try:
        return json.loads(hierarchy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("中企层级表加载失败: %s | path=%s", exc, hierarchy_path)
        raise


@lru_cache(maxsize=1)
def _nationality_index() -> dict[str, _RegistryCompany]:
    """从受控登记册构建名称/别名到企业国别记录的确定性索引。"""
    registry_path = Path(__file__).resolve().parents[4] / "developer_registry.json"
    if not registry_path.exists():
        logger.error("中企国别登记表不存在: %s", registry_path)
        return {}
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
        companies = [_RegistryCompany(**item) for item in payload.get("companies", [])]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("中企国别登记表加载失败: %s | path=%s", exc, registry_path)
        raise
    index: dict[str, _RegistryCompany] = {}
    for company in companies:
        for name in (company.canonical_cn, *company.aliases):
            key = name.strip()
            if not key:
                continue
            previous = index.get(key)
            if previous and previous.canonical_cn != company.canonical_cn:
                raise ValueError(
                    f"中企国别登记表别名冲突: {key!r} → "
                    f"{previous.canonical_cn} / {company.canonical_cn}"
                )
            index[key] = company
    return index


def resolve_entity(verbatim: str) -> EntityResolution:
    """归一实体并给出三态国别结论；未命中不得作为已核实中企发布。"""
    name = (verbatim or "").strip()
    if not name or name == "Unknown":
        return EntityResolution("Unknown", "", UNMATCHED_NEEDS_REVIEW, "unmatched")

    registry_company = _nationality_index().get(name)
    if registry_company is not None:
        status = VERIFIED_CHINESE if registry_company.is_chinese else VERIFIED_NON_CHINESE
        parent = (registry_company.parent_cn or "").strip()
        if status == VERIFIED_CHINESE and parent:
            return EntityResolution(parent, name, status, "registry")
        return EntityResolution(registry_company.canonical_cn, "", status, "registry")

    parent, sub, matched = _normalize_from_hierarchy(name)
    if matched:
        return EntityResolution(parent, sub, VERIFIED_CHINESE, "hierarchy")

    logger.warning("entity_normalize 未命中（转人工复核）: %s", name)
    return EntityResolution(name, "", UNMATCHED_NEEDS_REVIEW, "unmatched")


def _normalize_from_hierarchy(name: str) -> tuple[str, str, bool]:
    """只使用中企层级表归一名称，并显式返回是否命中。"""
    idx = _hierarchy_index_v2()

    if name in idx:
        stored_canon, stored_sub = idx[name]
        if stored_sub:
            return stored_canon, name, True
        if name == stored_canon:
            return stored_canon, "", True
        return stored_canon, name, True

    stripped_input = _strip_suffix(name)
    if stripped_input != name and stripped_input in _stripped_index_cache:
        canon, _sub_key = _stripped_index_cache[stripped_input]
        return canon, name, True
    return name, "", False


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
    resolution = resolve_entity(verbatim)
    return resolution.implementing, resolution.subsidiary
