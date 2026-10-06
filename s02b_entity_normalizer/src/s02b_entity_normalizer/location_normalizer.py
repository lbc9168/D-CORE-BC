"""location_normalizer.py — 项目地点归一化（canonical_projects.location → location_norm）

复用根 bri_location（三信号 + LLM 确认 + Union-Find），按 country_iso 分组归一。
只写新列 location_norm，绝不覆盖原 location（可逆、安全）。

使用方法（在 s02b_entity_normalizer/ 目录下）：
    uv run normalize_locations             # 正式运行，写 location_norm
    uv run normalize_locations --preview   # 仅预览候选对，不调 LLM、不写库
    uv run normalize_locations --db PATH   # 指定数据库（默认 s02a 主库）
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Windows GBK 控制台下强制 UTF-8 输出，避免中文乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() in ("gbk", "cp936", "cp950"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

_S02B_ROOT = Path(__file__).resolve().parents[2]  # s02b_entity_normalizer/
_repo_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
import bri_location

DEFAULT_DB = _S02B_ROOT.parent / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"


def _load_locations_by_country(db_path: str) -> Dict[str, List[Tuple[str, int]]]:
    """读 canonical_projects 非空 location，按 country_iso 分组返回 {iso: [(地名, 出现次数)]}。"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT country_iso, location, COUNT(*) FROM canonical_projects "
        "WHERE location IS NOT NULL AND location != '' "
        "GROUP BY country_iso, location"
    ).fetchall()
    conn.close()
    by_country: Dict[str, List[Tuple[str, int]]] = {}
    for iso, loc, n in rows:
        by_country.setdefault(iso or "UNK", []).append((loc, n))
    return by_country


def _write_location_norm(db_path: str, mapping_by_country: Dict[str, Dict[str, str]]) -> int:
    """把 {iso: {别名→规范名}} 写入 canonical_projects.location_norm（事务 + executemany）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("ALTER TABLE canonical_projects ADD COLUMN location_norm TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在（已知无害边界，按 CLAUDE.md 规则1 沉默）
    params = [
        (canon, iso, alias)
        for iso, mapping in mapping_by_country.items()
        for alias, canon in mapping.items()
    ]
    n = 0
    if params:
        cur = conn.executemany(
            "UPDATE canonical_projects SET location_norm = ? "
            "WHERE country_iso = ? AND location = ?",
            params,
        )
        n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def run(db_path: Optional[str] = None, preview: bool = False) -> None:
    """CLI 入口：加载地名 → 候选预览（preview 止步于此）→ LLM 确认 → 写 location_norm。"""
    if db_path is None and preview is False:
        parser = argparse.ArgumentParser(description="项目地点归一化（音译变体合并，写 location_norm）")
        parser.add_argument("--preview", action="store_true", help="仅预览候选对，不调 LLM、不写库")
        parser.add_argument("--db", type=str, default=None, help=f"数据库路径（默认：{DEFAULT_DB}）")
        args, _ = parser.parse_known_args()
        preview, db_path = args.preview, args.db

    if db_path is None:
        db_path = str(DEFAULT_DB)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    print(f"\n{'=' * 50}\n  地点归一化工具{'（预览模式）' if preview else ''}\n{'=' * 50}")
    print(f"  数据库: {db_path}")

    by_country = _load_locations_by_country(db_path)
    print(f"  加载 {sum(len(v) for v in by_country.values())} 个 (国家, 地名) 组合，覆盖 {len(by_country)} 国")

    n_cand = 0
    for iso, names in sorted(by_country.items()):
        for p in bri_location.find_location_candidates(names):
            n_cand += 1
            print(f"    [{iso}] 「{p['name_a']}」({p['count_a']}) <-> "
                  f"「{p['name_b']}」({p['count_b']})  [{p['signals']}]")
    print(f"  候选地名对合计: {n_cand}")
    if preview:
        print("\n[预览模式] 跳过 LLM 确认与写库。")
        return
    if n_cand == 0:
        print("  无候选对，无需归一化。")
        return

    # 延迟导入：preview 模式无需 OpenAI 凭据，也不触发模型调用。
    from dotenv import load_dotenv
    from s02b_entity_normalizer.openai_gateway import chat_completion_json
    load_dotenv(_S02B_ROOT / ".env")
    load_dotenv()
    location_model = os.getenv("S02B_LOCATION_MODEL", "gpt-6-luna").strip()
    location_effort = os.getenv("S02B_LOCATION_EFFORT", "medium").strip().lower()
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GPT_API_KEY", "")
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY 或 GPT_API_KEY，请在 .env 文件中配置")

    def generate_text(prompt: str) -> str:
        return chat_completion_json(
            prompt,
            model=location_model,
            effort=location_effort,
            api_key=api_key,
        )

    mapping_by_country = bri_location.build_location_mapping(
        by_country,
        None,
        generate_text=generate_text,
    )
    for iso, mapping in sorted(mapping_by_country.items()):
        for alias, canon in sorted(mapping.items()):
            print(f"    [{iso}] {alias}  →  {canon}")
    if not mapping_by_country:
        print("  LLM 未确认任何同地点对，无需写库。")
        return

    n_rows = _write_location_norm(db_path, mapping_by_country)
    n_alias = sum(len(m) for m in mapping_by_country.values())
    print(f"\n  ✓ location_norm 更新 {n_rows} 行（{n_alias} 条别名映射）")


if __name__ == "__main__":
    run()
