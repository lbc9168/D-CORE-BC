# Developer 名称登记册与归一化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 developer 名称登记册（CN/EN 规范名+别名），把 2B-3 归一从"每轮 LLM 重判"改为"登记册确定性查表 + LLM 降级为补录建议"，并让 en_backfill 以登记册为最高优先来源填齐 `project_developer_en`（现 61% 空）。

**Architecture:** 数据文件 `developer_registry.json` 在仓库根目录（单一可信源）；查表引擎 `company_lookup.py` 在 s02b 包内（2B-3 与 2B-4 共同消费）；引擎三层查找 registry→hierarchy(只读复用)→透传+miss；EN 单一写入方=en_backfill。权威口径见 spec `docs/superpowers/specs/2026-07-07-developer-registry-design.md`（D1-D6）。

**Tech Stack:** Python + sqlite3 + Pydantic（loader 校验）+ 现有 s02b 工具链（developer_normalizer / en_backfill / db_backup）。

## Global Constraints（每个任务隐含遵守）

- 真库绝对路径 `E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db`（worktree 无数据副本）；动库一律 dry-run→.bak(WAL 安全 sqlite3 backup API+三重断言)→单事务→幂等自检；`PRAGMA busy_timeout=30000`（有并行会话写库）
- 根目录测试/CLI 一律 `uv run --no-sync`；**禁止 uv add / uv sync**（根 venv 有未声明 s01b editable）；s02b 内 `cd s02b_entity_normalizer && uv run --no-sync pytest -q`
- **git 提交只用显式路径** `git add <file> && git commit -m "..." -- <file...>`；工作区有并行会话未提交文件，绝不捎带；禁止 `git add -A`
- D2：同一法人写法变体才归一，**母子公司永不合并**；`parent_cn` 仅标注
- D1：就地改写 `project_developer`；**不加任何新列**；2B-3 只写 CN，EN 唯一写入方=en_backfill
- 登记册别名全册唯一，载入冲突即抛错；campaign 产物入册前必须过用户复核 gate
- 中企部件复用 `supporting_docs/company_hierarchy_v2.json`（**只读**，映射到该实体自身规范名，绝不上卷母公司）
- 补录建议/miss/would-change 等 CSV 一律 utf-8-sig、供人工复看，不提交 git

---

### Task 1: 登记册数据文件 + 根级数据契约测试

**Files:**
- Create: `developer_registry.json`（根目录）
- Test: `tests/test_developer_registry.py`（根目录，纯 json+assert，风格照 `tests/test_enums_consistency.py`）

**Interfaces:**
- Produces: `developer_registry.json` 顶层结构 `{"_meta": {...}, "companies": [...]}`，company 字段：`canonical_cn`(必填非空) / `canonical_en`(必填非空) / `aliases`(list，可空) / `is_chinese`(bool) / `parent_cn`(str|null) / `note`(可选) / `source`(str)

- [ ] **Step 1: 写空壳数据文件**

```json
{
  "_meta": {
    "version": 1,
    "generated": "2026-07-07",
    "source": "bootstrap（种子 campaign 前的空壳，仅含金样例锚点）",
    "notes": "developer 名称登记册。规则见 docs/superpowers/specs/2026-07-07-developer-registry-design.md。alias 全册唯一；canonical_cn/canonical_en 必填；中企原则上走 company_hierarchy_v2 不入本册。"
  },
  "companies": [
    {
      "canonical_cn": "阿布扎比未来能源公司",
      "canonical_en": "Masdar",
      "aliases": ["Masdar", "马斯达尔", "Abu Dhabi Future Energy Company", "马斯达尔公司"],
      "is_chinese": false,
      "parent_cn": null,
      "note": "金样例锚点：跨语言变体归一的验收案例",
      "source": "bootstrap_2026-07-07"
    }
  ]
}
```

- [ ] **Step 2: 写失败的契约测试**

```python
"""developer_registry.json 数据契约测试：结构、必填、别名全册唯一。"""
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = _ROOT / "developer_registry.json"


def _load():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def test_registry_file_exists_and_wellformed():
    data = _load()
    assert set(data.keys()) == {"_meta", "companies"}
    assert data["_meta"]["version"] == 1


def test_companies_required_fields():
    for c in _load()["companies"]:
        assert c["canonical_cn"].strip(), c
        assert c["canonical_en"].strip(), c
        assert isinstance(c["aliases"], list), c
        assert isinstance(c["is_chinese"], bool), c
        assert "parent_cn" in c, c


def test_aliases_unique_across_registry():
    seen = {}
    for c in _load()["companies"]:
        # canonical_cn 本身视作自身 alias 参与唯一性
        for name in [c["canonical_cn"], *c["aliases"]]:
            key = name.strip()
            assert key not in seen, f"别名冲突: {key!r} 同时属于 {seen.get(key)} 与 {c['canonical_cn']}"
            seen[key] = c["canonical_cn"]
```

