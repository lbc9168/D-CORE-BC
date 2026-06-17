# s02a 去重断点续跑 + 失败重跑（判断结果缓存）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 给 s02a 去重的 LLM 确认阶段加一个持久化缓存,使全量重建(及季度更新)可断点续跑、失败可重跑,从而绕开后台进程 ~10 分钟寿命限制,并在 60 并发下稳定完成。

**Architecture:** 判断结果(每对项目 same/not-same)互相独立,可分批跑、分批落盘;但 canonical 合并是全局 union-find,必须等所有对判完才组装。故方案 = **阶段1 可续跑的判断缓存(batch 并行 + 每批 commit + 失败重跑) → 阶段2 缓存判满后一次性组装+写库**。缓存按"项目内容哈希对称键"持久化到 sidecar SQLite,跨运行稳定。

**Tech Stack:** Python + sqlite3 + ThreadPoolExecutor + hashlib;TDD(pytest,LLM 全 mock)。

**约束:** `cache_db=None` 时行为与现状完全一致(现有 30 测试不变);P3 的去重逻辑(分组去 stage / Signal 6 / 跨阶段分摊)全部保留。

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/dedup_cache.py` | Create | 缓存单一职责:pair_key(对称内容哈希) + init/load/upsert |
| `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py` | Modify | 拆 `_llm_confirm`(抛错版+兼容版);`_deduplicate_projects` 加 `cache_db`;新增 `_confirm_llm_pairs`/`_run_confirm_batches`;常量 `DEDUP_BATCH_SIZE`、`DEDUP_WORKERS` 默认 60;`_run` 传 cache_db |
| `s02a_duplicate_merger/tests/test_dedup_cache.py` | Create | dedup_cache + 缓存集成 + 续跑/失败重跑 测试 |
| `scripts/oneoff/run_b3_rebuild.py` | Modify | 传 cache_db 路径;并发默认 60 |

**测试运行:** `uv --project s02a_duplicate_merger run pytest s02a_duplicate_merger/tests/ -q`(现有 30 + 新增,全绿)。

---

### Task 1：dedup_cache.py 模块(pair_key + 缓存读写)

**Files:** Create `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/dedup_cache.py`;Create `s02a_duplicate_merger/tests/test_dedup_cache.py`

- [ ] **Step 1：写失败测试**

```python
"""dedup_cache:对称内容哈希键 + 缓存读写。"""
import sys
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from s02a_duplicate_merger.tools import dedup_cache as dc


def _p(name, **kw):
    base = {"project_name": name, "country": "巴基斯坦", "category": "光伏",
            "capacity_mw": "100", "source_links": ["http://a"]}
    base.update(kw)
    return base


def test_pair_key_symmetric():
    a, b = _p("项目A"), _p("项目B", source_links=["http://b"])
    assert dc.pair_key(a, b) == dc.pair_key(b, a)        # 顺序无关


def test_pair_key_distinguishes_content():
    a, b, c = _p("项目A"), _p("项目B"), _p("项目C")
    assert dc.pair_key(a, b) != dc.pair_key(a, c)


def test_pair_key_stable_across_calls():
    a, b = _p("项目A"), _p("项目B")
    assert dc.pair_key(a, b) == dc.pair_key(dict(a), dict(b))


def test_init_load_upsert_roundtrip(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db)
    assert dc.load_cache(db) == {}
    dc.upsert(db, [("k1", 1, "done"), ("k2", 0, "done"), ("k3", None, "failed")])
    got = dc.load_cache(db)
    assert got["k1"] == (1, "done")
    assert got["k2"] == (0, "done")
    assert got["k3"] == (None, "failed")


def test_upsert_replaces(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db)
    dc.upsert(db, [("k1", None, "failed")])
    dc.upsert(db, [("k1", 1, "done")])     # 重跑失败项 → 覆盖为 done
    assert dc.load_cache(db)["k1"] == (1, "done")


def test_init_idempotent(tmp_path):
    db = str(tmp_path / "c.db")
    dc.init_cache(db); dc.init_cache(db)    # 二次不报错
    dc.upsert(db, [("k", 1, "done")])
    assert dc.load_cache(db)["k"] == (1, "done")
```

- [ ] **Step 2：跑确认失败** — `ModuleNotFoundError: dedup_cache`

- [ ] **Step 3：实现 dedup_cache.py**

```python
"""dedup_cache.py — 去重 LLM 判断结果的持久化缓存,支持断点续跑 + 失败重跑。

判断(每对项目是否同一)互相独立,故可分批落盘;键用两个项目的对称内容哈希,跨运行稳定。
"""
import hashlib
import sqlite3
from typing import Dict, List, Optional, Tuple


