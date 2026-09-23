# s02e_source_finder P1（中文外链）实施计划

> 历史计划，已由 2026-09-06 用户确认的新流程替代，不继续执行本文的直接写主库方案。当前为真实单项目产品验收阶段，见 [s02e 状态](../../status/s02e.md) 与 [模块 README](../../../s02e_source_finder/README.md)。下文仅保留历史依据。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建可选子项目 `s02e_source_finder`，按事件给"网站可见 + 当前无链接"的项目事件用 Serper(中文)+Gemini 找一个中文参考源、写进 `project_events` 的链接字段；并让 s03/s04 按事件展示这些链接。

**Architecture:** 复用 `external-tools/external-link-adder/fill_links.py` 已验证的 Serper/Gemini/限速/重试原语，外面换成"读写主库 + 按事件搜 + 反塌缩 + 熔断"。模块拆成 `gate / queries / search / db / runner / main` 六个单一职责文件。下游 s03 只新增一个 `event_links` 字段、s04 只重写一个 section。

**Tech Stack:** Python 3.x + `uv`（独立子项目）、`sqlite3`（内置）、`requests`（Serper）、`google-genai`（Gemini gemini-2.5-flash-lite）、`pydantic`（入口校验）。测试 `pytest`，**全程 mock Serper/Gemini**（不打真实 API）。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-06-25-s02e-source-finder-design.md`（本计划只做其中 **P1**；P2 另立计划）。
- 取代 s02c，但**不删 s02c**。
- **不改任何 schema**：链接写现有 `project_events.official_website_links` / `non_official_website_links`（JSON 数组）。
- **s03 可见性逻辑（`passes_baseline`）一律不动**；s02e 用自带宽松 gate。
- 运行时输出用 `logging` 写文件（含时间戳/模块/级别）；`print` 仅限 `main.py` 顶层人机提示（CLAUDE.md 规则2）。
- 异常**禁止裸 `except: pass`**；捕获指定具体类型 + `logging.error` 记上下文（规则1）。唯一可静默：ALTER 列已存在类已知边界并加注释。
- 批量写库用**事务**包裹，>50 条用 `executemany`（规则3）。
- 入口参数（`main.run`/`run_source_finder`）用 Pydantic `BaseModel` 校验（规则4）。
- 真实跑 **GATED 人值守**，默认不进 `pipeline/flow.py`。
- 默认 `--max-events = 3500`（熔断器）；`pending > max_events` → 中止报警、要求显式放行。
- 默认 Serper 限速 ≤2/s + Gemini 指数退避重试（沿用 fill_links）。

---

## 文件结构

**新建子项目 `s02e_source_finder/`**（参照 `s02b_entity_normalizer` 的扁平结构）：

| 文件 | 职责 |
|------|------|
| `pyproject.toml` | 子项目定义 + `run_source_finder` 脚本入口 |
| `src/s02e_source_finder/__init__.py` | 空包标记 |
| `src/s02e_source_finder/gate.py` | `passes_s02e_gate(...)`：宽松超集范围门槛（纯函数） |
| `src/s02e_source_finder/queries.py` | `build_event_queries(...)`：按事件构造"阶段词+年份"查询（纯函数） |
| `src/s02e_source_finder/search.py` | `serper_search(...)`（中文 gl/hl）+ `judge_event_source(...)`（Gemini 按事件判定） |
| `src/s02e_source_finder/db.py` | `load_targets(conn)` 读目标事件 + `write_event_links(conn, ...)` 写链接 |
| `src/s02e_source_finder/runner.py` | `run_source_finder(cfg, search_fn, judge_fn)` 编排：逐事件、跨事件去重、兜底、熔断、dry-run |
| `src/s02e_source_finder/main.py` | `run()` + CLI（`--dry-run/--limit/--max-events/--workers`） |
| `tests/test_gate.py` / `test_queries.py` / `test_search.py` / `test_db.py` / `test_runner.py` | 单测（mock API） |

**改下游（P1-b）**：
- Modify `s03_data_exporter/src/s03_data_exporter/flat_builder.py`（加 `event_links` 字段）
- Modify `s03_data_exporter/tests/`（新增 event_links 测试）
- Modify `s04_web_visualization/src/types/index.ts`（`Project.event_links?`）
- Modify `s04_web_visualization/src/components/projects/ProjectDetail.tsx`（重写外部链接 section）

---

## Task 1: 子项目脚手架

**Files:**
- Create: `s02e_source_finder/pyproject.toml`
- Create: `s02e_source_finder/src/s02e_source_finder/__init__.py`
- Create: `s02e_source_finder/tests/__init__.py`

**Interfaces:**
- Produces: 可 `uv --project s02e_source_finder run pytest` 的空骨架；脚本名 `run_source_finder`。

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "s02e_source_finder"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "requests>=2.31.0",
    "google-genai>=1.49.0",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
]

[project.scripts]
run_source_finder = "s02e_source_finder.main:run"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/s02e_source_finder"]

[dependency-groups]
dev = ["pytest>=8.0.0"]
```

- [ ] **Step 2: 建空包文件**

`src/s02e_source_finder/__init__.py` 与 `tests/__init__.py` 均为空文件。

- [ ] **Step 3: 安装并确认骨架可跑**

Run: `uv --project s02e_source_finder sync`
Expected: 安装成功，无报错。

- [ ] **Step 4: Commit**

```bash
git add s02e_source_finder/pyproject.toml s02e_source_finder/src/s02e_source_finder/__init__.py s02e_source_finder/tests/__init__.py
git commit -m "feat(s02e): 子项目脚手架"
```

---