- [ ] **Step 3: 跑测试确认通过**（文件先写好则直接绿——本任务契约先行，无实现代码）

Run: `uv run --no-sync pytest tests/test_developer_registry.py -q`
Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add developer_registry.json tests/test_developer_registry.py
git commit -m "feat(registry): developer_registry.json 空壳+数据契约测试（D1-D6 spec Task1）" -- developer_registry.json tests/test_developer_registry.py
```

---

### Task 2: s02b 查表引擎 company_lookup.py（三层查找 + hierarchy 只读复用）

**Files:**
- Create: `s02b_entity_normalizer/src/s02b_entity_normalizer/company_lookup.py`
- Test: `s02b_entity_normalizer/tests/test_company_lookup.py`

**Interfaces:**
- Consumes: Task 1 的 `developer_registry.json`；既有 `supporting_docs/company_hierarchy_v2.json`（结构：`{"groups": [{"canonical_name_cn", "is_central_soe", "aliases": [...], "subsidiaries": [{"name_cn", "legal_full_name_cn", 可选 "aliases"}]}]}`）
- Produces（Task 5/6 依赖，签名精确）:
  - `@dataclass(frozen=True) LookupResult(canonical_cn: str, canonical_en: Optional[str], matched: bool, source: str)`，source ∈ {"registry","hierarchy","passthrough"}
  - `build_index(registry_path: Optional[str] = None, hierarchy_path: Optional[str] = None) -> LookupIndex`（缺省用根目录两文件；Pydantic 校验 registry；别名冲突抛 `ValueError`）
  - `lookup(part: str, index: LookupIndex) -> LookupResult`
  - `collect_misses(index: LookupIndex) -> list[str]`（本进程内 lookup 过且透传的部件，去重保序，供 miss CSV）

- [ ] **Step 1: 写失败测试（夹具内嵌小型 registry+hierarchy，另加真实文件两条锚点断言）**

```python
"""company_lookup 三层查表引擎测试。"""
import json
import pytest
from s02b_entity_normalizer.company_lookup import build_index, lookup

FIXTURE_REGISTRY = {
    "_meta": {"version": 1, "generated": "2026-01-01", "source": "test"},
    "companies": [
        {"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
         "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"},
        {"canonical_cn": "Recurrent Energy", "canonical_en": "Recurrent Energy",
         "aliases": [], "is_chinese": True, "parent_cn": "阿特斯阳光电力集团股份有限公司", "source": "t"},
    ],
}
FIXTURE_HIERARCHY = {
    "schema_version": 2, "generated_on": "2026-01-01", "notes": "", "groups": [
        {"canonical_name_cn": "中国电力建设集团有限公司", "is_central_soe": True,
         "aliases": ["中国电建"], "subsidiaries": [
            {"name_cn": "中国电建集团海外投资有限公司",
             "legal_full_name_cn": "中国电建集团海外投资有限公司",
             "aliases": ["中国能源海外投资有限公司", "电建海投"]}]},
    ],
}


@pytest.fixture()
def index(tmp_path):
    rp = tmp_path / "reg.json"; rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    hp = tmp_path / "hier.json"; hp.write_text(json.dumps(FIXTURE_HIERARCHY, ensure_ascii=False), encoding="utf-8")
    return build_index(str(rp), str(hp))


def test_registry_exact_and_alias_cross_language(index):
    for name in ["阿布扎比未来能源公司", "Masdar", "马斯达尔"]:
        r = lookup(name, index)
        assert (r.canonical_cn, r.canonical_en, r.source) == ("阿布扎比未来能源公司", "Masdar", "registry")


def test_hierarchy_alias_maps_to_subsidiary_not_parent(index):
    r = lookup("中国能源海外投资有限公司", index)
    assert r.canonical_cn == "中国电建集团海外投资有限公司"   # 讹变→规范子公司名
    assert r.canonical_cn != "中国电力建设集团有限公司"        # 绝不上卷母公司
    assert r.source == "hierarchy" and r.canonical_en is None  # hierarchy 无 EN，交缓存


def test_hierarchy_group_alias_maps_to_group_itself(index):
    r = lookup("中国电建", index)
    assert r.canonical_cn == "中国电力建设集团有限公司" and r.source == "hierarchy"


