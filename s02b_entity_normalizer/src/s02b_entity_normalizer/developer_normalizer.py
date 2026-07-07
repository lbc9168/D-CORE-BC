"""developer_normalizer.py — 业主(project_events.project_developer)归一化。

流程：整值垃圾占位统一 Unknown → 拆串取部件（带频次）→ 复用 entity_normalizer 三信号
候选 + LLM 批量确认 + Union-Find 映射 → 保守重建每个业主串（部件有变化才改）。
developer_en 不在此处理——由 en_backfill 在归一后统一重算。

用法（s02b_entity_normalizer/ 目录下）：
    uv run normalize_developers              # 预览：只看候选与 would-change，不写库（等同 --preview）
    uv run normalize_developers --preview    # 同上，显式 preview
    uv run normalize_developers --apply      # LLM 确认后写库（.bak 先备份）
    uv run normalize_developers --db PATH --out CSV
"""
import argparse
import csv
import logging
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

from s02b_entity_normalizer.company_lookup import LookupIndex, build_index, collect_misses, lookup
from s02b_entity_normalizer.db_backup import backup_db
from s02b_entity_normalizer.entity_normalizer import (
    BATCH_SIZE, DEFAULT_DB, _build_mapping, _find_candidates,
)

logger = logging.getLogger(__name__)
_S02B_ROOT = Path(__file__).resolve().parents[2]
_SUPPORTING_DOCS = _S02B_ROOT.parent / "supporting_docs"
load_dotenv(_S02B_ROOT / ".env")
load_dotenv()

DEV_SEP_RE = re.compile(r"[;；、，,/]+")
# 整值等于这些（大小写无关）→ 统一 'Unknown'（"业主"是占位词不是公司名）
DEV_JUNK_VALUES = {"unknown", "未知", "不详", "不明", "暂无", "n/a", "na", "-", "业主", "业主方"}


# 保守归一（用户拍板 2026-07-04）：业主字段保留产权层级——联合体/连字符串是多主体载体，禁止参与合并
_JV_MARKERS = ("联合体", "-", "－", "–")


def filter_candidates_conservative(candidates: List[Dict]) -> List[Dict]:
    """剔除任一侧含联合体/连字符标记的候选对（折叠 JV 串会丢合资伙伴）。"""
    return [c for c in candidates
            if not any(m in c["name_a"] or m in c["name_b"] for m in _JV_MARKERS)]


def _build_dev_confirm_prompt(batch: List[Dict]) -> str:
    """业主专用 LLM 确认提示词：仅同一家法人的写法变体才合并，母子公司/联合体一律不合并。"""
    pairs_text = "\n".join(
        f"{p['id']}. 「{p['name_a']}」(出现{p['count_a']}次)"
        f" vs 「{p['name_b']}」(出现{p['count_b']}次)"
        f"  [信号: {p['signals']}]"
        for p in batch
    )
    return f"""你是一个专注于中国企业的数据库清洗专家。
下面列出"一带一路"可再生能源项目数据库中候选重复的**项目业主**名称对。请判断每对是否为**同一家法人主体的不同写法**，并选定规范名。

【判断标准——业主字段务必保守】
- 仅当两个名称是同一家法人的全称/简称/繁简体/笔误变体时才算同一（如"中国电力建设集团有限公司"<->"中国电建"）。
- **母公司与子公司/分公司/事业部/院所一律不合并**（如"中国电建" vs "中国电建海投公司"是不同法人，业主字段需保留持股主体层级），选 is_same=false。
- 联合体、多主体串一律不合并；业务实质不同的企业不合并。
- 规范名选择：优先行业通用的正式简称。

【候选对】
{pairs_text}

请以 JSON 数组返回，每个元素格式：
{{"id": <编号>, "is_same": <true|false>, "canonical_name": "<规范名；is_same=false时留空>"}}

只返回 JSON，不要有其他文字。"""


def _llm_confirm_batch_dev(client, batch: List[Dict]) -> List[Dict]:
    """Gemini 批量确认（业主保守口径提示词）；调用/解析机制同 entity_normalizer，仅 prompt 不同。"""
    import json as _json
    import time as _time
    from google.genai import types as _types
    prompt = _build_dev_confirm_prompt(batch)
    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite", contents=prompt,
                config=_types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
            )
            raw = resp.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            results = _json.loads(raw)
            result_map = {r["id"]: r for r in results}
            for p in batch:
                info = result_map.get(p["id"], {})
                p["is_same"] = bool(info.get("is_same", False))
                p["canonical_name"] = str(info.get("canonical_name", "")).strip()
            return batch
        except Exception as e:  # API/解析异常型号繁多：统一记录并退避重试（与 entity_normalizer 同模式）
            logger.error("业主确认 LLM 调用失败 attempt=%d/3: %s", attempt + 1, e)
            _time.sleep(2 ** attempt)
    for p in batch:
        p["is_same"] = False
        p["canonical_name"] = ""
    return batch