## Task 2: gate.py —— 宽松超集范围门槛

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/gate.py`
- Test: `s02e_source_finder/tests/test_gate.py`

**Interfaces:**
- Produces: `passes_s02e_gate(country_iso: str, category: str, excluded: int, event_years: list[int]) -> bool`。`event_years` 是该项目所有事件的年份（int 列表，可空）。规则=稳条件 + 宽松年份，保证是 s03 可见集的超集。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_gate.py
from s02e_source_finder.gate import passes_s02e_gate

def test_pass_normal_project():
    assert passes_s02e_gate("IND", "光伏", 0, [2023]) is True

def test_exclude_cn_hk_mo_tw():
    for iso in ("CHN", "HKG", "MAC", "TWN"):
        assert passes_s02e_gate(iso, "光伏", 0, [2023]) is False

def test_exclude_when_excluded_flag():
    assert passes_s02e_gate("IND", "光伏", 1, [2023]) is False

def test_exclude_hidden_category():
    # 火电/核电/新能源制造业/输变电建设 为隐藏类（不在网站显示）
    assert passes_s02e_gate("IND", "火电", 0, [2023]) is False

def test_loose_year_any_event_ge_2022_passes():
    assert passes_s02e_gate("IND", "光伏", 0, [2019, 2022]) is True

def test_exclude_all_events_before_2022():
    assert passes_s02e_gate("IND", "光伏", 0, [2019, 2021]) is False

def test_no_event_years_excluded():
    assert passes_s02e_gate("IND", "光伏", 0, []) is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_gate.py -v`
Expected: FAIL（`ModuleNotFoundError: s02e_source_finder.gate`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/gate.py
"""s02e 范围门槛：故意宽松的超集 gate——只卡稳条件 + 宽松年份，保证覆盖 s03 所有可见项目。"""
import sys
from pathlib import Path

# 复用根 bri_enums 的隐藏类别判定（单一可信源）
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bri_enums  # noqa: E402

_EXCLUDED_ISO = {"CHN", "HKG", "MAC", "TWN"}
BASELINE_YEAR = 2022


def passes_s02e_gate(country_iso: str, category: str, excluded: int, event_years: list[int]) -> bool:
    """判断一个项目是否进入 s02e 搜索范围（宽松超集：少卡脆弱派生量，只多放不漏）。"""
    if (country_iso or "").upper() in _EXCLUDED_ISO:
        return False
    if excluded:
        return False
    if bri_enums.is_hidden_category(category):
        return False
    # 宽松年份：任意事件年份 >= 2022 即放行（比 s03 精确的 last_event_year 更松 → 超集）
    if not any(y is not None and y >= BASELINE_YEAR for y in event_years):
        return False
    return True
```

> 注：若 `bri_enums` 无 `is_hidden_category`，用其等价访问器（实施时确认函数名，与 s03/s04 判隐藏类同口径）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_gate.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/gate.py s02e_source_finder/tests/test_gate.py
git commit -m "feat(s02e): 宽松超集范围 gate + 测试"
```

---

## Task 3: queries.py —— 按事件构造查询（反塌缩）

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/queries.py`
- Test: `s02e_source_finder/tests/test_queries.py`

**Interfaces:**
- Produces: `build_event_queries(project_name: str, entity: str, country: str, stage: str, year: int | None) -> list[str]`。返回**有序查询列表**：①事件专属（实体+项目名+阶段词+年份）②项目级兜底（项目名+国家）。空字段自动省略。`STAGE_SEARCH_KEYWORDS: dict[str,str]`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_queries.py
from s02e_source_finder.queries import build_event_queries, STAGE_SEARCH_KEYWORDS

def test_event_specific_then_fallback():
    qs = build_event_queries("某光伏电站", "中国能建", "印度", "开工", 2024)
    assert qs[0] == "中国能建 某光伏电站 开工 2024"      # 事件专属
    assert qs[-1] == "某光伏电站 印度"                   # 项目级兜底
    assert len(qs) == 2

def test_missing_entity_skips_entity_in_q1():
    qs = build_event_queries("某光伏电站", "", "印度", "竣工", 2026)
    assert qs[0] == "某光伏电站 竣工 2026"

def test_missing_year_omitted():
    qs = build_event_queries("某光伏电站", "中国能建", "印度", "开工", None)
    assert qs[0] == "中国能建 某光伏电站 开工"

def test_stage_without_keyword_falls_back_to_stage_text():
    qs = build_event_queries("某光伏电站", "中国能建", "印度", "收购", 2025)
    assert "收购" in qs[0]

def test_keyword_map_covers_main_stages():
    for s in ("中标", "签约", "开工", "在建", "竣工", "投入运营", "收购"):
        assert s in STAGE_SEARCH_KEYWORDS
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_queries.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/queries.py
"""按事件构造搜索查询：用阶段词+年份把搜索偏向特定里程碑，对抗"所有事件搜出同一条"。"""

# 阶段 → 搜索关键词（搜索专用，非枚举映射；多义阶段取最常见词）
STAGE_SEARCH_KEYWORDS: dict[str, str] = {
    "中标": "中标",
    "签约": "签约",
    "开工": "开工",
    "在建": "建设",
    "竣工": "竣工",
    "投入运营": "投产",
    "收购": "收购",
}


def build_event_queries(project_name: str, entity: str, country: str,
                        stage: str, year: int | None) -> list[str]:
    """返回有序查询：[事件专属, 项目级兜底]。空字段省略，保序去重。"""
    kw = STAGE_SEARCH_KEYWORDS.get(stage, stage or "")
    q1_parts = [p for p in (entity, project_name, kw, str(year) if year else "") if p]
    q2_parts = [p for p in (project_name, country) if p]
    queries: list[str] = []
    for parts in (q1_parts, q2_parts):
        q = " ".join(parts).strip()
        if q and q not in queries:
            queries.append(q)
    return queries
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_queries.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/queries.py s02e_source_finder/tests/test_queries.py
git commit -m "feat(s02e): 按事件查询构造（反塌缩）+ 测试"
```

---

