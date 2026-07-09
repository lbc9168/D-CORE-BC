"""en_translate_residual.py — 2B-6 剩余名称英文回填：2B-4 之后 *_en 仍空的长尾名 → 翻译或直通。

口径：只处理「会随非排除项目出现在网页/下载」的名称（挂 excluded=0 项目、en 空或含中文）。
含中文名走 LLM 翻译；纯 ASCII 名（已是英文，只是 _en 空）走直通复制，不调 LLM。
逐部件四层解析：registry canonical_en > 主缓存 en_abbr/en > 残余缓存 > LLM（miss 才调，成功写回缓存）。
落库为 fill-if-null 语义（只填 en 空/含中文的行，绝不覆盖有效英文名）；翻译失败/结果含中文不缓存不落库。
残余缓存独立于人工核实过的 company_name_cache.json（source=llm_auto，供季度 campaign 升级进 registry）。

用法：uv run translate_residual_en [--db 主库] [--cache PATH] [--residual PATH] [--apply] [--out CSV]
    裸跑=preview（只报缺口计数，不调 LLM、不写库）；--apply 翻译+写缓存+回填。
"""
import argparse
import csv
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

from s02b_entity_normalizer.company_lookup import LookupIndex, build_index, lookup
from s02b_entity_normalizer.db_backup import backup_db
from s02b_entity_normalizer.developer_normalizer import DEV_JUNK_VALUES, split_developer
from s02b_entity_normalizer.en_backfill import DEFAULT_CACHE, _cache_en
from s02b_entity_normalizer.entity_normalizer import DEFAULT_DB

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")
load_dotenv()

DEFAULT_RESIDUAL_CACHE = str(_ROOT / "supporting_docs" / "residual_translation_cache.json")
_CJK_RE = re.compile(r"[一-鿿]")
_TRANSLATE_MODEL = "gemini-2.5-flash-lite"
_BATCH_SIZE = 20
_CACHE_KEY_MAX_LEN = 60  # 超长键多为提取残渣，不固化进缓存（与 s01b 垃圾键守卫同旨）

# (表, 中文列, 英文列, 关联到 canonical_projects 的 JOIN 片段；表别名固定 t)
_SCAN_TARGETS = [
    ("chinese_entities", "implementing_entity", "implementing_entity_en",
     "JOIN project_events pe ON t.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id"),
    ("chinese_entities", "subsidiary_name", "subsidiary_name_en",
     "JOIN project_events pe ON t.event_id = pe.id JOIN canonical_projects cp ON pe.canonical_id = cp.id"),
    ("project_events", "project_developer", "project_developer_en",
     "JOIN canonical_projects cp ON t.canonical_id = cp.id"),
]


class RunParams(BaseModel):
    """入口参数校验（编程规则#4）：库与主缓存必须存在；残余缓存允许首跑不存在。"""
    db_path: str
    cache_path: str
    residual_path: str

    @field_validator("db_path", "cache_path")
    @classmethod
    def _must_exist(cls, v: str) -> str:
        if not Path(v).exists():
            raise ValueError(f"路径不存在: {v}")
        return v


def _register_has_cjk(conn: sqlite3.Connection) -> None:
    """在连接上注册 has_cjk SQL 函数（SQLite 无原生 CJK 判断）。"""
    conn.create_function("has_cjk", 1, lambda s: 1 if (s and _CJK_RE.search(str(s))) else 0)


def scan_targets(conn: sqlite3.Connection) -> List[dict]:
    """扫描三对列：非占位名、挂 excluded=0 项目、en 空/含中文 → 待回填值清单（含行数）。
    含中文名走翻译；纯 ASCII 名（已是英文，只是 _en 空）走直通复制（resolve_residual 内处理）。"""
    _register_has_cjk(conn)
    out: List[dict] = []
    for table, cn_col, en_col, join in _SCAN_TARGETS:
        rows = conn.execute(
            f"SELECT t.{cn_col}, COUNT(*) FROM {table} t {join} "
            f"WHERE COALESCE(cp.excluded, 0) = 0 "
            f"  AND t.{cn_col} IS NOT NULL AND TRIM(t.{cn_col}) <> '' "
            f"  AND (t.{en_col} IS NULL OR TRIM(t.{en_col}) = '' OR has_cjk(t.{en_col})) "
            f"GROUP BY t.{cn_col}"
        ).fetchall()
        for cn, n in rows:
            s = str(cn).strip()
            if s.lower() in DEV_JUNK_VALUES:
                continue
            out.append({"table": table, "cn_col": cn_col, "en_col": en_col, "cn": s, "rows": n})
    return out


def _residual_en(residual: Dict[str, dict], part: str) -> str:
    """残余缓存取值：条目缺失或 en 含中文视为无效，返回空串。"""
    v = residual.get(part)
    if not isinstance(v, dict):
        return ""
    en = str(v.get("en") or "").strip()
    return "" if (not en or _CJK_RE.search(en)) else en