def test_passthrough_unknown(index):
    r = lookup("某某不存在公司", index)
    assert r.matched is False and r.canonical_cn == "某某不存在公司" and r.source == "passthrough"


def test_ascii_part_hits_registry_case_sensitive_exact(index):
    assert lookup("Recurrent Energy", index).canonical_en == "Recurrent Energy"


def test_alias_conflict_raises(tmp_path):
    bad = json.loads(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False))
    bad["companies"].append({"canonical_cn": "另一家", "canonical_en": "X",
                             "aliases": ["Masdar"], "is_chinese": False, "parent_cn": None, "source": "t"})
    rp = tmp_path / "bad.json"; rp.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        build_index(str(rp), None)


def test_real_files_anchor():
    """真实文件锚点：Masdar 在册（Task1 bootstrap）；电建海投讹变在 hierarchy 可解。"""
    idx = build_index()  # 缺省路径=根目录真实文件
    assert lookup("Masdar", idx).canonical_cn == "阿布扎比未来能源公司"
    r = lookup("中国能源海外投资有限公司", idx)
    assert r.matched and "海外投资" in r.canonical_cn
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd s02b_entity_normalizer && uv run --no-sync pytest tests/test_company_lookup.py -q`
Expected: FAIL（ModuleNotFoundError: company_lookup）

- [ ] **Step 3: 实现引擎**

```python
"""company_lookup —— developer 名称三层查表引擎：registry → hierarchy(只读) → 透传+miss。

数据文件：根目录 developer_registry.json（本引擎唯一可写建议来源，但引擎本身零写盘）；
supporting_docs/company_hierarchy_v2.json 只读复用（别名→该实体自身规范名，绝不上卷母公司，spec D2）。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_REGISTRY = _ROOT / "developer_registry.json"
DEFAULT_HIERARCHY = _ROOT / "supporting_docs" / "company_hierarchy_v2.json"
_CJK_RE = re.compile(r"[一-鿿]")


class _Company(BaseModel):
    """登记册单条：canonical_cn/en 必填非空，aliases 列表。"""
    canonical_cn: str
    canonical_en: str
    aliases: List[str] = []
    is_chinese: bool
    parent_cn: Optional[str] = None
    note: Optional[str] = None
    source: str = ""

    @field_validator("canonical_cn", "canonical_en")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("canonical 名不得为空")
        return v.strip()


@dataclass(frozen=True)
class LookupResult:
    canonical_cn: str
    canonical_en: Optional[str]
    matched: bool
    source: str  # registry | hierarchy | passthrough


@dataclass
class LookupIndex:
    registry_map: Dict[str, tuple]        # 名/别名 → (canonical_cn, canonical_en)
    hierarchy_map: Dict[str, str]         # 名/别名 → 该实体自身规范名（不上卷）
    misses: List[str] = field(default_factory=list)
    _seen_miss: set = field(default_factory=set)


def _load_registry(path: Path) -> List[_Company]:
    """载入并 Pydantic 校验登记册（编程规则#6：外部 JSON 进业务前必须校验）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [_Company(**c) for c in data["companies"]]


def build_index(registry_path: Optional[str] = None,
                hierarchy_path: Optional[str] = None) -> LookupIndex:
    """构建两层索引；registry 别名全册冲突抛 ValueError；hierarchy 缺失时降级只用 registry。"""
    reg_map: Dict[str, tuple] = {}
    for c in _load_registry(Path(registry_path) if registry_path else DEFAULT_REGISTRY):
        for name in [c.canonical_cn, *c.aliases]:
            key = name.strip()
            if key in reg_map and reg_map[key][0] != c.canonical_cn:
                raise ValueError(f"registry 别名冲突: {key!r} → {reg_map[key][0]} / {c.canonical_cn}")
            reg_map[key] = (c.canonical_cn, c.canonical_en)

    hier_map: Dict[str, str] = {}
    hpath = Path(hierarchy_path) if hierarchy_path else DEFAULT_HIERARCHY
    if hierarchy_path is None and not hpath.exists():
        logger.warning("hierarchy 文件缺失 %s，降级仅 registry 查表", hpath)
    elif hierarchy_path is not None or hpath.exists():
        groups = json.loads(hpath.read_text(encoding="utf-8"))["groups"]
        for g in groups:
            canon = g["canonical_name_cn"].strip()
            for a in g.get("aliases", []) or []:
                hier_map.setdefault(a.strip(), canon)  # 集团别名→集团自身
            for sub in g.get("subsidiaries", []) or []:
                sub_canon = sub["name_cn"].strip()
                variants = [sub.get("legal_full_name_cn", ""), *(sub.get("aliases", []) or [])]
                for v in variants:
                    v = (v or "").strip()
                    if v and v != sub_canon:
                        hier_map.setdefault(v, sub_canon)  # 变体→子公司自身，绝不上卷
    return LookupIndex(registry_map=reg_map, hierarchy_map=hier_map)


