"""中国实体别名候选发现器：只读扫描主库，输出人工审核 CSV。

正式实体归类由 s01b 的 ``company_hierarchy_v2.json`` 确定性解析器负责。本模块只用
字符串相似度发现登记册可能遗漏的别名，不调用 LLM、不构造传递闭包，也不更新数据库。
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from rapidfuzz import fuzz

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger(__name__)
_S02B_ROOT = Path(__file__).resolve().parents[2]
_PROJECT_ROOT = _S02B_ROOT.parent

DEFAULT_DB = _PROJECT_ROOT / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
DEFAULT_HIERARCHY = _PROJECT_ROOT / "supporting_docs" / "company_hierarchy_v2.json"

FUZZY_TOKEN_THRESHOLD = 80
FUZZY_PARTIAL_THRESHOLD = 90
PARTIAL_MAX_LEN_RATIO = 2.5
SUBSEQ_MIN_LEN = 4
SUBSEQ_MAX_LEN = 8
SUBSEQ_MIN_LEN_DIFF = 2
SUBSEQ_MAX_LEN_RATIO = 0.68
SUBSEQ_PREFIX_MATCH = 3
MIN_BOTH_LEN = 3
BATCH_SIZE = 25  # developer_normalizer 的兼容导入；实体建议流程本身不分批。

UNKNOWN_VARIANTS = {"unknown", "未知", "不详", "不明", "暂无", "n/a", "na", "-"}
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
    """剥除一个常见公司尾缀，供登记册命中状态判断。"""
    for suffix in _CORP_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_subsequence(short: str, long_: str) -> bool:
    """判断短名字符是否按顺序出现在长名中，用于发现可能的简称。"""
    iterator = iter(long_)
    return all(char in iterator for char in short)


def _find_candidates(names_with_counts: List[Tuple[str, int]]) -> List[Dict]:
    """扫描名称对，返回满足至少一个相似度信号的只读审核候选。"""
    names = [name for name, _ in names_with_counts]
    count_map = {name: count for name, count in names_with_counts}
    candidates: List[Dict] = []

    for index, name_a in enumerate(names):
        for name_b in names[index + 1 :]:
            if len(name_a) < MIN_BOTH_LEN or len(name_b) < MIN_BOTH_LEN:
                continue

            signals: List[str] = []
            score_token = fuzz.token_sort_ratio(name_a, name_b)
            score_partial = fuzz.partial_ratio(name_a, name_b)

            if score_token >= FUZZY_TOKEN_THRESHOLD:
                signals.append(f"token_sort={score_token}")

            if score_partial >= FUZZY_PARTIAL_THRESHOLD:
                short_p, long_p = sorted((name_a, name_b), key=len)
                if len(long_p) / len(short_p) <= PARTIAL_MAX_LEN_RATIO:
                    signals.append(f"partial={score_partial}")

            if not signals:
                short, long_ = sorted((name_a, name_b), key=len)
                prefix_n = min(SUBSEQ_PREFIX_MATCH, len(short) - 1)
                if (
                    SUBSEQ_MIN_LEN <= len(short) <= SUBSEQ_MAX_LEN
                    and len(long_) - len(short) >= SUBSEQ_MIN_LEN_DIFF
                    and len(short) / len(long_) <= SUBSEQ_MAX_LEN_RATIO
                    and short[:prefix_n] == long_[:prefix_n]
                    and _is_subsequence(short, long_)
                ):
                    signals.append("subsequence")

            if signals:
                candidates.append(
                    {
                        "id": len(candidates),
                        "name_a": name_a,
                        "count_a": count_map[name_a],
                        "name_b": name_b,
                        "count_b": count_map[name_b],
                        "signals": ", ".join(signals),
                        "score_token": score_token,
                        "score_partial": score_partial,
                    }
                )
    return candidates


def _build_mapping(
    names_with_counts: List[Tuple[str, int]], confirmed_pairs: List[Dict]
) -> Dict[str, str]:
    """兼容 developer_normalizer 的旧辅助函数；不得用于中方实体字段写库。"""
    all_names = {name for name, _ in names_with_counts}
    parent: Dict[str, str] = {name: name for name in all_names}
    canonical_by_root: Dict[str, str] = {}

    def find(name: str) -> str:
        while parent.get(name, name) != name:
            parent[name] = parent.get(parent[name], parent[name])
            name = parent[name]
        return name

    def union(name_a: str, name_b: str, canonical: str) -> None:
        for name in (name_a, name_b, canonical):
            parent.setdefault(name, name)
        root_a, root_b, root_c = find(name_a), find(name_b), find(canonical)
        parent[root_a] = root_c
        parent[root_b] = root_c
        canonical_by_root[root_c] = canonical

    for pair in confirmed_pairs:
        if pair.get("is_same") and pair.get("canonical_name"):
            union(pair["name_a"], pair["name_b"], pair["canonical_name"])

    mapping: Dict[str, str] = {}
    for name in all_names:
        root = find(name)
        canonical = canonical_by_root.get(root, root)
        if canonical != name:
            mapping[name] = canonical
    return mapping


def _load_registered_names(hierarchy_path: str | os.PathLike[str]) -> tuple[set[str], set[str]]:
    """校验并加载公司层级登记册的全部名称键及无歧义去尾缀键。"""
    path = Path(hierarchy_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        raise ValueError(f"公司层级登记册结构无效: {path}")

    names: set[str] = set()
    for group in data["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("canonical_name_cn"), str):
            raise ValueError(f"公司层级登记册 group 结构无效: {group!r}")
        names.add(group["canonical_name_cn"].strip())
        aliases = group.get("aliases", []) or []
        subsidiaries = group.get("subsidiaries", []) or []
        if not isinstance(aliases, list) or not isinstance(subsidiaries, list):
            raise ValueError(f"公司层级登记册 aliases/subsidiaries 结构无效: {group!r}")
        names.update(str(alias).strip() for alias in aliases if str(alias).strip())
        for subsidiary in subsidiaries:
            if not isinstance(subsidiary, dict):
                raise ValueError(f"公司层级登记册 subsidiary 结构无效: {subsidiary!r}")
            for key in ("name_cn", "legal_full_name_cn"):
                value = str(subsidiary.get(key) or "").strip()
                if value:
                    names.add(value)
            sub_aliases = subsidiary.get("aliases", []) or []
            if not isinstance(sub_aliases, list):
                raise ValueError(f"公司层级登记册 subsidiary.aliases 结构无效: {subsidiary!r}")
            names.update(str(alias).strip() for alias in sub_aliases if str(alias).strip())

    stripped_owners: dict[str, set[str]] = {}
    for name in names:
        stripped = _strip_suffix(name)
        if stripped != name:
            stripped_owners.setdefault(stripped, set()).add(name)
    unambiguous_stripped = {key for key, owners in stripped_owners.items() if len(owners) == 1}
    return names, unambiguous_stripped


def _is_registered(name: str, exact: set[str], stripped: set[str]) -> bool:
    """判断名称是否能由登记册的精确或无歧义去尾缀规则处理。"""
    if name in exact:
        return True
    core = _strip_suffix(name)
    return core != name and core in stripped


def generate_suggestions(
    db_path: str,
    out_path: str,
    hierarchy_path: str | os.PathLike[str] = DEFAULT_HIERARCHY,
) -> dict[str, int | str]:
    """只读扫描实体名并写审核 CSV；返回统计信息。"""
    db = Path(db_path)
    if not db.exists():
        raise FileNotFoundError(f"数据库不存在: {db}")

    exact, stripped = _load_registered_names(hierarchy_path)
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT implementing_entity, COUNT(*) FROM chinese_entities "
            "WHERE implementing_entity IS NOT NULL AND TRIM(implementing_entity) <> '' "
            "GROUP BY implementing_entity ORDER BY COUNT(*) DESC"
        ).fetchall()
    finally:
        connection.close()

    names_with_counts = [
        (str(name).strip(), int(count))
        for name, count in rows
        if str(name).strip().lower() not in UNKNOWN_VARIANTS
    ]
    candidates = _find_candidates(names_with_counts)
    suggestions = []
    for candidate in candidates:
        registered_a = _is_registered(candidate["name_a"], exact, stripped)
        registered_b = _is_registered(candidate["name_b"], exact, stripped)
        if registered_a and registered_b:
            continue
        suggestions.append(
            {
                **candidate,
                "registered_a": int(registered_a),
                "registered_b": int(registered_b),
                "review_relationship": "",
                "approved_canonical": "",
                "review_notes": "",
            }
        )

    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "name_a",
        "count_a",
        "registered_a",
        "name_b",
        "count_b",
        "registered_b",
        "signals",
        "score_token",
        "score_partial",
        "review_relationship",
        "approved_canonical",
        "review_notes",
    ]
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(suggestions)

    result: dict[str, int | str] = {
        "names": len(names_with_counts),
        "candidates": len(candidates),
        "suggestions": len(suggestions),
        "out_path": str(output),
    }
    logger.info("实体别名只读建议生成完成: %s", result)
    return result


def _update_db(*_args, **_kwargs) -> tuple[int, int]:
    """拒绝旧式模糊映射写库，防止母子公司及历史记录再次被全表改写。"""
    raise RuntimeError(
        "旧式实体模糊映射写库已禁用；请审核建议并更新 company_hierarchy_v2.json，"
        "再使用 S01B 确定性解析器处理明确记录。"
    )


def run(db_path: str | None = None, preview: bool = False, out: str | None = None) -> dict:
    """CLI/流水线入口：始终只读并输出候选审核 CSV，preview 参数仅保留兼容。"""
    if db_path is None:
        parser = argparse.ArgumentParser(description="中国实体别名候选发现（只读，不写数据库）")
        parser.add_argument("--db", default=str(DEFAULT_DB), help="主数据库路径")
        parser.add_argument("--out", default=None, help="审核 CSV 输出路径")
        parser.add_argument(
            "--preview",
            action="store_true",
            help="兼容参数；当前命令始终是只读 preview",
        )
        args = parser.parse_args()
        db_path = args.db
        out = args.out
        preview = True

    del preview  # 当前入口不存在 apply 模式。
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = out or str(Path(db_path).parent / "reports" / f"entity_alias_suggestions_{timestamp}.csv")
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(str(Path(output).with_suffix(".log")), encoding="utf-8")],
    )
    result = generate_suggestions(str(db_path), output)
    print(
        "实体别名候选已只读导出："
        f"names={result['names']} candidates={result['candidates']} "
        f"suggestions={result['suggestions']} CSV={result['out_path']}"
    )
    return result


if __name__ == "__main__":
    run()
