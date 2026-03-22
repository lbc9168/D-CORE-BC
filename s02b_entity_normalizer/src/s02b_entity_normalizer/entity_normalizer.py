"""entity_normalizer.py — 中国实体名称归一化工具

三信号候选筛查 + LLM 批量确认：
  Signal 1: token_sort_ratio >= 75%（词序无关相似度）
  Signal 2: partial_ratio >= 80%（子串包含，覆盖"子公司简称"情况）
  Signal 3: is_subsequence(短名, 长名) AND len(短名) <= 8
            （缩写检测，如 中国电建 <-> 中国电力建设集团）

Special:  Unknown / 未知 / 不详 等变体统一归一为 "Unknown"

使用方法（在 s02b_entity_normalizer/ 目录下）：
    uv run normalize_entities            # 正式运行，写入数据库
    uv run normalize_entities --preview  # 仅预览候选对，不写入
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Windows GBK 控制台下强制 UTF-8 输出，避免中文乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from dotenv import load_dotenv
from google import genai
from google.genai import types
from rapidfuzz import fuzz

_S02B_ROOT = Path(__file__).resolve().parents[2]  # s02b_entity_normalizer/

load_dotenv(_S02B_ROOT / ".env")
load_dotenv()

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_DB = _S02B_ROOT.parent / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"

FUZZY_TOKEN_THRESHOLD   = 80   # Signal 1: token_sort_ratio 阈值
FUZZY_PARTIAL_THRESHOLD = 90   # Signal 2: partial_ratio 阈值
PARTIAL_MAX_LEN_RATIO   = 2.5  # Signal 2: 长名/短名长度比上限（排除父公司 vs 远房子公司）
SUBSEQ_MIN_LEN          = 4    # Signal 3: 短名最少字符数（避免"中国"等过短词误触发）
SUBSEQ_MAX_LEN          = 8    # Signal 3: 短名最多字符数
SUBSEQ_MIN_LEN_DIFF     = 2    # Signal 3: 长名至少比短名多几个字符
SUBSEQ_MAX_LEN_RATIO    = 0.68 # Signal 3: 短名/长名长度比上限（排除"电建山西" vs "电建四川"类）
SUBSEQ_PREFIX_MATCH     = 3    # Signal 3: 短名前N字必须与长名前N字相同（防止乱序误匹配）
MIN_BOTH_LEN            = 3    # 双方名称最小字符数（过滤"中国"等无意义短名）

GEMINI_MODEL = "gemini-2.5-flash-lite"
BATCH_SIZE   = 25   # 每次 LLM 调用处理的候选对数

# 统一归一为 "Unknown" 的变体集合（比较时忽略大小写）
UNKNOWN_VARIANTS = {"unknown", "未知", "不详", "不明", "暂无", "n/a", "na", "-"}


# ── Signal 3：子序列检测 ───────────────────────────────────────────────────────

def _is_subsequence(short: str, long_: str) -> bool:
    """检测 short 所有字符是否按序出现在 long_ 中（用于识别缩写关系）。"""
    it = iter(long_)
    return all(c in it for c in short)


# ── 候选对筛查 ─────────────────────────────────────────────────────────────────

def _find_candidates(names_with_counts: List[Tuple[str, int]]) -> List[Dict]:
    """
    三信号扫描所有名称对，返回满足至少一个信号的候选对列表。

    每个候选对格式：
        {id, name_a, count_a, name_b, count_b, signals, score_token, score_partial}
    """
    names     = [n for n, _ in names_with_counts]
    count_map = {n: c for n, c in names_with_counts}

    candidates: List[Dict] = []

    for i, name_a in enumerate(names):
        for j in range(i + 1, len(names)):
            name_b = names[j]

            # 双方至少 MIN_BOTH_LEN 字，过滤「中国」等无意义短名
            if len(name_a) < MIN_BOTH_LEN or len(name_b) < MIN_BOTH_LEN:
                continue

            signals      : List[str] = []
            score_token   = fuzz.token_sort_ratio(name_a, name_b)
            score_partial = fuzz.partial_ratio(name_a, name_b)

            # Signal 1: token_sort_ratio
            if score_token >= FUZZY_TOKEN_THRESHOLD:
                signals.append(f"token_sort={score_token}")

            # Signal 2: partial_ratio + 长度比约束（排除父公司 vs 远房子公司）
            if score_partial >= FUZZY_PARTIAL_THRESHOLD:
                short_p = name_a if len(name_a) <= len(name_b) else name_b
                long_p  = name_b if len(name_a) <= len(name_b) else name_a
                if len(long_p) / len(short_p) <= PARTIAL_MAX_LEN_RATIO:
                    signals.append(f"partial={score_partial}")

            # Signal 3：子序列缩写检测（仅在前两个信号均未触发时运行）
            # 约束：短名 4~8 字；长名至少多 2 字；长度比 <= 0.68；前缀前 3 字一致
            # 目的：捕获"中国电建 <-> 中国电力建设集团"类，排除"电建山西 vs 电建四川"类
            if not signals:
                short, long_ = (
                    (name_a, name_b) if len(name_a) <= len(name_b)
                    else (name_b, name_a)
                )
                len_s, len_l = len(short), len(long_)
                prefix_n = min(SUBSEQ_PREFIX_MATCH, len_s - 1)
                if (
                    SUBSEQ_MIN_LEN <= len_s <= SUBSEQ_MAX_LEN
                    and (len_l - len_s) >= SUBSEQ_MIN_LEN_DIFF
                    and (len_s / len_l) <= SUBSEQ_MAX_LEN_RATIO
                    and short[:prefix_n] == long_[:prefix_n]
                    and _is_subsequence(short, long_)
                ):
                    signals.append("subsequence")

            if signals:
                candidates.append({
                    "id"           : len(candidates),
                    "name_a"       : name_a,
                    "count_a"      : count_map[name_a],
                    "name_b"       : name_b,
                    "count_b"      : count_map[name_b],
                    "signals"      : ", ".join(signals),
                    "score_token"  : score_token,
                    "score_partial": score_partial,
                })

    return candidates


# ── LLM 批量确认 ───────────────────────────────────────────────────────────────

def _llm_confirm_batch(client: genai.Client, batch: List[Dict]) -> List[Dict]:
    """
    Gemini 批量判断候选对是否为同一公司，并选定规范名称。
    返回同一 batch 列表，每项追加 is_same / canonical_name 字段。
    """
    pairs_text = "\n".join(
        f"{p['id']}. 「{p['name_a']}」(出现{p['count_a']}次)"
        f" vs 「{p['name_b']}」(出现{p['count_b']}次)"
        f"  [信号: {p['signals']}]"
        for p in batch
    )

    prompt = f"""你是一个专注于中国企业的数据库清洗专家。