def lookup(part: str, index: LookupIndex) -> LookupResult:
    """单部件三层查找。未命中记 miss（进程内去重保序）。"""
    key = (part or "").strip()
    if not key:
        return LookupResult(part, None, False, "passthrough")
    if key in index.registry_map:
        cn, en = index.registry_map[key]
        return LookupResult(cn, en, True, "registry")
    if _CJK_RE.search(key) and key in index.hierarchy_map:
        return LookupResult(index.hierarchy_map[key], None, True, "hierarchy")
    if key not in index._seen_miss:
        index._seen_miss.add(key)
        index.misses.append(key)
    return LookupResult(key, None, False, "passthrough")


def collect_misses(index: LookupIndex) -> List[str]:
    """返回本进程内透传过的部件（去重保序），供 miss CSV。"""
    return list(index.misses)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd s02b_entity_normalizer && uv run --no-sync pytest tests/test_company_lookup.py -q`
Expected: 8 passed
注意：`test_hierarchy_alias_maps_to_subsidiary_not_parent` 用夹具必过；`test_real_files_anchor` 依赖真实 hierarchy 里讹变条目的实际挂法——若真实文件中"中国能源海外投资有限公司"不在 subsidiary aliases 而在别处，**调整引擎读取而不是删断言**（该讹变 121 次是设计的验收锚点），实在无处可解则 NEEDS_CONTEXT 上报。

- [ ] **Step 5: s02b 全量回归 + Commit**

Run: `cd s02b_entity_normalizer && uv run --no-sync pytest -q`
Expected: 全绿（基线 18+ 新增 8）

```bash
git add s02b_entity_normalizer/src/s02b_entity_normalizer/company_lookup.py s02b_entity_normalizer/tests/test_company_lookup.py
git commit -m "feat(s02b): company_lookup 三层查表引擎（registry→hierarchy只读→透传+miss）" -- s02b_entity_normalizer/src/s02b_entity_normalizer/company_lookup.py s02b_entity_normalizer/tests/test_company_lookup.py
```

---

### Task 3: 种子导出 CLI（campaign 输入）

**Files:**
- Create: `s02b_entity_normalizer/src/s02b_entity_normalizer/tools/developer_seed_export.py`
- Test: `s02b_entity_normalizer/tests/test_developer_seed_export.py`

**Interfaces:**
- Consumes: Task 2 `build_index/lookup`（已在册/hierarchy 可解的部件要排除出种子——别浪费 campaign 名额）
- Produces: `export_seed(db_path: str, min_freq: int = 5, out_csv: Optional[str] = None) -> list[dict]` + argparse `main()`（`--db` 默认真库绝对路径 / `--min-freq 5` / `--out` 默认 `supporting_docs/developer_seed_<YYYYMMDD_HHMMSS>.csv`）
- CSV 列：`part / freq / sample_projects(最多3个 canonical_id+项目名) / current_en_cache(命中翻译缓存的现值) / is_ascii / suggested_action(空,campaign填)`

- [ ] **Step 1: 写失败测试（临时库夹具：3 个 developer 值含 JV 串/低频/已在册名）**

```python
"""developer_seed_export：频次统计、拆部件、排除已可解、CSV 产出。"""
import sqlite3
from s02b_entity_normalizer.tools.developer_seed_export import export_seed


def _mk_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, excluded INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, project_developer TEXT)")
    conn.execute("INSERT INTO canonical_projects VALUES ('X-PV-0001','甲项目',0)")
    rows = [("X-PV-0001-%02d" % i, "X-PV-0001", "ACWA Power; 未知小公司") for i in range(1, 7)]
    rows += [("X-PV-0001-%02d" % i, "X-PV-0001", "Masdar") for i in range(7, 13)]  # 已在册→排除
    rows += [("X-PV-0001-13", "X-PV-0001", "只出现一次公司")]                      # 低频→排除
    conn.executemany("INSERT INTO project_events VALUES (?,?,?)", rows)
    conn.commit(); conn.close()
    return str(db)