## Task 4: search.py —— Serper 中文搜索（适配自 fill_links）

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/search.py`（本任务先写 `serper_search` + 限速器）
- Test: `s02e_source_finder/tests/test_search.py`（mock `requests.post`）

**Interfaces:**
- Produces: `SerperRateLimiter(calls_per_second=2.0)`；`serper_search(query, api_key, rate_limiter, *, session=requests) -> list[dict]`。结果项含 `title/link/snippet`；请求体带 `gl="cn"`、`hl="zh-cn"`。`session` 参数便于测试注入。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_search.py
from unittest.mock import MagicMock
from s02e_source_finder.search import serper_search, SerperRateLimiter

def test_serper_sends_chinese_params_and_parses_organic():
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"organic": [
        {"title": "标题", "link": "https://x.cn/a", "snippet": "摘要"},
    ]}
    fake_resp.raise_for_status.return_value = None
    fake_session = MagicMock()
    fake_session.post.return_value = fake_resp

    hits = serper_search("某项目 开工 2024", "KEY",
                         SerperRateLimiter(1000.0), session=fake_session)

    assert hits == [{"title": "标题", "link": "https://x.cn/a", "snippet": "摘要"}]
    body = fake_session.post.call_args.kwargs["json"]
    assert body["gl"] == "cn" and body["hl"] == "zh-cn"

def test_serper_returns_empty_on_error():
    fake_session = MagicMock()
    fake_session.post.side_effect = RuntimeError("boom")
    hits = serper_search("q", "KEY", SerperRateLimiter(1000.0), session=fake_session)
    assert hits == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_search.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现（适配 fill_links：加 gl/hl + session 注入 + logging）**

```python
# src/s02e_source_finder/search.py
"""Serper 中文搜索 + Gemini 按事件判定（适配自 external-tools/external-link-adder/fill_links.py）。"""
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

SERPER_URL = "https://google.serper.dev/search"
MAX_RESULTS = 5


class SerperRateLimiter:
    """全局 Serper 限速器（跨线程共享，≤ calls_per_second）。"""

    def __init__(self, calls_per_second: float = 2.0):
        self._interval = 1.0 / calls_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        with self._lock:
            wait = self._interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()


def serper_search(query: str, api_key: str, rate_limiter: SerperRateLimiter,
                  *, session=requests) -> list[dict]:
    """调 Serper（中文 gl/hl）返回 organic 结果（title/link/snippet）。失败记日志返回 []。"""
    rate_limiter.acquire()
    try:
        resp = session.post(
            SERPER_URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": MAX_RESULTS, "gl": "cn", "hl": "zh-cn"},
            timeout=15,
        )
        resp.raise_for_status()
        organic = resp.json().get("organic", [])
        return [
            {"title": r.get("title", ""), "link": r.get("link", ""), "snippet": r.get("snippet", "")}
            for r in organic[:MAX_RESULTS]
        ]
    except (requests.RequestException, ValueError, RuntimeError) as e:
        logger.error("Serper 查询失败 q=%r: %s", query, e)
        return []
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_search.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/search.py s02e_source_finder/tests/test_search.py
git commit -m "feat(s02e): Serper 中文搜索（gl=cn/hl=zh-cn）+ 测试"
```

---

## Task 5: search.py —— Gemini 按事件判定

**Files:**
- Modify: `s02e_source_finder/src/s02e_source_finder/search.py`（加 `judge_event_source`）
- Test: `s02e_source_finder/tests/test_search.py`（追加，mock Gemini client）

**Interfaces:**
- Produces: `judge_event_source(project_name, country, entity, stage, year, results, client, *, event_specific: bool) -> dict`。返回 `{"official": url|None, "non_official": url|None, "reason": str}`。prompt 硬规定：必须中文源、对得上目标里程碑（`event_specific=True`）才填，否则 null；`event_specific=False`（项目级兜底）放宽到"项目对得上即可"。

- [ ] **Step 1: 写失败测试（mock client 返回 JSON）**

```python
# tests/test_search.py （追加）
from unittest.mock import MagicMock
from s02e_source_finder.search import judge_event_source

def _client_returning(text):
    client = MagicMock()
    client.models.generate_content.return_value = MagicMock(text=text)
    return client

def test_judge_parses_official_and_drops_null_strings():
    client = _client_returning(
        '{"official": "https://ceec.cn/p", "non_official": "null", "reason": "官网"}')
    out = judge_event_source("某项目", "印度", "中国能建", "开工", 2024,
                             [{"title": "t", "link": "https://ceec.cn/p", "snippet": "s"}],
                             client, event_specific=True)
    assert out["official"] == "https://ceec.cn/p"
    assert out["non_official"] is None