下面列出若干对候选重复企业名称（来自"一带一路"可再生能源项目数据库），请判断每对是否指同一家中国企业，并选定规范名称。

【判断标准】
- 同一公司的典型情况：全称与缩写（如"中国电力建设集团"<->"中国电建"）、名称微小差异（如繁简体、错字）
- 不是同一公司：业务实质不同的企业，即使名称相似也不合并
- 规范名选择：优先选用行业内更广泛认可的正式名称（若"中国电建"比"中国电力建设集团"更通用，则选前者）

【候选对】
{pairs_text}

请以 JSON 数组返回，每个元素格式：
{{"id": <编号>, "is_same": <true|false>, "canonical_name": "<规范名；is_same=false时留空>"}}

只返回 JSON，不要有其他文字。"""

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
            raw = resp.text.strip()
            # 有时模型返回 ```json ... ``` 包裹
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            results = json.loads(raw)
            result_map = {r["id"]: r for r in results}
            for p in batch:
                info = result_map.get(p["id"], {})
                p["is_same"]       = bool(info.get("is_same", False))
                p["canonical_name"] = info.get("canonical_name", "").strip()
            return batch
        except Exception as e:
            print(f"    [警告] LLM 调用失败 (attempt {attempt + 1}/3): {e}")
            time.sleep(2 ** attempt)

    # 全部失败：保守处理，不合并
    for p in batch:
        p["is_same"]        = False
        p["canonical_name"] = ""
    return batch


# ── Union-Find 构建规范名映射 ──────────────────────────────────────────────────

def _build_mapping(
    names_with_counts: List[Tuple[str, int]],
    confirmed_pairs  : List[Dict],
) -> Dict[str, str]:
    """
    Union-Find 合并已确认的同一公司名称集合，选定规范名。
    返回 {alias → canonical_name}（不含自身→自身的条目）。
    """
    all_names = {n for n, _ in names_with_counts}

    parent: Dict[str, str] = {n: n for n in all_names}
    canon_of: Dict[str, str] = {}   # root → 该 union 的规范名

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: str, b: str, canon: str) -> None:
        # 确保 canon 在 parent 中（可能不在原始名单）
        if canon not in parent:
            parent[canon] = canon
        for name in (a, b, canon):
            if name not in parent:
                parent[name] = name

        ra, rb, rc = find(a), find(b), find(canon)
        # 将 rc 设为代表根
        parent[ra] = rc
        parent[rb] = rc
        canon_of[rc] = canon

    for p in confirmed_pairs:
        if p.get("is_same") and p.get("canonical_name"):
            union(p["name_a"], p["name_b"], p["canonical_name"])

    mapping: Dict[str, str] = {}
    for name in all_names:
        root  = find(name)
        canon = canon_of.get(root, root)
        if canon != name:
            mapping[name] = canon

    return mapping


# ── 数据库更新 ─────────────────────────────────────────────────────────────────

def _update_db(db_path: str, mapping: Dict[str, str]) -> Tuple[int, int]:
    """
    将 chinese_entities 表中的别名替换为规范名。
    同时更新 implementing_entity 和 subsidiary_name 两列。
    返回 (更新的 implementing_entity 行数, 更新的 subsidiary_name 行数)。
    """
    if not mapping:
        return 0, 0

    conn = sqlite3.connect(db_path)
    n_impl = n_sub = 0

    for alias, canon in mapping.items():
        r = conn.execute(
            "UPDATE chinese_entities SET implementing_entity = ? "
            "WHERE implementing_entity = ?",
            (canon, alias),
        )
        n_impl += r.rowcount

        r = conn.execute(
            "UPDATE chinese_entities SET subsidiary_name = ? "
            "WHERE subsidiary_name = ?",
            (canon, alias),
        )
        n_sub += r.rowcount

    conn.commit()
    conn.close()
    return n_impl, n_sub


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(db_path: Optional[str] = None, preview: bool = False) -> None:

    # ── CLI 参数解析 ──────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="实体名称归一化工具（三信号 + LLM 确认）"
    )
    parser.add_argument(
        "--preview", action="store_true",
        help="仅预览候选对，不写入数据库"
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help=f"数据库路径（默认：{DEFAULT_DB}）"
    )
    args, _ = parser.parse_known_args()

    if args.preview:
        preview = True
    if args.db:
        db_path = args.db

    if db_path is None:
        db_path = str(DEFAULT_DB)

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key and not preview:
        raise RuntimeError("未找到 GEMINI_API_KEY，请在 .env 文件中配置")

    print(f"\n{'='*50}")
    print(f"  实体名称归一化工具{'（预览模式）' if preview else ''}")
    print(f"{'='*50}")
    print(f"  数据库: {db_path}")

    # ── Step 0：统一 Unknown 变体 ──────────────────────────────────────────────
    print("\n[0] 统一 Unknown 变体...")
    if not preview:
        conn = sqlite3.connect(db_path)
        n_unknown = 0
        for variant in UNKNOWN_VARIANTS:
            if variant.lower() == "unknown":
                continue
            r = conn.execute(
                "UPDATE chinese_entities SET implementing_entity = 'Unknown' "
                "WHERE lower(implementing_entity) = ?",
                (variant.lower(),),
            )
            n_unknown += r.rowcount
        conn.commit()
        conn.close()
        print(f"  统一 {n_unknown} 条 Unknown 变体 → 'Unknown'")
    else:
        conn = sqlite3.connect(db_path)
        n_unknown = sum(
            conn.execute(
                "SELECT COUNT(*) FROM chinese_entities "
                "WHERE lower(implementing_entity) = ?",
                (v.lower(),),
            ).fetchone()[0]
            for v in UNKNOWN_VARIANTS if v.lower() != "unknown"
        )
        conn.close()
        print(f"  [预览] 将统一 {n_unknown} 条 Unknown 变体 → 'Unknown'")

    # ── Step 1：加载实体名 ─────────────────────────────────────────────────────
    print("\n[1] 加载实体名称...")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT implementing_entity, COUNT(*) as n "
        "FROM chinese_entities "
        "WHERE implementing_entity IS NOT NULL AND implementing_entity != '' "
        "  AND lower(implementing_entity) != 'unknown' "
        "GROUP BY implementing_entity ORDER BY n DESC"
    ).fetchall()
    conn.close()

    names_with_counts: List[Tuple[str, int]] = [(r[0], r[1]) for r in rows]
    print(f"  加载 {len(names_with_counts)} 个不重复实体名（排除 Unknown）")

    # ── Step 2：三信号候选筛查 ─────────────────────────────────────────────────
    print(
        f"\n[2] 三信号候选筛查（token≥{FUZZY_TOKEN_THRESHOLD} / "
        f"partial≥{FUZZY_PARTIAL_THRESHOLD} / subsequence≤{SUBSEQ_MAX_LEN}字）..."
    )
    candidates = _find_candidates(names_with_counts)
    print(f"  发现 {len(candidates)} 对候选重复对")

    if not candidates:
        print("  无候选对，无需归一化。")
        return

    print("\n  候选对列表：")
    for p in candidates:
        print(
            f"    [{p['id']:>3}] 「{p['name_a']}」({p['count_a']}) <-> "
            f"「{p['name_b']}」({p['count_b']})  [{p['signals']}]"
        )

    if preview:
        print("\n[预览模式] 跳过 LLM 确认和数据库更新。")
        return

    # ── Step 3：LLM 批量确认 ───────────────────────────────────────────────────
    client = genai.Client(api_key=api_key)
    print(f"\n[3] LLM 批量确认（batch_size={BATCH_SIZE}）...")

    confirmed: List[Dict] = []
    total_batches = (len(candidates) - 1) // BATCH_SIZE + 1
    for i in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[i: i + BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f"  批次 {batch_no}/{total_batches}：{len(batch)} 对...")
        confirmed.extend(_llm_confirm_batch(client, batch))
        time.sleep(0.5)

    same_count = sum(1 for p in confirmed if p.get("is_same"))
    print(f"\n  LLM 结果：{same_count} 对确认为同一公司，{len(confirmed) - same_count} 对排除")
    for p in confirmed:
        if p.get("is_same"):
            print(
                f"    ✓ 「{p['name_a']}」<->「{p['name_b']}」"
                f" → 规范名：「{p['canonical_name']}」"
            )

    # ── Step 4：构建规范名映射 ─────────────────────────────────────────────────
    print("\n[4] 构建规范名映射（Union-Find）...")
    mapping = _build_mapping(names_with_counts, confirmed)
    print(f"  生成 {len(mapping)} 条别名 → 规范名映射")
    for alias, canon in sorted(mapping.items()):
        print(f"    {alias}  →  {canon}")

    if not mapping:
        print("  无需更新数据库。")
        return

    # ── Step 5：写入数据库 ─────────────────────────────────────────────────────
    print("\n[5] 更新数据库...")
    n_impl, n_sub = _update_db(db_path, mapping)
    print(f"  ✓ implementing_entity 更新：{n_impl} 行")
    print(f"  ✓ subsidiary_name     更新：{n_sub} 行")

    print(f"\n{'='*50}")
    print(f"  ✅ 实体名称归一化完成")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
