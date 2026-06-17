"""存量回填：给现有 bri_re_database.db.project_events 补 project_developer_en（业主英文）。

健壮版（Gemini 503 不稳 → 回填改用 deepseek LLMClient，有重试退避）：
- cache-first：命中 company_name_cache.json（Gemini 源）→ 与 entity_en 一致（重叠公司同英文）；
- ascii-only 直通；其余 CJK → deepseek 翻译，逐条 try 隔离，失败留 NULL（可重跑）。
- 不写回共享缓存（保持 cache Gemini 纯净）。先 .bak、并发、事务 UPDATE、UTF-8 报告。"""
import json
import os
import pathlib
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import find_dotenv, load_dotenv

root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
load_dotenv(find_dotenv(usecwd=True))

from s01b_project_extractor.llm.client import LLMClient, _config_for_provider  # noqa: E402

DDB = root / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
CACHE = root / "supporting_docs" / "company_name_cache.json"
out = []


def _has_cjk(s):
    return any("一" <= c <= "鿿" for c in str(s))


# ── 载入 Gemini 源缓存（只读，cache-first 复用）──
name_cache = {}
try:
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    name_cache = {k: v for k, v in raw.items() if not k.startswith("_")}
except OSError as e:
    out.append(f"读缓存失败（继续）: {e}")


def _cache_en(name):
    v = name_cache.get(name)
    if isinstance(v, dict):
        return v.get("en_abbr") or v.get("en") or ""
    return v or ""


# ── .bak + 加列 ──
bak = f"{DDB}.bak_pre_devbackfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(str(DDB), bak)
out.append(f"已备份 → {pathlib.Path(bak).name}")
conn = sqlite3.connect(str(DDB))
try:
    conn.execute("ALTER TABLE project_events ADD COLUMN project_developer_en TEXT")
    out.append("已加列 project_developer_en")
except sqlite3.OperationalError:
    out.append("列 project_developer_en 已存在")

distinct = [r[0] for r in conn.execute(
    "SELECT DISTINCT project_developer FROM project_events "
    "WHERE project_developer IS NOT NULL AND TRIM(project_developer)<>''")]
out.append(f"distinct 业主: {len(distinct)}")

# 分类：缓存命中 / ascii 直通 / 需 deepseek
mapping, need_llm = {}, []
for d in distinct:
    ce = _cache_en(d)
    if ce and not _has_cjk(ce):
        mapping[d] = ce
    elif not _has_cjk(d):
        mapping[d] = d                 # 英文直通
    else:
        need_llm.append(d)
out.append(f"缓存命中: {sum(1 for d in distinct if d in mapping and _cache_en(d))} | ascii 直通另计 | 需 deepseek: {len(need_llm)}")

cfg = _config_for_provider("deepseek")
llm = LLMClient(cfg)
out.append(f"deepseek model={cfg.model}")
_PROMPT = ("将以下项目业主/公司名称翻译为官方英文名称（有官方英文名优先；多个用分号分隔的逐一翻译）。"
           "只输出英文名称，不要解释、不要引号：\n")
fails = [0]


def _tr(dev):
    try:
        en = llm.complete(_PROMPT + dev, stage="translate", json_mode=False)
        en = (en or "").strip().strip('"').strip("'")
        return dev, (en or dev)
    except Exception as e:
        print(f"[fail] {dev}: {type(e).__name__}")
        fails[0] += 1
        return dev, None                # 失败留 NULL，可重跑


with ThreadPoolExecutor(max_workers=24) as ex:
    for dev, en in ex.map(_tr, need_llm):
        if en is not None:
            mapping[dev] = en
out.append(f"deepseek 翻译失败(留NULL,可重跑): {fails[0]}")

# ── 事务批量 UPDATE ──
pairs = [(mapping[d], d) for d in distinct if d in mapping and mapping[d]]
with conn:
    conn.executemany(
        "UPDATE project_events SET project_developer_en=? WHERE project_developer=?", pairs)
filled = conn.execute(
    "SELECT COUNT(*) FROM project_events WHERE project_developer_en IS NOT NULL AND TRIM(project_developer_en)<>''").fetchone()[0]
tot = conn.execute("SELECT COUNT(*) FROM project_events WHERE project_developer IS NOT NULL AND TRIM(project_developer)<>''").fetchone()[0]
out.append(f"已回填行数 project_developer_en 非空: {filled}/{tot}（业主非空事件）")
out.append("抽样:")
for r in conn.execute("SELECT project_developer, project_developer_en FROM project_events "
                      "WHERE project_developer_en IS NOT NULL AND TRIM(project_developer_en)<>'' LIMIT 10"):
    out.append(f"  {r[0]}  →  {r[1]}")
conn.close()

(pathlib.Path(__file__).parent / "_developer_en_backfill.txt").write_text("\n".join(out), encoding="utf-8")
print("done")