def _sig(p: dict) -> str:
    """单个项目的稳定内容签名(名/国/类/容量/排序信源)。"""
    links = "|".join(sorted(p.get("source_links") or []))
    return "‖".join([
        str(p.get("project_name", "")), str(p.get("country", "")),
        str(p.get("category", "")), str(p.get("capacity_mw", "")), links,
    ])


def pair_key(a: dict, b: dict) -> str:
    """两个项目的对称内容哈希键(与顺序无关),跨运行稳定。"""
    lo, hi = sorted([_sig(a), _sig(b)])
    return hashlib.sha1(f"{lo}❘{hi}".encode("utf-8")).hexdigest()


def init_cache(db_path: str) -> None:
    """建缓存表(幂等)。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS pair_verdicts ("
            "pair_key TEXT PRIMARY KEY, same INTEGER, status TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def load_cache(db_path: str) -> Dict[str, Tuple[Optional[int], str]]:
    """读全部缓存为 {pair_key: (same, status)}。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT pair_key, same, status FROM pair_verdicts").fetchall()
    finally:
        conn.close()
    return {k: (s, st) for k, s, st in rows}


def upsert(db_path: str, items: List[Tuple[str, Optional[int], str]]) -> None:
    """批量写入 (pair_key, same, status)(事务 + executemany,CLAUDE.md 规则3)。"""
    if not items:
        return
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO pair_verdicts (pair_key, same, status) VALUES (?,?,?)",
                items,
            )
    finally:
        conn.close()
```

- [ ] **Step 4：跑测试转绿**(6 passed)。提交。

```bash
git add s02a_duplicate_merger/src/s02a_duplicate_merger/tools/dedup_cache.py s02a_duplicate_merger/tests/test_dedup_cache.py
git commit -m "feat(s02a): dedup_cache 判断结果缓存模块（对称内容哈希键 + 读写）"
```

---

### Task 2：拆 `_llm_confirm` 为"抛错版 + 兼容版"(区分失败 vs 不同项目)

**Files:** Modify `duplicate_merger_tool.py`(`_llm_confirm` ~L669-727);Test 追加 `test_dedup_cache.py`

**为什么:** 现 `_llm_confirm` 内部吞异常返回 False,导致"调用失败"和"判定不同项目"无法区分。缓存要正确标 `failed`(供重跑),需底层确认在持久错误时**抛出**。

- [ ] **Step 1：写失败测试**(追加)

```python
import s02a_duplicate_merger.tools.duplicate_merger_tool as dmt


def test_llm_confirm_raising_propagates(monkeypatch):
    """_llm_confirm_raising:LLM 持久失败应抛出(供缓存标 failed)。"""
    def boom(*a, **k):
        raise RuntimeError("socket boom")
    monkeypatch.setattr(dmt, "_llm_call_with_retry", boom)
    import pytest
    with pytest.raises(RuntimeError):
        dmt._llm_confirm_raising(None, None, {"project_name": "A", "source_links": [], "location": "",
            "category": "光伏", "capacity_mw": "1", "project_stage": "在建"},
            {"project_name": "B", "source_links": [], "location": "", "category": "光伏",
             "capacity_mw": "1", "project_stage": "在建"})