def split_developer(value) -> List[str]:
    """按分隔符拆业主串为公司名列表（去空白、去空段）。"""
    s = str(value or "").strip()
    if not s:
        return []
    return [p.strip() for p in DEV_SEP_RE.split(s) if p.strip()]


def unify_junk(value) -> Optional[str]:
    """整值为垃圾占位（业主/未知/N/A 等）→ 'Unknown'；否则 None（不改）。"""
    s = str(value or "").strip()
    return "Unknown" if s.lower() in DEV_JUNK_VALUES else None


def rebuild(value: str, part_mapping: Dict[str, str]) -> str:
    """按部件映射重建业主串：任一部件命中映射才改写（'; ' 拼回）；否则原样返回（含分隔符风格）。"""
    parts = split_developer(value)
    if not parts:
        return value
    mapped = [part_mapping.get(p, p) for p in parts]
    if mapped == parts:
        return value
    return "; ".join(mapped) if len(mapped) > 1 else mapped[0]


def plan_value_changes(conn, part_mapping: Dict[str, str]) -> List[Tuple[str, str]]:
    """扫描 distinct 业主值 → (old, new) 变更对（垃圾值统一 + 部件映射重建，只列有变化的）。"""
    rows = conn.execute(
        "SELECT project_developer FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    pairs: List[Tuple[str, str]] = []
    for (v,) in rows:
        new = unify_junk(v) or rebuild(v, part_mapping)
        if new != v:
            pairs.append((v, new))
    return pairs


def apply_value_changes(db_path: str, pairs: List[Tuple[str, str]]) -> int:
    """先备份 .bak，再事务 executemany 改写 project_developer；返回受影响行数。"""
    if not pairs:
        return 0
    bak = backup_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.executemany(
            "UPDATE project_events SET project_developer = ? WHERE project_developer = ?",
            [(new, old) for old, new in pairs],
        )
        conn.commit()
        logger.info("project_developer 归一落库 %d 行（%d 个不重复值）", cur.rowcount, len(pairs))
        return cur.rowcount
    except sqlite3.Error as e:
        logger.error("落库失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def _collect_parts(conn) -> List[Tuple[str, int]]:
    """拆串统计部件频次（按值出现行数加权），排除 Unknown 与纯 ASCII 部件。"""
    rows = conn.execute(
        "SELECT project_developer, COUNT(*) FROM project_events "
        "WHERE project_developer IS NOT NULL AND TRIM(project_developer) <> '' "
        "GROUP BY project_developer"
    ).fetchall()
    counter: Counter = Counter()
    for v, n in rows:
        if unify_junk(v):
            continue
        for p in split_developer(v):
            if p.lower() in DEV_JUNK_VALUES or not re.search(r"[一-鿿]", p):
                continue
            counter[p] += n
    return counter.most_common()


def registry_part_mapping(parts: List[Tuple[str, int]], index: LookupIndex) -> Dict[str, str]:
    """registry/hierarchy 确定性映射：命中且规范名≠原文的部件才进映射（已是规范名的不列入）。"""
    mapping: Dict[str, str] = {}
    for name, _count in parts:
        r = lookup(name, index)
        if r.matched and r.canonical_cn != name:
            mapping[name] = r.canonical_cn
    return mapping


def _candidates_for_llm(parts: List[Tuple[str, int]], index: LookupIndex) -> List[Dict]:
    """三信号候选对，仅在 registry/hierarchy 未命中的部件之间生成（命中部件不得再配对）。"""
    unmatched = [(n, c) for n, c in parts if not lookup(n, index).matched]
    candidates = _find_candidates(unmatched)
    return filter_candidates_conservative(candidates)


def write_suggestions_csv(confirmed_pairs: List[Dict], out_path: str) -> None:
    """把 LLM 确认为同一法人的候选对写成登记册补录建议 CSV（供人工复核入册，不进映射不写库）。"""
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["part_a", "part_b", "freq_a", "freq_b", "verdict", "llm_reason"])
        for p in confirmed_pairs:
            if not p.get("is_same"):
                continue
            w.writerow([p["name_a"], p["name_b"], p["count_a"], p["count_b"],
                        p.get("canonical_name", ""), p.get("signals", "")])


def _write_misses_csv(misses: List[str], out_path: str) -> None:
    """把 collect_misses 收集到的未命中部件写成 miss CSV（供后续种子 campaign 参考）。"""
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["part"])
        for m in misses:
            w.writerow([m])