def test_seed_export_filters_and_counts(tmp_path):
    out = export_seed(_mk_db(tmp_path), min_freq=5, out_csv=str(tmp_path / "seed.csv"))
    parts = {r["part"]: r for r in out}
    assert "ACWA Power" in parts and parts["ACWA Power"]["freq"] == 6
    assert "未知小公司" in parts
    assert "Masdar" not in parts          # registry 已可解，不进种子
    assert "只出现一次公司" not in parts   # 低频
    assert parts["ACWA Power"]["is_ascii"] is True
```

- [ ] **Step 2: 跑测试确认失败** → Expected: ModuleNotFoundError

- [ ] **Step 3: 实现**（要点：`mode=ro` 只读连接；复用 `developer_normalizer.split_developer` 与 `unify_junk`（Unknown 等垃圾值排除）；对每部件 `lookup()`，`matched=True` 的排除；sample_projects 取该部件出现的前 3 个 (canonical_id, project_name)；current_en_cache 用 `en_backfill._cache_en` 同款缓存读取；utf-8-sig 写 CSV；logging 落文件）

- [ ] **Step 4: 跑测试通过 + s02b 全量绿**

Run: `cd s02b_entity_normalizer && uv run --no-sync pytest -q`

- [ ] **Step 5: 真库试跑（只读）并核对量级**

Run: `cd s02b_entity_normalizer && uv run --no-sync python -m s02b_entity_normalizer.tools.developer_seed_export`
Expected: 种子 ≈150±20 个（真库频次≥5 共 153，减去 Masdar 锚点与 hierarchy 已可解的中企名）；CSV 路径打印

- [ ] **Step 6: Commit**

```bash
git add s02b_entity_normalizer/src/s02b_entity_normalizer/tools/developer_seed_export.py s02b_entity_normalizer/tests/test_developer_seed_export.py
git commit -m "feat(s02b): developer 种子导出 CLI（频次≥5、排除已可解、campaign 输入）" -- s02b_entity_normalizer/src/s02b_entity_normalizer/tools/developer_seed_export.py s02b_entity_normalizer/tests/test_developer_seed_export.py
```

---

### Task 4: 种子 campaign（编排型任务，含用户复核 GATE）

**Files:**
- Create: `supporting_docs/developer_registry_draft_2026-07.json`（campaign 草稿，**不提交**）
- Create: `supporting_docs/developer_registry_review_2026-07.csv`（复核清单，**不提交**）
- Modify: `developer_registry.json`（复核通过后合入，提交）

**Interfaces:**
- Consumes: Task 3 种子 CSV
- Produces: 入册后的 `developer_registry.json`（Task 5/6/7 的数据基础）

- [ ] **Step 1: 分批联网核实**——控制器把种子 CSV 按 ~50 名/批派 3 个 sonnet 联网代理（先例：中企 hierarchy campaign / misses campaign）。每名产出：canonical_cn / canonical_en / aliases（含种子原文变体）/ is_chinese / parent_cn（仅标注）/ 证据 URL。判定规则进代理 prompt：同一法人变体才归组（D2）；查不实的标 uncertain 不入册；中企若 hierarchy 已收则标 `hierarchy_covered` 排除。
- [ ] **Step 2: 汇总去冲突**——合并三代理产出为 draft JSON + review CSV（uncertain/低置信/跨代理冲突行打 ★）；跑一次 `build_index(draft)` 确认零别名冲突。
- [ ] **Step 3: 【GATE】用户复核 review CSV**——★ 行逐条过目，其余抽查。**未过目不得入册。**
- [ ] **Step 4: 按复核结果合入 `developer_registry.json`**，`_meta.generated` 更新、`source` 记 campaign 批次。
- [ ] **Step 5: 验证 + Commit**

Run: `uv run --no-sync pytest tests/test_developer_registry.py -q`（契约+别名唯一）
Run: `cd s02b_entity_normalizer && uv run --no-sync pytest tests/test_company_lookup.py -q`（真实文件锚点仍绿）

```bash
git add developer_registry.json
git commit -m "data(registry): 种子 campaign 入册（频次≥5，用户复核通过）" -- developer_registry.json
```

---

### Task 5: 2B-3 改造——registry 前置查表 + LLM 降级为补录建议器

**Files:**
- Modify: `s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py`（改 `run()` 主流程，约 :194-268；新增两个函数；既有 `split_developer/unify_junk/rebuild/plan_value_changes/apply_value_changes/_collect_parts` 与 LLM 三件套 `filter_candidates_conservative/_build_dev_confirm_prompt/_llm_confirm_batch_dev` 全部保留不动）
- Test: `s02b_entity_normalizer/tests/test_developer_normalizer_registry.py`（新文件；既有 developer_normalizer 测试不动）

**Interfaces:**
- Consumes: Task 2 `build_index/lookup/collect_misses`
- Produces: `run(db_path=None, preview=False, apply=False, use_registry=True)`（新增参数，默认开）；新函数 `registry_part_mapping(parts: list[tuple[str,int]], index) -> dict[str,str]`、`write_suggestions_csv(confirmed_pairs: list[dict], out_path: str) -> None`
- 行为契约（复审据此把关）：
  1. `use_registry=True` 时：part_mapping = registry/hierarchy 命中且 canonical≠原文 的映射（确定性，不经 LLM）
  2. LLM 候选对只在**未命中部件**之间生成；确认结果**不再进 part_mapping、不写库**，写 `supporting_docs/developer_registry_suggestions_<ts>.csv`（列：part_a/part_b/freq_a/freq_b/verdict/llm_reason）供入册复核
  3. miss CSV：`collect_misses` 写 `supporting_docs/developer_norm_misses_<ts>.csv`
  4. would-change CSV、.bak、apply、幂等契约与现行完全一致（`apply_value_changes` 原样复用）
  5. `use_registry=False` = 现行为 100% 不变（灰度对拍的对照组）

- [ ] **Step 1: 写失败测试**

```python
"""2B-3 registry 前置：确定性映射、LLM 降级为建议、旧行为可回退。"""
import json
import sqlite3
from s02b_entity_normalizer.company_lookup import build_index
from s02b_entity_normalizer.developer_normalizer import registry_part_mapping