def test_judge_returns_none_on_exhausted_retries():
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("down")
    out = judge_event_source("某项目", "印度", "中国能建", "开工", 2024,
                             [{"title": "t", "link": "x", "snippet": "s"}],
                             client, event_specific=True, max_retries=0)
    assert out == {"official": None, "non_official": None, "reason": out["reason"]}
    assert "失败" in out["reason"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_search.py -v`
Expected: FAIL（`ImportError: cannot import name 'judge_event_source'`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/search.py （追加）
import json
from google.genai import types

_SYS_EVENT = """你是可再生能源项目数据库专家，为项目的【某一个里程碑事件】找最权威的【中文】参考链接。
严格规则：
1. 只接受中文网站/中文报道；非中文一律 null。
2. official=实施企业官网或项目专属官网；non-official=可信第三方中文来源（行业媒体/政府公告/国际机构）。
3. 必须确认报道的就是【目标项目】；不确定是否同一项目则 null。
4. 目标里程碑模式下，还需阶段/时间对得上该里程碑，对不上则 null（不许拿项目通用文章凑）。
5. 新闻聚合站/维基/社交媒体不算可信。
只返回 JSON：{"official": "URL 或 null", "non_official": "URL 或 null", "reason": "简短中文说明"}"""

_SYS_PROJECT = _SYS_EVENT.replace(
    "4. 目标里程碑模式下，还需阶段/时间对得上该里程碑，对不上则 null（不许拿项目通用文章凑）。",
    "4. 项目级模式：只要确认是该项目的可信中文报道即可，不要求精确对上某个里程碑。")

GEMINI_MODEL = "gemini-2.5-flash-lite"


def _normalize_url(v):
    if isinstance(v, str) and v.lower() in {"null", "none", ""}:
        return None
    return v if isinstance(v, str) else None


def judge_event_source(project_name, country, entity, stage, year, results, client,
                       *, event_specific: bool, max_retries: int = 4) -> dict:
    """让 Gemini 从搜索结果挑该事件的中文源。event_specific 决定用"里程碑严判"还是"项目级宽判"。"""
    sys_prompt = _SYS_EVENT if event_specific else _SYS_PROJECT
    results_text = "\n".join(
        f"{i+1}. 标题:{r['title']}\n   链接:{r['link']}\n   摘要:{r['snippet']}"
        for i, r in enumerate(results))
    user = (f"目标项目:{project_name or 'N/A'}\n国家:{country or 'N/A'}\n"
            f"中方实施企业:{entity or 'N/A'}\n目标里程碑:{stage or 'N/A'} / {year or 'N/A'}\n\n"
            f"搜索结果:\n{results_text}")

    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[types.Content(role="user",
                          parts=[types.Part(text=sys_prompt + "\n\n" + user)])],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.1),
            )
            r = json.loads(resp.text)
            return {"official": _normalize_url(r.get("official")),
                    "non_official": _normalize_url(r.get("non_official")),
                    "reason": r.get("reason", "")}
        except (RuntimeError, ValueError, KeyError) as e:
            last_err = str(e)
            if attempt < max_retries:
                time.sleep(5.0 * (2 ** attempt))
    logger.error("Gemini 判定重试耗尽 project=%r: %s", project_name, last_err)
    return {"official": None, "non_official": None, "reason": f"Gemini 失败:{last_err[:120]}"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_search.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/search.py s02e_source_finder/tests/test_search.py
git commit -m "feat(s02e): Gemini 按事件判定（中文硬闸 + 里程碑/项目级两模式）+ 测试"
```

---

## Task 6: db.py —— 读目标事件 + 写链接

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/db.py`
- Test: `s02e_source_finder/tests/test_db.py`（用临时 sqlite，自建最小 schema）

**Interfaces:**
- Consumes: `gate.passes_s02e_gate`。
- Produces:
  - `load_targets(conn) -> list[dict]`：每项=`{canonical_id, project_name, country, events: [ev...]}`，只含通过 gate 的项目、且 events 只含**当前无链接**的事件；`ev={event_id, stage, year, source_link, entity}`。
  - `write_event_links(conn, event_id, official, non_official)`：把链接**追加去重**进该事件的两个 JSON 数组列（单事件一次，事务由调用方控制）。
  - `_year_of(ev_row) -> int|None`、`_first_link(json_str)` 等辅助。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_db.py
import json, sqlite3
import pytest
from s02e_source_finder.db import load_targets, write_event_links

_SCHEMA = """
CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, country TEXT,
    country_iso TEXT, category TEXT, excluded INTEGER DEFAULT 0);
CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
    publish_date TEXT, investment_date TEXT, planned_start_date TEXT, planned_complete_date TEXT,
    source_links TEXT, official_website_links TEXT, non_official_website_links TEXT);
CREATE TABLE chinese_entities (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, implementing_entity TEXT);
"""

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO canonical_projects VALUES ('IND-PV-0001','某光伏','印度','IND','光伏',0)")
    c.execute("""INSERT INTO project_events (id,canonical_id,project_stage,publish_date,source_links,
                 official_website_links,non_official_website_links)
                 VALUES ('IND-PV-0001-01','IND-PV-0001','开工','2024-03-01','["https://goalfore.cn/x"]',NULL,NULL)""")
    c.execute("INSERT INTO chinese_entities (event_id,implementing_entity) VALUES ('IND-PV-0001-01','中国能建')")
    # 已有链接的事件 → 应被跳过
    c.execute("""INSERT INTO project_events (id,canonical_id,project_stage,publish_date,
                 official_website_links) VALUES ('IND-PV-0001-02','IND-PV-0001','竣工','2026-01-01','["https://done.cn"]')""")
    # 被 gate 排除的项目（中国）
    c.execute("INSERT INTO canonical_projects VALUES ('CHN-PV-0001','国内','中国','CHN','光伏',0)")
    c.execute("INSERT INTO project_events (id,canonical_id,project_stage,publish_date) VALUES ('CHN-PV-0001-01','CHN-PV-0001','开工','2024-01-01')")
    c.commit()
    return c

def test_load_targets_filters_gate_and_existing_links(conn):
    targets = load_targets(conn)
    assert len(targets) == 1                                  # 只有 IND 项目
    proj = targets[0]
    assert proj["canonical_id"] == "IND-PV-0001"
    assert [e["event_id"] for e in proj["events"]] == ["IND-PV-0001-01"]  # 02 已有链接被跳过
    ev = proj["events"][0]
    assert ev["stage"] == "开工" and ev["year"] == 2024
    assert ev["entity"] == "中国能建"
    assert ev["source_link"] == "https://goalfore.cn/x"

def test_write_event_links_appends_dedup(conn):
    write_event_links(conn, "IND-PV-0001-01", "https://ceec.cn/p", "https://media.cn/n")
    conn.commit()
    row = conn.execute("SELECT official_website_links, non_official_website_links FROM project_events WHERE id='IND-PV-0001-01'").fetchone()
    assert json.loads(row[0]) == ["https://ceec.cn/p"]
    assert json.loads(row[1]) == ["https://media.cn/n"]
    # 再写相同 official → 去重不重复
    write_event_links(conn, "IND-PV-0001-01", "https://ceec.cn/p", None)
    conn.commit()
    row = conn.execute("SELECT official_website_links FROM project_events WHERE id='IND-PV-0001-01'").fetchone()
    assert json.loads(row[0]) == ["https://ceec.cn/p"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_db.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/db.py
"""主库读写：选目标事件（gate + 无链接）、按事件追加去重写链接。"""
import json
import logging
import sqlite3

from s02e_source_finder.gate import passes_s02e_gate

logger = logging.getLogger(__name__)


def _first_link(json_str) -> list[str]:
    try:
        return json.loads(json_str) if json_str else []
    except (json.JSONDecodeError, TypeError):
        return []


def _has_link(ev_row) -> bool:
    return bool(_first_link(ev_row["official_website_links"]) or _first_link(ev_row["non_official_website_links"]))


def _year_of(ev_row) -> int | None:
    for col in ("publish_date", "investment_date", "planned_complete_date", "planned_start_date"):
        v = ev_row[col] if col in ev_row.keys() else None
        if v and len(str(v)) >= 4 and str(v)[:4].isdigit():
            return int(str(v)[:4])
    return None


def load_targets(conn: sqlite3.Connection) -> list[dict]:
    """返回通过 gate 的项目，每项只含当前无链接的事件（按日期升序）。"""
    conn.row_factory = sqlite3.Row
    projects = conn.execute(
        "SELECT id, project_name, country, country_iso, category, excluded FROM canonical_projects"
    ).fetchall()
    targets: list[dict] = []
    for p in projects:
        evs = conn.execute(
            """SELECT * FROM project_events WHERE canonical_id = ?
               ORDER BY COALESCE(publish_date, investment_date, '9999')""", (p["id"],)
        ).fetchall()
        years = [_year_of(e) for e in evs]
        if not passes_s02e_gate(p["country_iso"], p["category"], p["excluded"], years):
            continue
        pending = []
        for e in evs:
            if _has_link(e):
                continue
            ent = conn.execute(
                "SELECT implementing_entity FROM chinese_entities WHERE event_id = ? ORDER BY id LIMIT 1",
                (e["id"],)).fetchone()
            srcs = _first_link(e["source_links"])
            pending.append({
                "event_id": e["id"], "stage": e["project_stage"] or "",
                "year": _year_of(e), "source_link": srcs[0] if srcs else "",
                "entity": (ent["implementing_entity"] if ent else "") or "",
            })
        if pending:
            targets.append({"canonical_id": p["id"], "project_name": p["project_name"] or "",
                            "country": p["country"] or "", "events": pending})
    return targets


def write_event_links(conn: sqlite3.Connection, event_id: str,
                      official: str | None, non_official: str | None) -> None:
    """把链接追加去重进该事件的两个 JSON 数组列（调用方负责事务/commit）。"""
    row = conn.execute(
        "SELECT official_website_links, non_official_website_links FROM project_events WHERE id = ?",
        (event_id,)).fetchone()
    off = _first_link(row["official_website_links"] if isinstance(row, sqlite3.Row) else row[0])
    noff = _first_link(row["non_official_website_links"] if isinstance(row, sqlite3.Row) else row[1])
    if official and official not in off:
        off.append(official)
    if non_official and non_official not in noff:
        noff.append(non_official)
    conn.execute(
        "UPDATE project_events SET official_website_links = ?, non_official_website_links = ? WHERE id = ?",
        (json.dumps(off, ensure_ascii=False), json.dumps(noff, ensure_ascii=False), event_id))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_db.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/db.py s02e_source_finder/tests/test_db.py
git commit -m "feat(s02e): 主库读目标事件 + 按事件追加去重写链接 + 测试"
```

---

## Task 7: runner.py —— 编排（逐事件 + 跨事件去重 + 兜底 + 熔断 + dry-run）

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/runner.py`
- Test: `s02e_source_finder/tests/test_runner.py`（mock search_fn / judge_fn，临时 sqlite）

**Interfaces:**
- Consumes: `queries.build_event_queries`、`db.load_targets`、`db.write_event_links`。
- Produces: `run_source_finder(conn, *, search_fn, judge_fn, dry_run=False, limit=None, max_events=3500) -> dict`（返回统计 `{processed, event_specific, fallback, not_found}`）。`search_fn(query)->list[dict]`、`judge_fn(proj, country, entity, stage, year, results, event_specific)->dict` 均可注入便于测试。**熔断**：`pending > max_events` 抛 `RuntimeError`。**跨事件去重**：同项目内已用 URL 不复用。**兜底(a)**：事件专属失败则项目级查询（`event_specific=False`），结果照存、统计计入 `fallback`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_runner.py
import json, sqlite3
import pytest
from s02e_source_finder.runner import run_source_finder
from tests.test_db import _SCHEMA   # 复用最小 schema

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO canonical_projects VALUES ('IND-PV-0001','某光伏','印度','IND','光伏',0)")
    for nn, stage, dt in (("01","签约","2023-05-01"), ("02","开工","2024-03-01")):
        c.execute("""INSERT INTO project_events (id,canonical_id,project_stage,publish_date,source_links)
                     VALUES (?,?,?,?,?)""",
                  (f"IND-PV-0001-{nn}","IND-PV-0001",stage,dt,'["https://goalfore.cn/x"]'))
    c.commit()
    return c

def test_circuit_breaker_aborts_when_pending_exceeds_cap(conn):
    with pytest.raises(RuntimeError, match="超过上限"):
        run_source_finder(conn, search_fn=lambda q: [], judge_fn=None, max_events=1)

def test_cross_event_dedup_and_per_event_write(conn):
    # search 永远有结果；judge 给签约/开工各返回不同官方链接，但若 URL 重复则被跨事件去重
    def judge_fn(proj, country, entity, stage, year, results, event_specific):
        url = "https://ceec.cn/" + stage
        return {"official": url, "non_official": None, "reason": "ok"}
    stats = run_source_finder(conn, search_fn=lambda q: [{"title":"t","link":"l","snippet":"s"}],
                              judge_fn=judge_fn)
    conn.commit()
    r1 = conn.execute("SELECT official_website_links FROM project_events WHERE id='IND-PV-0001-01'").fetchone()
    r2 = conn.execute("SELECT official_website_links FROM project_events WHERE id='IND-PV-0001-02'").fetchone()
    assert json.loads(r1[0]) == ["https://ceec.cn/签约"]
    assert json.loads(r2[0]) == ["https://ceec.cn/开工"]
    assert stats["processed"] == 2

def test_dry_run_writes_nothing(conn):
    run_source_finder(conn, search_fn=lambda q: [{"title":"t","link":"l","snippet":"s"}],
                      judge_fn=lambda *a, **k: {"official":"https://x.cn","non_official":None,"reason":""},
                      dry_run=True)
    conn.commit()
    row = conn.execute("SELECT official_website_links FROM project_events WHERE id='IND-PV-0001-01'").fetchone()
    assert row[0] is None

def test_fallback_counts_when_event_specific_fails(conn):
    # event_specific 调用返回空；project 级调用返回链接 → 计入 fallback
    def judge_fn(proj, country, entity, stage, year, results, event_specific):
        if event_specific:
            return {"official": None, "non_official": None, "reason": "no"}
        return {"official": None, "non_official": "https://media.cn/n", "reason": "proj"}
    stats = run_source_finder(conn, search_fn=lambda q: [{"title":"t","link":"l","snippet":"s"}],
                              judge_fn=judge_fn)
    assert stats["fallback"] == 2 and stats["event_specific"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_runner.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/runner.py
"""编排：逐事件搜索+判定，跨事件去重，事件专属失败则项目级兜底，dry-run/limit/熔断。"""
import logging
import sqlite3

from s02e_source_finder.db import load_targets, write_event_links
from s02e_source_finder.queries import build_event_queries

logger = logging.getLogger(__name__)


def _pick(judged: dict, source_link: str, used: set) -> dict:
    """剔除与原文同 URL、或同项目已用过的 URL。"""
    out = dict(judged)
    for k in ("official", "non_official"):
        u = out.get(k)
        if u and (u == source_link or u in used):
            out[k] = None
    return out


def run_source_finder(conn: sqlite3.Connection, *, search_fn, judge_fn,
                      dry_run: bool = False, limit: int | None = None,
                      max_events: int = 3500) -> dict:
    """主编排。search_fn(query)->results；judge_fn(proj,country,entity,stage,year,results,event_specific)->dict。"""
    targets = load_targets(conn)
    flat = [(p, ev) for p in targets for ev in p["events"]]
    if limit is not None:
        flat = flat[:limit]
    pending = len(flat)
    if pending > max_events:
        raise RuntimeError(
            f"待处理事件 {pending} 超过上限 {max_events}：疑似 bug 或数据增长，已中止。"
            f"确要继续请显式调大 max_events。")
    logger.info("s02e 待处理事件 %d（dry_run=%s）", pending, dry_run)

    stats = {"processed": 0, "event_specific": 0, "fallback": 0, "not_found": 0}
    used_by_project: dict[str, set] = {}

    for p, ev in flat:
        used = used_by_project.setdefault(p["canonical_id"], set())
        queries = build_event_queries(p["project_name"], ev["entity"], p["country"], ev["stage"], ev["year"])
        chosen, event_specific = None, False
        for qi, q in enumerate(queries):
            hits = search_fn(q)
            if not hits:
                continue
            is_specific = (qi == 0 and len(queries) > 1)  # 第1条是事件专属查询
            judged = judge_fn(p["project_name"], p["country"], ev["entity"], ev["stage"], ev["year"], hits, is_specific)
            judged = _pick(judged, ev["source_link"], used)
            if judged.get("official") or judged.get("non_official"):
                chosen, event_specific = judged, is_specific
                break

        stats["processed"] += 1
        if not chosen:
            stats["not_found"] += 1
            continue
        stats["event_specific" if event_specific else "fallback"] += 1
        for k in ("official", "non_official"):
            if chosen.get(k):
                used.add(chosen[k])
        logger.info("event=%s specific=%s off=%s noff=%s", ev["event_id"], event_specific,
                    chosen.get("official"), chosen.get("non_official"))
        if not dry_run:
            write_event_links(conn, ev["event_id"], chosen.get("official"), chosen.get("non_official"))
    return stats
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_runner.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/runner.py s02e_source_finder/tests/test_runner.py
git commit -m "feat(s02e): 编排器（逐事件+跨事件去重+兜底+熔断+dry-run）+ 测试"
```

---

## Task 8: main.py —— CLI 入口（GATED 真跑）

**Files:**
- Create: `s02e_source_finder/src/s02e_source_finder/main.py`
- Test: `s02e_source_finder/tests/test_main.py`（只测 Pydantic 入口校验，不真跑 API）

**Interfaces:**
- Consumes: `runner.run_source_finder`、`search.serper_search`/`judge_event_source`。
- Produces: `class RunConfig(BaseModel)`（`db_path`/`dry_run`/`limit`/`max_events`/`workers`）；`run()` 解析 argv 装配真实 `search_fn`/`judge_fn`（Serper key + Gemini client）调 runner，事务提交。`db_path` 默认 `../s02a_duplicate_merger/data/db/bri_re_database.db`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_main.py
import pytest
from pydantic import ValidationError
from s02e_source_finder.main import RunConfig

def test_config_defaults():
    cfg = RunConfig()
    assert cfg.max_events == 3500 and cfg.dry_run is False

def test_config_rejects_nonpositive_max_events():
    with pytest.raises(ValidationError):
        RunConfig(max_events=0)

def test_config_rejects_negative_limit():
    with pytest.raises(ValidationError):
        RunConfig(limit=-1)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s02e_source_finder run pytest tests/test_main.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写实现**

```python
# src/s02e_source_finder/main.py
"""s02e 入口：装配真实 Serper/Gemini，GATED 跑源查找。"""
import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from pydantic import BaseModel, Field, PositiveInt

_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_ROOT / ".env")
_DEFAULT_DB = str(Path(__file__).resolve().parents[2].parent
                  / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db")


class RunConfig(BaseModel):
    db_path: str = _DEFAULT_DB
    dry_run: bool = False
    limit: int | None = Field(default=None, ge=0)
    max_events: PositiveInt = 3500
    workers: PositiveInt = 4


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(_ROOT / "s02e_source_finder" / "s02e_run.log", encoding="utf-8"),
                  logging.StreamHandler()])


def run() -> None:
    """CLI 入口（脚本 run_source_finder）。"""
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底
    parser = argparse.ArgumentParser(description="s02e 中文外链查找")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-events", type=int, default=3500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--db", dest="db_path", default=_DEFAULT_DB)
    args = parser.parse_args()
    cfg = RunConfig(db_path=args.db_path, dry_run=args.dry_run, limit=args.limit,
                    max_events=args.max_events, workers=args.workers)

    _setup_logging()
    serper_key = os.getenv("SERPER_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not serper_key or not gemini_key:
        raise EnvironmentError("缺 SERPER_API_KEY 或 GEMINI_API_KEY（检查 .env）")

    # 延迟 import，避免测试 RunConfig 时拉起 runner/search 的重依赖
    from s02e_source_finder.runner import run_source_finder
    from s02e_source_finder.search import SerperRateLimiter, serper_search, judge_event_source

    client = genai.Client(api_key=gemini_key)
    limiter = SerperRateLimiter(2.0)
    search_fn = lambda q: serper_search(q, serper_key, limiter)
    judge_fn = lambda proj, country, entity, stage, year, results, event_specific: judge_event_source(
        proj, country, entity, stage, year, results, client, event_specific=event_specific)

    conn = sqlite3.connect(cfg.db_path)
    try:
        stats = run_source_finder(conn, search_fn=search_fn, judge_fn=judge_fn,
                                  dry_run=cfg.dry_run, limit=cfg.limit, max_events=cfg.max_events)
        if not cfg.dry_run:
            conn.commit()
        print(f"[s02e] 完成：{stats}")
    finally:
        conn.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s02e_source_finder run pytest tests/test_main.py tests/ -v`
Expected: PASS（全部，含前面任务）

- [ ] **Step 5: Commit**

```bash
git add s02e_source_finder/src/s02e_source_finder/main.py s02e_source_finder/tests/test_main.py
git commit -m "feat(s02e): CLI 入口 + Pydantic 入口校验 + 测试"
```

---

## Task 9: s03 flat_builder —— 新增 `event_links` 字段（P1-b 数据）

**Files:**
- Modify: `s03_data_exporter/src/s03_data_exporter/flat_builder.py`（事件循环里加 `event_links`；row dict 加字段）
- Test: `s03_data_exporter/tests/test_event_links.py`（新建）

**Interfaces:**
- Produces: `projects_flat.event_links` = JSON `[{stage_en, year, official, non_official}]`，每个**有链接**的事件一条。**不动** `stage_events` 与项目级聚合链接数组。

- [ ] **Step 1: 写失败测试**

```python
# s03_data_exporter/tests/test_event_links.py
import json
from s03_data_exporter.flat_builder import _build_event_links   # 新抽的小函数

def test_build_event_links_picks_per_event_and_skips_empty():
    proj_events = [
        {"project_stage": "签约", "publish_date": "2023-05-01",
         "official_website_links": '["https://ceec.cn/a"]', "non_official_website_links": "[]"},
        {"project_stage": "开工", "publish_date": "2024-03-01",
         "official_website_links": "[]", "non_official_website_links": '["https://media.cn/b"]'},
        {"project_stage": "竣工", "publish_date": "2026-01-01",
         "official_website_links": "[]", "non_official_website_links": "[]"},  # 无链接 → 跳过
    ]
    out = _build_event_links(proj_events)
    assert out == [
        {"stage_en": "Signed", "year": 2023, "official": "https://ceec.cn/a", "non_official": None},
        {"stage_en": "Construction", "year": 2024, "official": None, "non_official": "https://media.cn/b"},
    ]
```

> 注：`stage_en` 用 `enums.stage_en(...)`，"Signed"/"Construction" 为示意，按实际枚举英文为准；测试里改成实际返回值。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s03_data_exporter run pytest tests/test_event_links.py -v`
Expected: FAIL（`ImportError: _build_event_links`）

- [ ] **Step 3: 写实现（在 flat_builder.py 加小函数 + row dict 加字段）**

```python
# flat_builder.py 顶部辅助区新增（复用已 import 的 json / enums / _parse_year）
def _build_event_links(proj_events: list) -> list[dict]:
    """逐事件挑链接：每个有链接的事件 → {stage_en, year, official, non_official}（官方/非官方各取首个）。"""
    out = []
    for ev in proj_events:
        off = json.loads(ev.get("official_website_links") or "[]")
        noff = json.loads(ev.get("non_official_website_links") or "[]")
        if not off and not noff:
            continue
        stage = ev.get("project_stage") or ""
        out.append({
            "stage_en": enums.stage_en(stage) or stage,
            "year": _parse_year(ev.get("publish_date") or ev.get("investment_date")),
            "official": off[0] if off else None,
            "non_official": noff[0] if noff else None,
        })
    return out
```

在 row dict（约 335-380 行，紧挨 `non_official_website_links` 之后）加一行：

```python
                "event_links": json.dumps(_build_event_links(proj_events), ensure_ascii=False),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s03_data_exporter run pytest tests/test_event_links.py tests/ -v`
Expected: PASS（新测 + 既有 s03 测试全绿）

- [ ] **Step 5: Commit**

```bash
git add s03_data_exporter/src/s03_data_exporter/flat_builder.py s03_data_exporter/tests/test_event_links.py
git commit -m "feat(s03): projects_flat 新增 event_links（逐事件链接，P1-b）"
```

---

## Task 10: s04 ProjectDetail —— 逐事件展示（P1-b 前端）

**Files:**
- Modify: `s04_web_visualization/src/types/index.ts`（`Project.event_links?`）
- Modify: `s04_web_visualization/src/components/projects/ProjectDetail.tsx`（重写"外部链接" section）

**Interfaces:**
- Consumes: `projects_flat.event_links`（s03 产出）。
- Produces: ProjectDetail 中每个事件一行：英文阶段标签 + 链接（**官方优先，无官方才非官方**）。其它 section 不动。

- [ ] **Step 1: 加类型**

`src/types/index.ts` 的 `Project` 接口加：

```typescript
  event_links?: { stage_en: string; year: number | null; official: string | null; non_official: string | null }[];
```

- [ ] **Step 2: 重写 ProjectDetail 的"外部链接" section**

把现有 `{/* 外部链接 */}`（约 223-245 行）整段替换为：

```tsx
        {/* 外部链接：逐事件，官方优先 */}
        {(p.event_links?.length ?? 0) > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              {t("projectDetail.externalLinks")}
            </h4>
            <div className="bg-slate-50 rounded-xl p-3 text-xs space-y-2">
              {p.event_links!.map((e, i) => {
                const url = e.official ?? e.non_official;
                if (!url) return null;
                return (
                  <div key={i}>
                    <div className="text-slate-500 font-medium">
                      {e.stage_en}{e.year ? ` · ${e.year}` : ""}
                    </div>
                    <a href={url} target="_blank" rel="noopener noreferrer"
                       className="text-teal-700 hover:underline break-all">{url}</a>
                  </div>
                );
              })}
            </div>
          </section>
        )}
