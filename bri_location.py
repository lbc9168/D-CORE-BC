"""bri_location.py — 地名归一化共享逻辑（单一可信源）

为音译地名变体（达瓦德密 = 达旺德米）调校的三信号候选筛查 + LLM 批量确认 + Union-Find 规范名映射。
两个消费端：
  - s02a duplicate_merger：去重前对批内 location 做 in-memory 归一（写 project["location_norm"]）
  - s02b location_normalizer：对 canonical_projects.location 做落库归一（写 location_norm 列）

模块顶层不依赖 google-genai：client 以鸭子类型传入（任何带 .models.generate_content 的对象），
LLM 失败保守不合并并 logging.error 记录。
"""
import json
import logging
import time
from typing import Dict, List, Tuple

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# 信号1：token_sort_ratio 阈值（地名短、音译差异大，阈值低于实体归一的 80）
LOC_TOKEN_THRESHOLD    = 70
# 信号2：partial_ratio 阈值（覆盖「信德」vs「信德省」等行政后缀差异）
LOC_PARTIAL_THRESHOLD  = 85
# 信号3：等长逐位相同比例下限（达瓦德密 vs 达旺德米 = 0.5）
LOC_POSITIONAL_RATIO   = 0.5
# 信号3 仅用于 ≤6 字短地名（长地名交给信号1/2）
LOC_POSITIONAL_MAX_LEN = 6
# 双方最短长度（过滤单字噪声）
LOC_MIN_LEN            = 2

LLM_BATCH_SIZE = 25
LLM_MODEL      = "gemini-2.5-flash-lite"


def _positional_variant(a: str, b: str) -> bool:
    """信号3：等长、首字相同、逐位相同比例达标的逐字音译变体检测（token/partial 对这类串均失效）。"""
    if len(a) != len(b) or not a or a[0] != b[0] or len(a) > LOC_POSITIONAL_MAX_LEN:
        return False
    same = sum(1 for x, y in zip(a, b) if x == y)
    return same / len(a) >= LOC_POSITIONAL_RATIO


def find_location_candidates(names_with_counts: List[Tuple[str, int]]) -> List[Dict]:
    """三信号扫描（同一国家内的）地名对，返回候选对列表（结构同 entity_normalizer 候选）。"""
    names = [n for n, _ in names_with_counts]
    count_map = dict(names_with_counts)
    candidates: List[Dict] = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if len(a) < LOC_MIN_LEN or len(b) < LOC_MIN_LEN:
                continue
            signals: List[str] = []
            score_token = fuzz.token_sort_ratio(a, b)
            score_partial = fuzz.partial_ratio(a, b)
            if score_token >= LOC_TOKEN_THRESHOLD:
                signals.append(f"token_sort={score_token:.0f}")
            if score_partial >= LOC_PARTIAL_THRESHOLD:
                signals.append(f"partial={score_partial:.0f}")
            # 信号3 仅在信号1/2 均未触发时运行，专门捕获音译逐字变体
            if not signals and _positional_variant(a, b):
                signals.append("positional")
            if signals:
                candidates.append({
                    "id": len(candidates),
                    "name_a": a, "count_a": count_map[a],
                    "name_b": b, "count_b": count_map[b],
                    "signals": ", ".join(signals),
                })
    return candidates