FIXTURE_REGISTRY = {  # 与 test_company_lookup 同款迷你册
    "_meta": {"version": 1, "generated": "2026-01-01", "source": "test"},
    "companies": [{"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
                   "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"}],
}


def _index(tmp_path):
    rp = tmp_path / "reg.json"
    rp.write_text(json.dumps(FIXTURE_REGISTRY, ensure_ascii=False), encoding="utf-8")
    return build_index(str(rp), None)


def test_registry_mapping_only_when_changed(tmp_path):
    idx = _index(tmp_path)
    parts = [("Masdar", 10), ("阿布扎比未来能源公司", 5), ("无关公司", 3)]
    m = registry_part_mapping(parts, idx)
    assert m == {"Masdar": "阿布扎比未来能源公司"}  # 已是规范名的不进映射；未命中不进映射


def test_llm_candidates_exclude_registry_hits(tmp_path):
    """未命中部件才可成为 LLM 候选（Masdar 命中后不得再与任何部件配对）。"""
    from s02b_entity_normalizer.developer_normalizer import _candidates_for_llm
    idx = _index(tmp_path)
    parts = [("Masdar", 10), ("马斯达尔能源公司", 4), ("马斯达尔能源集团", 3)]
    cands = _candidates_for_llm(parts, idx)
    names = {c["a"] for c in cands} | {c["b"] for c in cands}
    assert "Masdar" not in names
```

（另写一条端到端小库测试：temp DB 两行 developer=`"Masdar; 无关公司"` → `run(use_registry=True, apply=True)` 后值为 `"阿布扎比未来能源公司; 无关公司"`、重跑 0 变更、suggestions/miss CSV 生成——照 Task 3 的 `_mk_db` 建表式样，LLM 部分用 monkeypatch 短路为空返回。）

- [ ] **Step 2: 跑测试确认失败** → ImportError（新函数不存在）

- [ ] **Step 3: 实现**。`run()` 改造骨架（保守重建/CSV/apply 全走既有函数）：

```python
def run(db_path=None, preview=False, apply=False, use_registry=True):
    ...  # 现有开头不动：连接、_collect_parts、unify_junk 垃圾值 pairs
    part_mapping: Dict[str, str] = {}
    if use_registry:
        index = build_index()  # 缺省真实两文件
        part_mapping.update(registry_part_mapping(parts, index))
    # LLM 段：候选只在未命中部件间生成；apply=True 才调 LLM（现契约不变）
    candidates = _candidates_for_llm(parts, index) if use_registry else 原候选逻辑(parts)
    if apply and candidates:
        confirmed = ...  # 现行 _llm_confirm_batch_dev 分批调用，原样
        if use_registry:
            write_suggestions_csv(confirmed, out)   # 降级：只写建议，不并入 part_mapping
        else:
            part_mapping.update(现行 Union-Find 合并(confirmed))  # 对照组保持旧行为
    pairs = plan_value_changes(conn, part_mapping)  # ↓ 以下与现行完全一致
    ...