def test_llm_confirm_wrapper_swallows(monkeypatch):
    """_llm_confirm:兼容版仍吞错返回 False(现有调用方行为不变)。"""
    monkeypatch.setattr(dmt, "_llm_confirm_raising",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert dmt._llm_confirm(None, None, {"project_name": "A"}, {"project_name": "B"}) is False
```

- [ ] **Step 2：跑确认失败** — `AttributeError: _llm_confirm_raising`

- [ ] **Step 3：实现** — 把现有 `_llm_confirm` 的函数体(去掉最外层 try/except)提为 `_llm_confirm_raising`,新 `_llm_confirm` 调它并吞错:

```python
def _llm_confirm_raising(
    client: genai.Client, config: types.GenerateContentConfig, a: Dict, b: Dict,
) -> bool:
    """确认两条记录是否同一物理项目(阶段可不同);LLM/解析持久失败时**抛出**(供缓存标 failed)。"""
    src_a = a["source_links"][0] if a["source_links"] else "未知"
    src_b = b["source_links"][0] if b["source_links"] else "未知"
    prompt = f"""..."""   # 原 _llm_confirm 的 prompt 原样搬过来
    resp = _llm_call_with_retry(
        client.models.generate_content,
        model=LLM_CONFIRM_MODEL, contents=prompt, config=config,
    )
    result = json.loads(resp.text)
    if isinstance(result, list):
        result = result[0]
    if result.get("same_project") is True and result.get("confidence") != "low":
        return True
    if result.get("same_project") is True and result.get("confidence") == "low":
        print(f"    → [低置信度,不合并] {result.get('reasoning', '')}")
    return False


def _llm_confirm(
    client: genai.Client, config: types.GenerateContentConfig, a: Dict, b: Dict,
) -> bool:
    """调用 Gemini 确认两条记录是否为同一物理项目(阶段可不同);**吞错返回 False**(兼容旧调用)。"""
    try:
        return _llm_confirm_raising(client, config, a, b)
    except Exception as e:
        print(f"    [LLM 确认失败] {e}")
        return False
```

> 注意:把原 `_llm_confirm` 里的 prompt 字符串完整搬进 `_llm_confirm_raising`,不要改一个字(包括 P3 放宽的"阶段可不同"措辞 + 判断依据第6条 + 已修的中文引号)。

- [ ] **Step 4：跑测试转绿 + 全量回归**(现有 30 + 新增全绿)。提交。

```bash
git add s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py s02a_duplicate_merger/tests/test_dedup_cache.py
git commit -m "refactor(s02a): _llm_confirm 拆抛错版+兼容版（区分调用失败 vs 不同项目，为缓存铺路）"
```

---

### Task 3：`_deduplicate_projects` 集成缓存(批量 + 续跑 + 失败重跑)

**Files:** Modify `duplicate_merger_tool.py`(常量区;`_deduplicate_projects` 签名与 llm_pairs 块;新增两辅助);Test 追加

- [ ] **Step 1：写失败测试**(追加)

```python
def _proj_full(name, stage="在建", **kw):
    base = {"record_type": "具体项目", "project_name": name, "project_name_en": "",
            "name_en_quality": "ai_translated", "country": "巴基斯坦", "country_iso": "PAK",
            "bri_country": "是", "ldc": "否", "location": "", "category": "光伏",
            "capacity_mw": "100", "capacity_details": "", "project_stage": stage,
            "project_developer": "", "planned_start_date": "", "planned_complete_date": "",
            "total_investment_usd": "", "investment_date": "", "financing_model": "",
            "financing_details": "", "source_links": [f"http://{name}"],
            "official_website_links": [], "non_official_website_links": [],
            "publish_date": "", "additional_remarks": "", "project_summary": "",
            "excluded": 0, "exclude_reason": "", "entities": []}
    base.update(kw)
    return base


def test_cache_skips_already_confirmed(monkeypatch, tmp_path):
    """已缓存 done 的对不再调 LLM;只对未缓存的调。"""
    a, b, c = _proj_full("古杜电站"), _proj_full("信德二期"), _proj_full("旁遮普三期")
    projects = [a, b, c]
    cands = [(0, 1, 60.0), (0, 2, 60.0)]   # 两对都 <97 走 LLM
    cache_db = str(tmp_path / "cache.db")
    import s02a_duplicate_merger.tools.dedup_cache as dc
    dc.init_cache(cache_db)
    dc.upsert(cache_db, [(dc.pair_key(a, b), 1, "done")])   # (0,1) 预置为 same
    calls = []
    monkeypatch.setattr(dmt, "_llm_confirm_raising",
                        lambda cl, cf, x, y: calls.append((x["project_name"], y["project_name"])) or False)
    merged = dmt._deduplicate_projects(object(), None, projects, cands, cache_db=cache_db)
    # 只对未缓存的 (0,2) 调了一次;(0,1) 用缓存
    assert len(calls) == 1
    # (0,1)=same → a,b 合并 → 结果含 2 条(合并体 + c)
    assert len(merged) == 2


def test_cache_persists_and_retries_failed(monkeypatch, tmp_path):
    """确认结果落缓存;首轮失败的对在失败重跑轮被再调一次。"""
    a, b = _proj_full("古杜电站"), _proj_full("信德二期")
    projects = [a, b]
    cands = [(0, 1, 60.0)]
    cache_db = str(tmp_path / "cache.db")
    seq = {"n": 0}
    def flaky(cl, cf, x, y):
        seq["n"] += 1
        if seq["n"] == 1:
            raise RuntimeError("socket boom")   # 首轮失败
        return True                              # 重跑成功 → same
    monkeypatch.setattr(dmt, "_llm_confirm_raising", flaky)
    merged = dmt._deduplicate_projects(object(), None, projects, cands, cache_db=cache_db)
    assert seq["n"] == 2                          # 首轮 + 失败重跑
    import s02a_duplicate_merger.tools.dedup_cache as dc
    assert dc.load_cache(cache_db)[dc.pair_key(a, b)] == (1, "done")
    assert len(merged) == 1                        # 最终合并


def test_no_cache_db_unchanged(monkeypatch):
    """cache_db=None:行为同现状(不建缓存,直接并行确认)。"""
    a, b = _proj_full("古杜电站"), _proj_full("信德二期")
    monkeypatch.setattr(dmt, "_llm_confirm", lambda cl, cf, x, y: True)
    merged = dmt._deduplicate_projects(object(), None, [a, b], [(0, 1, 60.0)])
    assert len(merged) == 1
```

- [ ] **Step 2：跑确认失败**(`cache_db` 参数不存在 / 行为不符)

- [ ] **Step 3：实现 — 常量**

常量区(DEDUP_WORKERS 一带)加:

```python
DEDUP_BATCH_SIZE = int(os.getenv("S02A_DEDUP_BATCH", "500"))  # 每批确认对数,每批 commit 缓存
```

(DEDUP_WORKERS 默认值在 Task 4 由 runner 设 60;此处保持 `os.getenv("S02A_DEDUP_WORKERS", "3")` 不变。)

- [ ] **Step 4：实现 — 两个辅助函数**(放在 `_deduplicate_projects` 之前)

```python
def _run_confirm_batches(client, config, projects, todo, cache_db, cache) -> None:
    """分批并行确认 todo=[(pi,pj,key)];每批 commit 缓存并回填内存 cache(断点续跑核心)。"""
    for start in range(0, len(todo), DEDUP_BATCH_SIZE):
        batch = todo[start:start + DEDUP_BATCH_SIZE]

        def _confirm(item):
            pi, pj, k = item
            try:
                same = _llm_confirm_raising(client, config, projects[pi], projects[pj])
                return k, (1 if same else 0), "done"
            except Exception as e:  # 持久失败(socket 等)→ 标 failed 待重跑
                logging.error("确认失败 pair_key=%s: %s", k, e)
                return k, None, "failed"

        rows = []
        with ThreadPoolExecutor(max_workers=DEDUP_WORKERS) as exe:
            for k, same, status in exe.map(_confirm, batch):
                rows.append((k, same, status))
                cache[k] = (same, status)
        if cache_db:
            dedup_cache.upsert(cache_db, rows)
        print(f"    批次 {start // DEDUP_BATCH_SIZE + 1}:确认 {len(batch)} 对"
              f"(累计 {min(start + len(batch), len(todo))}/{len(todo)})")


def _confirm_llm_pairs(client, config, projects, llm_pairs, cache_db) -> Dict[Tuple[int, int], bool]:
    """对 llm_pairs 做可缓存/可续跑/失败重跑的确认,返回 {(pi,pj): same_bool}。"""
    keyed = [(pi, pj, dedup_cache.pair_key(projects[pi], projects[pj])) for pi, pj in llm_pairs]
    cache: Dict[str, Tuple] = {}
    if cache_db:
        dedup_cache.init_cache(cache_db)
        cache = dedup_cache.load_cache(cache_db)

    todo = [(pi, pj, k) for pi, pj, k in keyed if cache.get(k, (None, "none"))[1] != "done"]
    print(f"  LLM 核验 {len(llm_pairs)} 对（已缓存 {len(llm_pairs) - len(todo)} / 待确认 {len(todo)}"
          f" / workers={DEDUP_WORKERS} / batch={DEDUP_BATCH_SIZE}）…")
    _run_confirm_batches(client, config, projects, todo, cache_db, cache)

    failed = [(pi, pj, k) for pi, pj, k in keyed if cache.get(k, (None, "none"))[1] == "failed"]
    if failed:
        print(f"  失败重跑 {len(failed)} 对…")
        _run_confirm_batches(client, config, projects, failed, cache_db, cache)

    still = sum(1 for _, _, k in keyed if cache.get(k, (None, "none"))[1] == "failed")
    if still:
        logging.warning("仍有 %d 对确认失败,按"不合并"处理(缓存保留 failed,将来重跑可补)", still)

    out: Dict[Tuple[int, int], bool] = {}
    for pi, pj, k in keyed:
        same, status = cache.get(k, (None, "none"))
        out[(pi, pj)] = (status == "done" and bool(same))
    return out
```

- [ ] **Step 5：实现 — `_deduplicate_projects` 签名 + llm_pairs 块**

签名加 `cache_db: Optional[str] = None`;docstring 补"可选缓存续跑"。把现有 `if llm_pairs:` 整块(L1007-1027,含 `_confirm_pair`/ThreadPoolExecutor)替换为:

```python
    if llm_pairs:
        verdicts = _confirm_llm_pairs(client, config, projects, llm_pairs, cache_db)
        for (pi, pj), same in verdicts.items():
            if same:
                union(pi, pj)
```

(auto_pairs 块、union-find、组装块全部不动。)模块顶部 import 区加 `from s02a_duplicate_merger.tools import dedup_cache`(或 `from . import dedup_cache`,与现有 import 风格一致——本文件用绝对包名)。

- [ ] **Step 6：跑测试转绿 + 全量回归**

`uv --project s02a_duplicate_merger run pytest s02a_duplicate_merger/tests/ -q` → 现有 30 + Task1/2/3 新增全绿。重点确认 `test_no_cache_db_unchanged` + 原 `test_dedup_grouping` 那批仍绿(cache_db=None 路径不变)。

- [ ] **Step 7：Commit**

```bash
git add s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py s02a_duplicate_merger/tests/test_dedup_cache.py
git commit -m "feat(s02a): _deduplicate_projects 集成判断缓存——批量确认+每批落盘+断点续跑+失败重跑 (cache_db 可选)"
```

---

### Task 4：`_run` 接缓存 + 并发默认 60 + runner

**Files:** Modify `duplicate_merger_tool.py`(`_run` 调 `_deduplicate_projects` 处 + `DuplicateMergerInput`/签名加 cache_db);`scripts/oneoff/run_b3_rebuild.py`

- [ ] **Step 1：`_run` 传 cache_db**

`DuplicateMergerTool._run` 签名加 `cache_db: Optional[str] = None`;调 `_deduplicate_projects(client, gen_config, projects, candidates)` 改为传 `cache_db=cache_db`。`cache_db` 默认 None(不破坏现有流水线/测试);rebuild 由 runner 显式传。

- [ ] **Step 2：runner 设并发 60 + cache 路径**

`run_b3_rebuild.py`:`S02A_DEDUP_WORKERS` 默认改 `"60"`;新增 `CACHE_DB = str(_root / "s02a_duplicate_merger" / "data" / "db" / "dedup_cache.db")`;调 `_run(input_db=INPUT_DB, db_path=DB_PATH, cache_db=CACHE_DB)`;打印 CACHE_DB 路径。

- [ ] **Step 3：smoke + 全量回归**

`uv --project s02a_duplicate_merger run pytest s02a_duplicate_merger/tests/ -q`(全绿);
`uv --project s02a_duplicate_merger run python -c "import s02a_duplicate_merger.tools.duplicate_merger_tool"`(导入通过)。

- [ ] **Step 4：Commit**

```bash
git add s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py scripts/oneoff/run_b3_rebuild.py
git commit -m "feat(s02a): _run 接 cache_db 续跑 + rebuild runner 并发 60 + dedup_cache.db 路径"
```

---

### Task 5：用可靠方法重跑 B3(执行,非编码)

- [ ] **Step 1：确认 projects.db 干净**(imported_to_s02a=0 全量、excluded=1736)。
- [ ] **Step 2：detached 启动 rebuild**(`Start-Process`,脱离 10 分钟限制)+ 重定向日志。
- [ ] **Step 3：每 ~10 分钟监控**(报用户):缓存进度 `SELECT status,COUNT(*) FROM pair_verdicts`、10048 计数(应≈0)、是否进入写库/完成;**进程若死则直接重起,缓存自动续跑**。
- [ ] **Step 4：完成后验证**:canonical/events/entities 条数;为 B6 防误并证据(雪兰莪、达瓦德密、抽样)做准备。

---

## 验证清单(DoD)

- [ ] dedup_cache:对称键 + 读写 + 幂等;单测绿。
- [ ] `_llm_confirm` 拆分:抛错版抛、兼容版吞;现有调用行为不变。
- [ ] `_deduplicate_projects(cache_db=...)`:跳过已缓存、每批落盘、失败重跑;`cache_db=None` 行为不变;现有 30 测试全绿。
- [ ] rebuild:60 并发 + dedup_cache.db;杀掉重起能从缓存续跑(实跑验证)。
- [ ] B3 跑完产出 worktree bri_re_database.db;主工作区已发布库未动。

## 风险

- 缓存键碰撞:sha1 内容哈希,碰撞概率可忽略。
- 组装/写库阶段若中断:确认已全缓存(秒读),删半成品 bri_re_database.db 重起即可(便宜)。
- 写库阶段自身的 canonical 确认 LLM(顺序、未缓存):从空库全局去重后多为建新,量小;若实测慢再单独优化(本计划不含)。