def llm_confirm_locations(client, batch: List[Dict], country: str = "") -> List[Dict]:
    """LLM 批量判断候选地名对是否同一地点的不同写法；3 次重试后保守置 is_same=False。"""
    pairs_text = "\n".join(
        f"{p['id']}. 「{p['name_a']}」(出现{p['count_a']}次)"
        f" vs 「{p['name_b']}」(出现{p['count_b']}次)  [信号: {p['signals']}]"
        for p in batch
    )
    prompt = f"""你是地理数据清洗专家。下面是「一带一路」能源项目数据库中来自同一国家（{country or '未知'}）的候选重复地点名称对，请判断每对是否指同一个地点的不同写法。

【判断标准】
- 同一地点的典型情况：音译差异（如「达瓦德密」=「达旺德米」）、简繁体差异、行政后缀有无（如「信德」=「信德省」）、明显错别字
- 不是同一地点：同一省份内的两个不同城市/县/地区，即使名称相似也不得合并；拿不准时按"不是"处理
- 规范名选择：选更常见或更标准的写法（出现次数多者优先）

【候选对】
{pairs_text}

请以 JSON 数组返回，每个元素格式：
{{"id": <编号>, "is_same": <true|false>, "canonical_name": "<规范地名；is_same=false 时留空>"}}
只返回 JSON，不要有其他文字。"""

    for attempt in range(3):
        try:
            resp = client.models.generate_content(model=LLM_MODEL, contents=prompt)
            raw = (resp.text or "").strip()
            # 有时模型返回 ```json ... ``` 包裹，需去除
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            results = json.loads(raw)
            result_map = {r["id"]: r for r in results}
            for p in batch:
                info = result_map.get(p["id"], {})
                p["is_same"] = bool(info.get("is_same", False))
                p["canonical_name"] = (info.get("canonical_name") or "").strip()
            return batch
        except Exception as e:  # LLM/网络/解析异常统一退避重试；耗尽后保守不合并
            logger.error("地名 LLM 确认失败 (attempt %d/3, country=%s): %s", attempt + 1, country, e)
            time.sleep(2 ** attempt)

    # 全部重试耗尽：保守不合并
    for p in batch:
        p["is_same"] = False
        p["canonical_name"] = ""
    return batch


def build_union_mapping(
    names_with_counts: List[Tuple[str, int]],
    confirmed_pairs: List[Dict],
) -> Dict[str, str]:
    """Union-Find 合并确认对并选规范名，返回 {别名 → 规范名}（算法同 entity_normalizer._build_mapping）。"""
    all_names = {n for n, _ in names_with_counts}

    parent: Dict[str, str] = {n: n for n in all_names}
    canon_of: Dict[str, str] = {}  # root → 该 union 的规范名

    def find(x: str) -> str:
        """路径压缩查找根节点。"""
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str, canon: str) -> None:
        """合并两个节点到规范名所在的根，并记录规范名。"""
        for name in (a, b, canon):
            if name not in parent:
                parent[name] = name
        ra, rb, rc = find(a), find(b), find(canon)
        parent[ra] = rc
        parent[rb] = rc
        canon_of[rc] = canon

    for p in confirmed_pairs:
        if p.get("is_same") and p.get("canonical_name"):
            union(p["name_a"], p["name_b"], p["canonical_name"])

    mapping: Dict[str, str] = {}
    for name in all_names:
        canon = canon_of.get(find(name), find(name))
        if canon != name:
            mapping[name] = canon
    return mapping


def build_location_mapping(
    locations_by_country: Dict[str, List[Tuple[str, int]]],
    client,
) -> Dict[str, Dict[str, str]]:
    """按国家独立归一地名（国家间互不串改）。

    输入 {国家key: [(地名, 次数)]}，输出 {国家key: {别名 → 规范名}}；
    国家key 由调用方决定（s02a 用中文国名，s02b CLI 用 country_iso）。
    """
    result: Dict[str, Dict[str, str]] = {}
    for country, names_with_counts in locations_by_country.items():
        # 单一地名无需归一
        if len(names_with_counts) < 2:
            continue
        candidates = find_location_candidates(names_with_counts)
        if not candidates:
            continue
        # 分批调用 LLM 确认
        confirmed: List[Dict] = []
        for i in range(0, len(candidates), LLM_BATCH_SIZE):
            confirmed.extend(
                llm_confirm_locations(client, candidates[i:i + LLM_BATCH_SIZE], country)
            )
        mapping = build_union_mapping(names_with_counts, confirmed)
        if mapping:
            result[country] = mapping
    return result