```

- [ ] **Step 4: 跑新测试 + s02b 全量绿**

Run: `cd s02b_entity_normalizer && uv run --no-sync pytest -q`
Expected: 全绿（既有 developer_normalizer 测试因 use_registry 默认开可能受影响——若有断言旧 LLM 直写行为的用例，给该用例显式传 `use_registry=False`，不许改断言语义）

- [ ] **Step 5: Commit**

```bash
git add s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py s02b_entity_normalizer/tests/test_developer_normalizer_registry.py
git commit -m "feat(s02b): 2B-3 registry 前置查表，LLM 降级为登记册补录建议器（use_registry 可回退）" -- s02b_entity_normalizer/src/s02b_entity_normalizer/developer_normalizer.py s02b_entity_normalizer/tests/test_developer_normalizer_registry.py
```

---

### Task 6: en_backfill 加 registry 最高优先档（EN 单一写入方）

**Files:**
- Modify: `s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py`（`resolve_en` :54-73 与 `plan_db` :76 调用点、`main()` 接线）
- Test: `s02b_entity_normalizer/tests/test_en_backfill_registry.py`（新文件）

**Interfaces:**
- Consumes: Task 2 `build_index/lookup`
- Produces: `resolve_en(cache, cn, index=None)`（第三参可选，None=现行为 100% 不变）；`plan_db(conn, targets, cache, index=None)` 透传；`main()` 构建 index 并传入（加 `--no-registry` 开关备回退）
- 行为契约：
  1. 优先级：registry `canonical_en` > 缓存 `en_abbr`/`en` > miss（spec §5）
  2. **纯 ASCII 值也查册**：`ascii_skip` 前先 lookup，命中且 `canonical_en` 与现值不同 → update（治 28% 英文部件坐在 CN 列导致的 en 空洞）
  3. 多部件串逐段：每段 registry→缓存，全命中才拼（现行 `hit_multi` 契约不变）
  4. registry 命中段的 status 标 `hit_registry` / `hit_multi_registry`（报告可辨识来源）

- [ ] **Step 1: 写失败测试**

```python
"""en_backfill registry 优先档：跨源优先级、ASCII 查册、逐段拼接。"""
import json
from s02b_entity_normalizer.company_lookup import build_index
from s02b_entity_normalizer.en_backfill import resolve_en

REG = {"_meta": {"version": 1, "generated": "x", "source": "t"},
       "companies": [{"canonical_cn": "阿布扎比未来能源公司", "canonical_en": "Masdar",
                      "aliases": ["Masdar", "马斯达尔"], "is_chinese": False, "parent_cn": None, "source": "t"}]}


def _idx(tmp_path):
    p = tmp_path / "r.json"; p.write_text(json.dumps(REG, ensure_ascii=False), encoding="utf-8")
    return build_index(str(p), None)


def test_registry_beats_cache(tmp_path):
    cache = {"阿布扎比未来能源公司": {"en": "Abu Dhabi Future Energy Co."}}
    en, status = resolve_en(cache, "阿布扎比未来能源公司", index=_idx(tmp_path))
    assert en == "Masdar" and status == "hit_registry"


def test_ascii_value_hits_registry(tmp_path):
    en, status = resolve_en({}, "马斯达尔", index=_idx(tmp_path))
    assert en == "Masdar"
    en2, status2 = resolve_en({}, "Masdar", index=_idx(tmp_path))  # ASCII 值也查册
    assert en2 == "Masdar" and status2 == "hit_registry"


def test_no_index_keeps_legacy_behavior():
    assert resolve_en({}, "Masdar") == (None, "ascii_skip")   # 不传 index=现行为


def test_multipart_mixed_sources(tmp_path):
    cache = {"无关公司": {"en_abbr": "Wuguan Co."}}
    en, status = resolve_en(cache, "马斯达尔; 无关公司", index=_idx(tmp_path))
    assert en == "Masdar; Wuguan Co."