```

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd s04_web_visualization && npx tsc -b && npm run build`
Expected: 零报错，build 成功。

- [ ] **Step 4: Commit**

```bash
git add s04_web_visualization/src/types/index.ts s04_web_visualization/src/components/projects/ProjectDetail.tsx
git commit -m "feat(s04): ProjectDetail 逐事件展示外链（官方优先，P1-b）"
```

---

## Task 11: 文档同步 + 流水线挂载占位

**Files:**
- Modify: `docs/status/pipeline.md`、`docs/status/overview.md`（s02e 进模块表）
- Create: `docs/status/s02e.md`（新模块当前态，按统一 5 小节模板）
- Create: `docs/changes/2026-Www.md`（本次实施日志）

- [ ] **Step 1: 写 `docs/status/s02e.md`**

按 status 模板写：① 职责（Serper+Gemini 中文找源、按事件写链接、取代 s02c）② 当前功能（gate/queries/search/db/runner，P1=只写链接）③ 如何运行（`uv --project s02e_source_finder run run_source_finder --dry-run --limit 50`）④ 输入/输出（读主库 project_events，写 official/non_official_website_links）⑤ 依赖与约束（SERPER/GEMINI key、GATED、熔断 max_events）。

- [ ] **Step 2: overview.md / pipeline.md 模块表加 s02e 行；overview 把 s02c 标"被 s02e 取代"。**

