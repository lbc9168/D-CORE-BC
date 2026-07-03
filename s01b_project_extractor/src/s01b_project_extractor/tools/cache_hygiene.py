"""缓存卫生工具：规则扫描 company_name_cache.json 垃圾键/值 + corrections CSV upsert。

dry-run 默认（产报告 CSV 不写缓存）；--apply 先备份 .bak 再写回。
未来"分层核实 campaign"的核实结果也走 --corrections 同一通道写回。
用法：uv run cache_fix [--cache PATH] [--corrections CSV] [--findings CSV] [--apply] [--out CSV]
"""
import argparse
import csv
import json
import logging
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)

_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
DEFAULT_CACHE = str(_ROOT / "supporting_docs" / "company_name_cache.json")

_SEP_RE = re.compile(r"[;；、，,/]")
_CJK_RE = re.compile(r"[一-鿿]")
_TITLE_WORDS = ["董事长", "总经理", "书记", "副总裁", "党委", "首席"]
_GENERIC_WORDS = ["知名", "数家", "多家", "该公司", "未指明", "全球公司"]
_PLACEHOLDER_KEYS = {"n/a", "na", "-", "unknown", "未知", "不详"}
_MAX_KEY_LEN = 30
_MAX_EN_LEN = 120


def scan_cache(cache: dict) -> list[dict]:
    """规则扫描垃圾条目：返回 [{key, rule, en, note}]；verified=true 条目绝不列入。"""
    findings: list[dict] = []

    def _add(key, rule, en, note):
        findings.append({"key": key, "rule": rule, "en": en, "note": note})

    for key, val in cache.items():
        if key.startswith("_"):
            continue
        entry = val if isinstance(val, dict) else {"en": str(val)}
        if entry.get("verified"):
            continue  # 人工核实条目豁免
        en = str(entry.get("en") or "")
        if _SEP_RE.search(key):
            _add(key, "R1_key_separator", en, "键含分隔符（多公司串/头衔串）")
        elif any(w in key for w in _TITLE_WORDS):
            _add(key, "R2_key_person_title", en, "键含人名头衔词")
        elif key.strip().lower() in _PLACEHOLDER_KEYS or any(w in key for w in _GENERIC_WORDS):
            _add(key, "R3_key_placeholder", en, "键为占位/泛指短语")
        elif len(key) > _MAX_KEY_LEN:
            _add(key, "R4_key_too_long", en, f"键长 {len(key)} > {_MAX_KEY_LEN}")
        elif _CJK_RE.search(en):
            _add(key, "R5_value_cjk", en, "英文值含中文（翻译失败被固化）")
        elif "\n" in en or len(en) > _MAX_EN_LEN or en.startswith("[") or not en.strip():
            _add(key, "R6_value_noise", en, "英文值含换行/超长/括注噪声/为空")
    return findings


def load_corrections(csv_path: str) -> list[dict]:
    """读 corrections CSV（列 cn,en,en_abbr,verified,source）→ 行 dict 列表；空 cn 行跳过。"""
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            cn = str(r.get("cn") or "").strip()
            if not cn:
                continue
            rows.append({
                "cn": cn,
                "en": str(r.get("en") or "").strip(),
                "en_abbr": str(r.get("en_abbr") or "").strip() or None,
                "verified": str(r.get("verified") or "").strip().lower() == "true",
                "source": str(r.get("source") or "correction").strip(),
            })
    return rows


def plan_corrections(cache: dict, corrections: list[dict]) -> list[dict]:
    """corrections 与现缓存比对 → would-change 行（新键或 en/en_abbr 有变化才列出）。"""
    plan: list[dict] = []
    for c in corrections:
        cur = cache.get(c["cn"])
        cur = cur if isinstance(cur, dict) else ({"en": str(cur)} if cur else None)
        if cur is None:
            plan.append({"cn": c["cn"], "old_en": "", "new_en": c["en"],
                         "new_abbr": c["en_abbr"] or "", "is_new": True})
        elif (cur.get("en") or "") != c["en"] or (cur.get("en_abbr") or None) != c["en_abbr"]:
            plan.append({"cn": c["cn"], "old_en": str(cur.get("en") or ""), "new_en": c["en"],
                         "new_abbr": c["en_abbr"] or "", "is_new": False})
    return plan


def apply_changes(cache_path: str, remove_keys: list[str], corrections: list[dict]) -> dict:
    """备份 .bak 后写回：删除 remove_keys（verified 键豁免不删）+ upsert corrections；保留 _ 元字段。"""
    raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
    bak = cache_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
    shutil.copy2(cache_path, bak)
    logger.info("缓存已备份 %s", bak)

    removed = 0
    for key in remove_keys:
        entry = raw.get(key)
        if entry is None:
            continue
        if isinstance(entry, dict) and entry.get("verified"):
            logger.error("拒绝删除 verified 键（防误操作）: %s", key)
            continue
        del raw[key]
        removed += 1
    for c in corrections:
        raw[c["cn"]] = {"en": c["en"], "en_abbr": c["en_abbr"],
                        "verified": c["verified"], "source": c["source"]}
    Path(cache_path).write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("缓存写回：删除 %d 键、upsert %d 条", removed, len(corrections))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def main() -> None:
    """CLI：默认 dry-run（扫描+corrections 计划 → 报告 CSV）；--apply 时按 --findings 清单删除 + corrections 写回。"""
    parser = argparse.ArgumentParser(description="company_name_cache.json 卫生工具")
    parser.add_argument("--cache", default=DEFAULT_CACHE)
    parser.add_argument("--corrections", default=None, help="corrections CSV（cn,en,en_abbr,verified,source）")
    parser.add_argument("--findings", default=None, help="apply 时要删除的键清单 CSV（复看后的 scan 报告）")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", default=None, help="dry-run 报告 CSV 路径")
    args = parser.parse_args()

    if not Path(args.cache).exists():
        parser.error(f"缓存文件不存在: {args.cache}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.cache).parent / f"cache_hygiene_{ts}.csv")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(out_csv).with_suffix(".log")), encoding="utf-8"),
                  logging.StreamHandler()],
    )

    raw = json.loads(Path(args.cache).read_text(encoding="utf-8"))
    cache = {k: v for k, v in raw.items() if not k.startswith("_")}
    corrections = load_corrections(args.corrections) if args.corrections else []

    if not args.apply:
        findings = scan_cache(cache)
        plan = plan_corrections(cache, corrections)
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["kind", "key_or_cn", "rule_or_isnew", "current_en", "new_en", "note"])
            for x in findings:
                w.writerow(["remove_candidate", x["key"], x["rule"], x["en"], "", x["note"]])
            for p in plan:
                w.writerow(["correction", p["cn"], "new" if p["is_new"] else "update",
                            p["old_en"], p["new_en"], p["new_abbr"]])
        print(f">>> dry-run：清理候选 {len(findings)} 键、corrections 变更 {len(plan)} 条，报告: {out_csv}")
        return

    remove_keys: list[str] = []
    if args.findings:
        with open(args.findings, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("kind") or "") == "remove_candidate":
                    remove_keys.append(str(r.get("key_or_cn") or ""))
    apply_changes(args.cache, remove_keys, corrections)
    print(f">>> 已 apply：删除 {len(remove_keys)} 键（verified 豁免除外）、upsert {len(corrections)} 条")


if __name__ == "__main__":
    main()