```

- [ ] **Step 2: 跑测试确认失败** → TypeError（resolve_en 无 index 参）

- [ ] **Step 3: 实现**（`_resolve_part` 与单部件路径头部插入：`if index is not None: r = lookup(p, index); if r.matched and r.canonical_en: return r.canonical_en`；ASCII 早退分支改为先查册、未中维持 ascii_skip；status 命中源标注）

- [ ] **Step 4: 跑测试 + s02b 全量绿**（既有 en_backfill 测试不传 index，天然不变绿）

- [ ] **Step 5: Commit**

```bash
git add s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py s02b_entity_normalizer/tests/test_en_backfill_registry.py
git commit -m "feat(s02b): en_backfill 加 registry 最高优先档（EN 单一写入方，ASCII 值也查册）" -- s02b_entity_normalizer/src/s02b_entity_normalizer/en_backfill.py s02b_entity_normalizer/tests/test_en_backfill_registry.py
```

---

### Task 7: 灰度对拍 + 存量回填（真库，双 GATE）

**Files:** 无新代码（全用 Task 5/6 的 CLI）；产物 CSV 留 `supporting_docs/`（不提交）

- [ ] **Step 1: 拷贝库对拍**——WAL 安全快照真库到 scratchpad；同一快照分别跑 `normalize_developers`（`use_registry=False` 旧口径 dry-run）与（`use_registry=True` 新口径 dry-run），diff 两份 would-change CSV 出对拍报告：新口径命中数、旧口径独有合并（应≈0——查表应是 LLM 判定的子集+跨语言新增）、新口径独有（应全部可溯源到册内条目）。
- [ ] **Step 2: 【GATE】对拍报告交用户**——出现"册外意外合并"即 bug 打回 Task 5。通过后才许碰真库。
- [ ] **Step 3: 真库 CN 回填**——`uv run --no-sync normalize_developers --db <真库绝对路径>`（dry-run 出 would-change CSV）→ 用户复看 → `--apply`（.bak+幂等重跑 0）。
- [ ] **Step 4: 真库 EN 回填**——`uv run --no-sync backfill_entity_en --db <真库绝对路径>`（Task 6 后 registry 档生效；dry-run 核对 update 候选构成——应见 `hit_registry` 批量出现）→ `--apply` → 幂等重跑 0。
- [ ] **Step 5: 验收 SQL 实证**（只读）——① `project_developer` 中 `Masdar`→0 次、`阿布扎比未来能源公司` 次数≈38+36 ② 讹变 `中国能源海外投资有限公司`→0 次 ③ `project_developer_en` 空值率显著低于 61%（记录精确前后值）④ 行数三表不变。写进任务报告。

---

### Task 8: miss 循环 + docs 收尾

**Files:**
- Modify: `docs/rules_and_plans/codex_quarterly_runbook.md`（季度步骤加一行：增量流水线跑完后收集 `developer_norm_misses_*.csv` 与 `developer_registry_suggestions_*.csv`，人工/campaign 复核入册）
- Modify: `docs/status/s02b.md`（2B-3/2B-4 当前功能改为 registry 口径，覆盖式）、`docs/status/pipeline.md`（2B-3 一行描述更新）
- Modify: `docs/changes/2026-W28b.md`（追加条目）、`docs/superpowers/specs/2026-07-07-developer-registry-design.md`（状态行→已实施）
- 注意：`docs/status/s02b.md` 若实际文件名不同（status 目录以 sNN 命名），以目录实况为准

- [ ] **Step 1: 按 CLAUDE.md 文档规则更新上述四处**（status=覆盖式快照；changes=追加条目含 日期·模块·做了什么·为什么·影响）
- [ ] **Step 2: 全量测试终验**——根 `uv run --no-sync pytest tests/ -q`；s02b 全量。
- [ ] **Step 3: Commit（显式路径，逐文件列出）**

---

## Self-Review 记录（写完计划后自查）

- **Spec 覆盖**：D1(Task5/6 就地改写+不加列) / D2(Task2 引擎不上卷+Task4 campaign 规则) / D3(Task4 中外都收) / D4(Task3 min_freq=5) / D5(Task5 前置查表+降级建议) / D6(引擎在 s02b、只接 2B-3/2B-4、pipeline 步骤不动——**flow.py 零改动**，2B-3/2B-4 内部行为升级) ✅；spec §5 EN 单一写入方(Task6+Task5 不写 EN) ✅；§6 六步→Task1-8 ✅（§6.6 季度循环按 YAGNI 缩为 runbook 接入+CSV 契约，不建 T4 队列类型，留待真实跑一季后再定）。
- **占位符**：Task 3 Step 3 与 Task 5 Step 3 为"要点+骨架"而非全量代码——接口/行为契约/测试已完整钉死，实现细节留给实现者按既有模式（sweep CLI/现行 run()）填充，复审按行为契约把关。其余任务代码完整。
- **类型一致性**：`LookupResult/build_index/lookup/collect_misses` 在 Task 2 定义、Task 3/5/6 消费签名一致 ✅；`resolve_en(cache, cn, index=None)` Task 6 定义与 Task 7 Step 4 用法一致 ✅。