- [ ] **Step 3: 追加 `docs/changes/2026-Www.md`**（日期·模块·做了什么·为什么·影响）。

- [ ] **Step 4: Commit**

```bash
git add docs/status/s02e.md docs/status/pipeline.md docs/status/overview.md docs/changes/
git commit -m "docs(s02e): P1 状态 + changes 日志"
```

---

## 上线流程（实施完成后，GATED 人值守，不在本计划自动跑）

1. `uv --project s02e_source_finder run run_source_finder --dry-run --limit 80`
2. 看 `s02e_run.log` + 抽查 ~30 条：重点"同名错配"、事件阶段/年份对没对上。
3. 据抽查调 `search.py` 的 Gemini prompt（`_SYS_EVENT`/`_SYS_PROJECT`），重跑小样。
4. 满意后去掉 `--limit` 全量（`--max-events` 熔断保护），真跑前 `.bak` 备份主库。
5. 跑完 `uv --project s03_data_exporter run run_export` 重导出 → s04 看 ProjectDetail 逐事件链接。

---

## Self-Review（写完核对）

- **Spec 覆盖**：模块形态(Task1) / 反塌缩按事件查询(Task3) / 中文 gl-hl+硬闸(Task4-5) / 宽松超集 gate(Task2) / 按事件读写+追加去重(Task6) / 跨事件去重+兜底(a)+熔断+dry-run(Task7) / GATED 入口+Pydantic(Task8) / P1-b event_links(Task9) + 逐事件展示官方优先(Task10) / 文档(Task11) —— 均有对应任务。**P2、重建重放对策、自动插事件均不在本计划（属 P2/backlog）**，符合"只做 P1"。
- **占位符**：无 TBD/TODO；唯一两处"按实际枚举英文/函数名为准"是对 `enums.stage_en` 真实返回值与 `bri_enums` 隐藏类访问器名的实施期确认（已注明），非逻辑占位。
- **类型一致**：`search_fn(query)->list[dict]`、`judge_fn(proj,country,entity,stage,year,results,event_specific)->{official,non_official,reason}`、`write_event_links(conn,event_id,official,non_official)`、`event_links:[{stage_en,year,official,non_official}]` 在 Task6/7/8/9/10 间一致。