def resolve_residual(cn, cache: Dict[str, dict], residual: Dict[str, dict],
                     index: Optional[LookupIndex]) -> Tuple[Optional[str], List[str]]:
    """逐部件四层解析：ASCII 直通（查册优先）> registry canonical_en > 主缓存 > 残余缓存。
    全部件解析成功 → ('; ' 拼接串, [])；否则 (None, 未解析中文部件列表)。"""
    parts = split_developer(cn)
    ens: List[str] = []
    missing: List[str] = []
    for p in parts:
        if index is not None:
            r = lookup(p, index)
            if r.matched and r.canonical_en:
                ens.append(r.canonical_en)
                continue
        if not _CJK_RE.search(p):
            ens.append(p)  # ASCII 部件直通（与 en_backfill/s01b 口径一致）
            continue
        en = _cache_en(cache, p) or _residual_en(residual, p)
        if en:
            ens.append(en)
            continue
        missing.append(p)
    if missing:
        return None, missing
    return "; ".join(ens), []


def _load_residual(path: str) -> Dict[str, dict]:
    """载入残余缓存；文件不存在返回空 dict；过滤 '_' 前缀元数据键。"""
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def _save_residual(path: str, residual: Dict[str, dict]) -> None:
    """残余缓存整体落盘（UTF-8、保留中文原文）。"""
    Path(path).write_text(json.dumps(residual, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_new_csv(path: str, new_entries: Dict[str, str]) -> None:
    """本次新增翻译写 CSV（utf-8-sig 便于 Excel 抽检，喂季度 campaign 升级 registry）。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["cn", "en", "model", "date"])
        today = datetime.now().strftime("%Y-%m-%d")
        for cn_part, en in new_entries.items():
            w.writerow([cn_part, en, _TRANSLATE_MODEL, today])


def _build_gemini_translate(api_key: str) -> Callable[[List[str]], Dict[str, str]]:
    """构造真实批量翻译函数：一批若干名称 → JSON 数组返回 {id, en}；3 次退避重试，全败返回空 dict。"""
    from google import genai
    from google.genai import types as _types
    client = genai.Client(api_key=api_key)

    def translate(names: List[str]) -> Dict[str, str]:
        numbered = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names))
        prompt = (
            "请将下列中国企业/机构名称翻译为英文官方名称（如有官方英文名优先使用，否则给出简洁合理译名）。\n"
            '以 JSON 数组返回，每个元素格式 {"id": <编号>, "en": "<英文名>"}，只返回 JSON，不要其他文字：\n'
            + numbered)
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=_TRANSLATE_MODEL, contents=prompt,
                    config=_types.GenerateContentConfig(temperature=0, response_mime_type="application/json"),
                )
                data = json.loads(resp.text)
                out: Dict[str, str] = {}
                for item in data:
                    i = int(item.get("id", 0)) - 1
                    if 0 <= i < len(names):
                        out[names[i]] = str(item.get("en", "")).strip()
                return out
            except Exception as e:  # API/解析异常型号繁多：统一记录并退避重试（与 s02b 其他 LLM 调用同模式）
                logger.error("残余翻译 LLM 调用失败 attempt=%d/3: %s", attempt + 1, e)
                time.sleep(2 ** attempt)
        return {}

    return translate


def _apply_updates(db_path: str, updates: List[dict]) -> int:
    """先备份 .bak，再事务逐组 executemany 回填（fill-if-null：只改 en 空/含中文的行）。"""
    bak = backup_db(db_path)
    conn = sqlite3.connect(db_path)
    _register_has_cjk(conn)
    total = 0
    try:
        groups: Dict[Tuple[str, str, str], List[dict]] = {}
        for u in updates:
            groups.setdefault((u["table"], u["cn_col"], u["en_col"]), []).append(u)
        for (table, cn_col, en_col), items in groups.items():
            cur = conn.executemany(
                f"UPDATE {table} SET {en_col} = ? WHERE {cn_col} = ? "
                f"AND ({en_col} IS NULL OR TRIM({en_col}) = '' OR has_cjk({en_col}))",
                [(u["new_en"], u["cn"]) for u in items],
            )
            total += cur.rowcount
        conn.commit()
        logger.info("剩余名称回填落库 %d 行（%d 个值）", total, len(updates))
        return total
    except sqlite3.Error as e:
        logger.error("回填失败（备份在 %s）: %s", bak, e)
        raise
    finally:
        conn.close()


def run_residual_translate(db_path: str, cache_path: Optional[str] = None,
                           residual_path: Optional[str] = None, use_registry: bool = True,
                           apply: bool = False,
                           translate_fn: Optional[Callable[[List[str]], Dict[str, str]]] = None,
                           out_csv: Optional[str] = None) -> dict:
    """2B-6 编排入口：扫描未翻译值 → 四层解析 → 缺口部件批量翻译并写残余缓存 → fill-if-null 落库。
    apply=False 只报缺口计数（不调 LLM、不写任何文件/库）。返回计数报告 dict。"""
    cache_path = cache_path or DEFAULT_CACHE
    residual_path = residual_path or DEFAULT_RESIDUAL_CACHE
    RunParams(db_path=db_path, cache_path=cache_path, residual_path=residual_path)

    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    residual = _load_residual(residual_path)
    index = build_index() if use_registry else None

    conn = sqlite3.connect(db_path)
    try:
        values = scan_targets(conn)
    finally:
        conn.close()

    # 第一轮解析：收集缺口部件（去重保序）
    missing_parts: List[str] = []
    seen: set = set()
    for v in values:
        _, missing = resolve_residual(v["cn"], cache, residual, index)
        for p in missing:
            if p not in seen:
                seen.add(p)
                missing_parts.append(p)

    report = {"values_scanned": len(values), "parts_missing": len(missing_parts),
              "parts_translated": 0, "parts_failed": 0, "values_filled": 0, "rows_updated": 0}
    if not apply:
        logger.info("剩余翻译 preview：待翻值 %d，缺口部件 %d（未调 LLM、未写库）",
                    len(values), len(missing_parts))
        return report

    # 缺口部件批量翻译；结果校验（非空、不含中文、键长合规）后才进残余缓存
    new_entries: Dict[str, str] = {}
    if missing_parts:
        if translate_fn is None:
            api_key = os.environ.get("GEMINI_API_KEY", "")
            if not api_key:
                raise RuntimeError("未找到 GEMINI_API_KEY（.env）")
            translate_fn = _build_gemini_translate(api_key)
        for i in range(0, len(missing_parts), _BATCH_SIZE):
            batch = missing_parts[i:i + _BATCH_SIZE]
            got = translate_fn(batch) or {}
            for name in batch:
                en = str(got.get(name, "")).strip().strip('"').strip("'")
                if en and not _CJK_RE.search(en) and len(name) <= _CACHE_KEY_MAX_LEN:
                    new_entries[name] = en
                else:
                    report["parts_failed"] += 1
        report["parts_translated"] = len(new_entries)
        if new_entries:
            today = datetime.now().strftime("%Y-%m-%d")
            for cn_part, en in new_entries.items():
                residual[cn_part] = {"en": en, "source": "llm_auto", "model": _TRANSLATE_MODEL,
                                     "date": today, "verified": False}
            _save_residual(residual_path, residual)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = out_csv or str(_ROOT / "supporting_docs" / f"residual_translation_new_{ts}.csv")
            _write_new_csv(csv_path, new_entries)
            logger.info("新增翻译 %d 条已写缓存与 %s", len(new_entries), csv_path)

    # 第二轮解析：整串可解析的值 fill-if-null 落库
    updates = []
    for v in values:
        en, missing = resolve_residual(v["cn"], cache, residual, index)
        if en is not None:
            updates.append({**v, "new_en": en})
    report["values_filled"] = len(updates)
    if updates:
        report["rows_updated"] = _apply_updates(db_path, updates)
    logger.info("剩余翻译回填：扫描 %d 值，翻译 %d 部件（失败 %d），回填 %d 行",
                report["values_scanned"], report["parts_translated"],
                report["parts_failed"], report["rows_updated"])
    return report


def main() -> None:
    """CLI：裸跑=preview 只报缺口计数；--apply 翻译 + 写残余缓存 + fill-if-null 回填。"""
    parser = argparse.ArgumentParser(description="剩余名称翻译回填（LLM + 残余缓存，fill-if-null）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="主库路径")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="主缓存 company_name_cache.json")
    parser.add_argument("--residual", default=DEFAULT_RESIDUAL_CACHE, help="残余翻译缓存路径")
    parser.add_argument("--apply", action="store_true", help="翻译并落库（默认 preview 只看不写）")
    parser.add_argument("--out", default=None, help="本次新增翻译 CSV 路径")
    parser.add_argument("--no-registry", action="store_true", help="不查 developer_registry.json（对照回退位）")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _ROOT / "supporting_docs" / f"residual_translation_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(log_path), encoding="utf-8"), logging.StreamHandler()],
    )

    report = run_residual_translate(
        args.db, cache_path=args.cache, residual_path=args.residual,
        use_registry=not args.no_registry, apply=args.apply, out_csv=args.out)
    mode = "apply" if args.apply else "preview"
    print(f">>> [{mode}] 待翻值 {report['values_scanned']} / 缺口部件 {report['parts_missing']} / "
          f"新翻译 {report['parts_translated']}（失败 {report['parts_failed']}）/ 回填 {report['rows_updated']} 行")
    if not args.apply:
        print(">>> preview 完成（未调 LLM、未写库）。确认后加 --apply 执行。")


if __name__ == "__main__":
    main()