def run(db_path: Optional[str] = None, preview: bool = False, apply: bool = False,
        use_registry: bool = True) -> None:
    """主流程：registry 前置确定性映射 →（apply=True 时）LLM 对未命中部件确认 → would-change CSV →（apply=True 时）落库。

    CLI 裸跑（无 --apply）等同 preview=True：不调 LLM、不写库。
    程序调用：apply=True 才触发 LLM 与写库路径（preview=False 且 apply=False 时也只预览）。
    use_registry=True（默认）：registry/hierarchy 命中直接映射，LLM 只处理未命中部件且确认结果
    降级为补录建议 CSV（不进 part_mapping、不写库）。use_registry=False：现行为 100% 不变（对照组）。
    """
    if db_path is None:
        parser = argparse.ArgumentParser(description="业主(project_developer)归一化")
        parser.add_argument("--preview", action="store_true", help="只看候选与 would-change，不写库（默认行为）")
        parser.add_argument("--apply", action="store_true", help="确认后落库（默认等同 --preview，只看不写）")
        parser.add_argument("--db", type=str, default=None)
        parser.add_argument("--out", type=str, default=None, help="would-change CSV 路径")
        args = parser.parse_args()
        # 裸跑（无 --apply）= preview；--preview 或无 --apply 均为 preview
        preview = not args.apply or args.preview
        apply = args.apply and not args.preview
        db_path = args.db
        out_csv = args.out
    else:
        # 程序调用：apply=True 且 preview=False 才写库
        if preview:
            apply = False
        out_csv = None

    if db_path is None:
        db_path = str(DEFAULT_DB)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = out_csv or str(Path(db_path).parent / f"developer_normalize_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    index: Optional[LookupIndex] = None
    conn = sqlite3.connect(db_path)
    try:
        parts = _collect_parts(conn)
        print(f">>> 业主部件（中文、去垃圾）：{len(parts)} 个")

        part_mapping: Dict[str, str] = {}
        if use_registry:
            index = build_index()
            part_mapping.update(registry_part_mapping(parts, index))
            print(f">>> registry/hierarchy 确定性映射 {len(part_mapping)} 条")
            candidates = _candidates_for_llm(parts, index)
        else:
            candidates = filter_candidates_conservative(_find_candidates(parts))
        print(f">>> 三信号候选对（保守预过滤后）：{len(candidates)}")

        if not apply:
            pairs = plan_value_changes(conn, part_mapping)
            _write_csv(out_csv, candidates, pairs, part_mapping or None)
            print(f">>> [preview] would-change {len(pairs)} 个值；候选对已写 {out_csv}（LLM 未调用）")
            return

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未找到 GEMINI_API_KEY（.env）")
        from google import genai
        client = genai.Client(api_key=api_key)
        confirmed = []
        for i in range(0, len(candidates), BATCH_SIZE):
            batch = candidates[i:i + BATCH_SIZE]
            print(f">>> LLM 确认批次 {i // BATCH_SIZE + 1}/{(len(candidates) - 1) // BATCH_SIZE + 1}")
            confirmed.extend(_llm_confirm_batch_dev(client, batch))
            time.sleep(0.5)

        if use_registry:
            suggestions_path = str(_SUPPORTING_DOCS / f"developer_registry_suggestions_{ts}.csv")
            write_suggestions_csv(confirmed, suggestions_path)
            print(f">>> LLM 确认结果降级为登记册补录建议（未合并、未写库）：{suggestions_path}")
            misses_path = str(_SUPPORTING_DOCS / f"developer_norm_misses_{ts}.csv")
            _write_misses_csv(collect_misses(index), misses_path)
            print(f">>> miss 明细：{misses_path}")
        else:
            part_mapping.update(_build_mapping(parts, confirmed))
            print(f">>> 部件映射 {len(part_mapping)} 条")

        pairs = plan_value_changes(conn, part_mapping)
        _write_csv(out_csv, candidates, pairs, part_mapping or None)
        print(f">>> would-change {len(pairs)} 个不重复值，明细 {out_csv}")
    finally:
        conn.close()

    if apply and pairs:
        n = apply_value_changes(db_path, pairs)
        print(f">>> 已落库 {n} 行。注意：developer_en 需随后跑 backfill_entity_en 重算。")


def _write_csv(path: str, candidates: List[dict], pairs: List[Tuple[str, str]],
               mapping: Optional[Dict[str, str]] = None) -> None:
    """把候选对 / 部件映射 / would-change 值三段写入一个报告 CSV。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["kind", "a", "b", "note"])
        for c in candidates:
            w.writerow(["candidate", c["name_a"], c["name_b"], c["signals"]])
        for alias, canon in sorted((mapping or {}).items()):
            w.writerow(["part_mapping", alias, canon, ""])
        for old, new in pairs:
            w.writerow(["value_change", old, new, ""])


if __name__ == "__main__":
    run()
