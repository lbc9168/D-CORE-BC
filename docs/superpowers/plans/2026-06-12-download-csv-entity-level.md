# 下载 CSV 重做（实体级明细）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把下载用的 `events_export.csv` 从「事件级 + 公司拼串」改成「实体级（一行=一事件×一公司）+ 网页口径全量」，补全公司中英文名/子公司/参与角色/各自央企标志。

**Architecture:** 仅改 s03 后端 `events_exporter.py`：行粒度改为事件×公司；新增 `passes_baseline()` 复刻前端基线政策，从已构建的 `projects_flat`（DST_DB）取合格项目集；事件只保留 `year>=2022`。前端、s04 下载逻辑、文件名/路径全部不变。

**Tech Stack:** Python 3 + sqlite3 + csv（stdlib）；枚举走单一可信源 `bri_enums`/`enums_mapping.json`；pytest。

**工作目录：** worktree `.claude/worktrees/v2`（分支 `worktree-v2`）。所有路径相对该 worktree 根。

**设计稿：** `docs/superpowers/specs/2026-06-12-download-csv-entity-level-design.md`

---

## File Structure

- **Modify** `s03_data_exporter/src/s03_data_exporter/events_exporter.py` — 整文件重写：新列集、`passes_baseline()`、`_eligible_ids()`、实体级行生成、year>=2022 过滤、新签名 `flat_db=`。
- **Modify** `s03_data_exporter/src/s03_data_exporter/main.py` — 第 [6/6] 步给 `export_events_csv(...)` 补传 `flat_db=DST_DB`。
- **Modify** `s03_data_exporter/tests/test_events_exporter.py` — 整文件重写：基线纯函数单测 + 实体级集成测试（含 flat_db）。
- **Modify** `docs/status/*` 与 `docs/changes/*` — 同步项目状态与修改日志（项目规则要求）。

枚举值参考（实测）：容量类别如 `光伏`(Solar PV)；`储能`(Energy Storage)=no_capacity；`核电`(Nuclear)=hidden。当前无 `excluded` 类别（`is_excluded_category` 对所有现有类别返回 False，仍保留检查以对齐前端）。

---

## Task 1: 基线政策纯函数 `passes_baseline()`

把前端 `DataContext.shouldExcludeByPolicy` 复刻成可独立测试的 Python 纯函数。先写测试，再实现。

**Files:**
- Modify: `s03_data_exporter/src/s03_data_exporter/events_exporter.py`
- Test: `s03_data_exporter/tests/test_events_exporter.py`

- [ ] **Step 1: 写失败测试**（替换测试文件顶部，先只加基线单测；集成测试 Task 2 再改）

在 `s03_data_exporter/tests/test_events_exporter.py` 顶部加入：

```python
"""events_export.csv：实体级（一事件×一公司）+ 网页口径全量。"""
import csv
import sqlite3

from s03_data_exporter import events_exporter

# ── 基线政策纯函数（对齐前端 shouldExcludeByPolicy）──────────────────────────────

_OK = {
    "project_id": "IND-PV-0001", "country_iso": "IND",
    "first_event_year": 2022, "last_event_year": 2024,
    "category": "光伏", "primary_entity": "中国电建", "capacity_mw": 50.0,
}


def test_baseline_accepts_normal_project():
    assert events_exporter.passes_baseline(dict(_OK)) is True


def test_baseline_excludes_china_iso():
    assert events_exporter.passes_baseline({**_OK, "country_iso": "CHN"}) is False
    assert events_exporter.passes_baseline({**_OK, "country_iso": "twn"}) is False


def test_baseline_excludes_pre_2022_last_event():
    assert events_exporter.passes_baseline({**_OK, "last_event_year": 2021}) is False


def test_baseline_excludes_null_event_years():
    assert events_exporter.passes_baseline({**_OK, "first_event_year": None}) is False
    assert events_exporter.passes_baseline({**_OK, "last_event_year": None}) is False


def test_baseline_excludes_hidden_category():
    # 核电 = hidden
    assert events_exporter.passes_baseline({**_OK, "category": "核电"}) is False


def test_baseline_excludes_unknown_primary_entity():
    assert events_exporter.passes_baseline({**_OK, "primary_entity": "未知"}) is False
    assert events_exporter.passes_baseline({**_OK, "primary_entity": ""}) is False


def test_baseline_excludes_missing_capacity_for_capacity_category():
    # 光伏 应有容量，capacity 缺失 → 排除
    assert events_exporter.passes_baseline({**_OK, "capacity_mw": None}) is False


def test_baseline_allows_no_capacity_category_without_capacity():
    # 储能 = no_capacity，缺容量也通过
    assert events_exporter.passes_baseline(
        {**_OK, "category": "储能", "capacity_mw": None}
    ) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd s03_data_exporter && uv run pytest tests/test_events_exporter.py -k baseline -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'passes_baseline'`）

- [ ] **Step 3: 实现 `passes_baseline` 及辅助常量/函数**

在 `events_exporter.py` 的 `import bri_enums as enums` 之后、`_COLUMNS` 之前插入：

```python
BASELINE_YEAR = 2022
_EXCLUDED_ISO3 = {"CHN", "HKG", "MAC", "TWN"}
_UNKNOWN_TEXTS = {"unknown", "未知", "未披露", "n/a", "na", "-", "—"}


def _is_unknown_text(value) -> bool:
    """文本是否视作"未知"（空或占位符）；对齐前端 isUnknownText。"""
    if not value:
        return True
    return str(value).strip().lower() in _UNKNOWN_TEXTS


def passes_baseline(p: dict) -> bool:
    """projects_flat 行是否进入网页可视化口径（逐条对齐前端 shouldExcludeByPolicy）。

    ⚠️ 改这里须同步前端 src/contexts/DataContext.tsx 的 shouldExcludeByPolicy；
    TS/Python 无法共享代码，靠本文件测试钉住一致性。
    """
    iso = (p.get("country_iso") or "").upper()
    if iso in _EXCLUDED_ISO3:
        return False
    if p.get("first_event_year") is None or p.get("last_event_year") is None:
        return False
    if p["last_event_year"] < BASELINE_YEAR:
        return False
    category = p.get("category") or ""
    if enums.is_excluded_category(category) or enums.is_hidden_category(category):
        return False
    if _is_unknown_text(p.get("primary_entity")):
        return False
    # 应有容量的类别却缺容量 → 排除（空 category 不触发，与前端 hasCapacity 一致）
    if category and not enums.is_no_capacity(category) and p.get("capacity_mw") is None:
        return False
    return True
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd s03_data_exporter && uv run pytest tests/test_events_exporter.py -k baseline -v`
Expected: PASS（8 个 baseline 测试全绿）

- [ ] **Step 5: 提交**

```bash
git add s03_data_exporter/src/s03_data_exporter/events_exporter.py s03_data_exporter/tests/test_events_exporter.py
git commit -m "$(cat <<'EOF'
feat(s03): events_exporter 新增 passes_baseline 基线政策纯函数

复刻前端 DataContext.shouldExcludeByPolicy（中港澳台/2022基线/隐藏类别/
未知主体/缺容量），供下载 CSV 对齐网页口径；含 8 项单测钉住一致性。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 重写 `export_events_csv` 为实体级 + 集成测试

**Files:**
- Modify: `s03_data_exporter/src/s03_data_exporter/events_exporter.py`
- Test: `s03_data_exporter/tests/test_events_exporter.py`

- [ ] **Step 1: 写失败的集成测试**（在 Task 1 的 baseline 测试之后追加）

```python
# ── 实体级集成测试 ──────────────────────────────────────────────────────────────

def _mk(src, flat):
    """构造 s02a 三表（src）与 projects_flat（flat）测试库。"""
    c = sqlite3.connect(src)
    c.executescript(
        """
        CREATE TABLE canonical_projects(
            id TEXT PRIMARY KEY, project_name TEXT, project_name_en TEXT,
            country TEXT, country_iso TEXT, category TEXT, excluded INTEGER DEFAULT 0
        );
        CREATE TABLE project_events(
            id TEXT PRIMARY KEY, canonical_id TEXT, project_stage TEXT,
            capacity_mw REAL, total_investment_usd REAL, publish_date TEXT,
            investment_date TEXT, financing_model TEXT, verification_status TEXT
        );
        CREATE TABLE chinese_entities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT,
            implementing_entity TEXT, implementing_entity_en TEXT,
            subsidiary_name TEXT, subsidiary_name_en TEXT,
            involvement_type TEXT, is_central_soe INTEGER
        );
        """
    )
    # 项目 A：合格；E01 两家公司、E02 无公司、E03 为 2021（应被 year 过滤）
    c.execute("INSERT INTO canonical_projects(id, project_name, project_name_en, country_iso, category, excluded) "
              "VALUES('IND-PV-0001','光伏A','Solar A','IND','光伏',0)")
    c.executemany(
        "INSERT INTO project_events(id, canonical_id, project_stage, capacity_mw, publish_date) VALUES(?,?,?,?,?)",
        [
            ("IND-PV-0001-01", "IND-PV-0001", "签约", 50.0, "2023-01-15"),
            ("IND-PV-0001-02", "IND-PV-0001", "竣工", 50.0, "2025-10-01"),
            ("IND-PV-0001-03", "IND-PV-0001", "签约", 50.0, "2021-05-01"),
        ],
    )
    c.executemany(
        "INSERT INTO chinese_entities(event_id, implementing_entity, implementing_entity_en, "
        "subsidiary_name, subsidiary_name_en, involvement_type, is_central_soe) VALUES(?,?,?,?,?,?,?)",
        [
            ("IND-PV-0001-01", "中国电建", "PowerChina", "山东电建", "Shandong Electric", "EPC", 1),
            ("IND-PV-0001-01", "中国能建", "Energy China", "", "", "投资", 1),
        ],
    )
    # 项目 B：中国境内（基线应排除，但 flat_db=None 时保留）
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('CHN-PV-0002','Solar B','CHN','光伏',0)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, capacity_mw, publish_date) "
              "VALUES('CHN-PV-0002-01','CHN-PV-0002','签约',10.0,'2023-03-01')")
    c.execute("INSERT INTO chinese_entities(event_id, implementing_entity, is_central_soe) "
              "VALUES('CHN-PV-0002-01','某公司',0)")
    # 项目 C：excluded=1，永远排除
    c.execute("INSERT INTO canonical_projects(id, project_name_en, country_iso, category, excluded) "
              "VALUES('X','X','IND','光伏',1)")
    c.execute("INSERT INTO project_events(id, canonical_id, project_stage, publish_date) "
              "VALUES('X-01','X','签约','2024-01')")
    c.commit()
    c.close()

    # projects_flat（仅含基线判定所需列）：A 合格、B 因 CHN 不合格
    f = sqlite3.connect(flat)
    f.execute(
        "CREATE TABLE projects_flat(project_id TEXT PRIMARY KEY, country_iso TEXT, "
        "first_event_year INTEGER, last_event_year INTEGER, category TEXT, "
        "primary_entity TEXT, capacity_mw REAL)"
    )
    f.executemany(
        "INSERT INTO projects_flat VALUES(?,?,?,?,?,?,?)",
        [
            ("IND-PV-0001", "IND", 2023, 2025, "光伏", "中国电建", 50.0),
            ("CHN-PV-0002", "CHN", 2023, 2023, "光伏", "某公司", 10.0),
        ],
    )
    f.commit()
    f.close()


def test_entity_level_rows_with_baseline(tmp_path):
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    n = events_exporter.export_events_csv(
        src, [out], flat_db=flat, iso3_to_en={"IND": "India"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = rows[0].keys() if rows else []

    # A: E01 两家公司(2 行) + E02 无公司(1 行) = 3；E03(2021) 被过滤；B(CHN) 基线排除；C excluded
    assert n == 3 and len(rows) == 3

    e01 = [r for r in rows if r["event_id"] == "IND-PV-0001-01"]
    assert len(e01) == 2
    pc = next(r for r in e01 if r["entity_cn"] == "中国电建")
    assert pc["entity_en"] == "PowerChina"
    assert pc["subsidiary_cn"] == "山东电建" and pc["subsidiary_en"] == "Shandong Electric"
    assert pc["involvement_type_cn"] == "EPC" and pc["involvement_type_en"] == "EPC"
    assert pc["is_central_soe"] == "1"
    assert pc["stage_cn"] == "签约" and pc["stage_en"] == "Signed"
    assert pc["stage_group_cn"] == "前期" and pc["stage_group_en"] == "Pre-construction"
    assert pc["year"] == "2023" and pc["month"] == "2023-01" and pc["country_en"] == "India"

    # E02 无公司：实体列全空
    e02 = next(r for r in rows if r["event_id"] == "IND-PV-0001-02")
    assert e02["entity_cn"] == "" and e02["entity_en"] == "" and e02["is_central_soe"] == ""
    assert e02["stage_group_cn"] == "完工"

    # 删除的旧列不存在，新列存在
    assert "entities" not in header
    assert "entity_cn" in header and "involvement_type_en" in header
    # 被过滤/排除的项目均不出现
    ids = {r["canonical_id"] for r in rows}
    assert ids == {"IND-PV-0001"}


def test_no_flat_db_skips_baseline_only_excluded(tmp_path):
    """flat_db=None：不做基线，仅排 excluded=1（B 的 CHN 项目会保留）。"""
    src, flat, out = tmp_path / "s.db", tmp_path / "f.db", tmp_path / "events_export.csv"
    _mk(str(src), str(flat))

    n = events_exporter.export_events_csv(src, [out], iso3_to_en={"IND": "India"})

    with open(out, encoding="utf-8-sig", newline="") as fh:
        ids = {r["canonical_id"] for r in csv.DictReader(fh)}
    # A(3 行) + B(1 行) = 4；C 仍排除；E03(2021) 仍按 year 过滤
    assert n == 4
    assert ids == {"IND-PV-0001", "CHN-PV-0002"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd s03_data_exporter && uv run pytest tests/test_events_exporter.py -k "entity_level or no_flat_db" -v`
Expected: FAIL（旧 `export_events_csv` 无 `flat_db` 参数 / 无 `entity_cn` 列）

- [ ] **Step 3: 整文件重写 `events_exporter.py`**

把 `events_exporter.py` 替换为下述完整内容（保留 Task 1 已加的 `passes_baseline` 等，不重复——下面是合并后的整文件）：

```python
"""events_exporter.py — 从 s02a 三表导出"实体级"明细 CSV（一行 = 一事件 × 一家中国公司）。

供前端下载：同一项目每个 stage 拆条，每个事件下每家中国公司各一行；补全公司
中英文名/子公司中英文名/参与角色中英文/各自央企标志。口径对齐网页可视化：
项目集走 projects_flat + 基线政策（passes_baseline），事件只含 year>=2022。
"""
import csv
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# ── 枚举单一可信源：上溯找仓库根（含 bri_enums.py）──
_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums  # noqa: E402

BASELINE_YEAR = 2022
_EXCLUDED_ISO3 = {"CHN", "HKG", "MAC", "TWN"}
_UNKNOWN_TEXTS = {"unknown", "未知", "未披露", "n/a", "na", "-", "—"}

_COLUMNS = [
    "canonical_id", "event_id",
    "project_name_en", "project_name_cn",
    "country_en", "country_iso", "province",
    "category_en", "category_cn",
    "stage_cn", "stage_en", "stage_group_cn", "stage_group_en",
    "year", "month",
    "capacity_mw", "total_investment_usd", "financing_model_en",
    "verification_status",
    "entity_cn", "entity_en", "subsidiary_cn", "subsidiary_en",
    "involvement_type_cn", "involvement_type_en", "is_central_soe",
]

_ENTITY_COLS = (
    "entity_cn", "entity_en", "subsidiary_cn", "subsidiary_en",
    "involvement_type_cn", "involvement_type_en", "is_central_soe",
)


def _year(d: Optional[str]) -> str:
    """日期字符串 → 年（4 位数字）或空串。"""
    d = d or ""
    return d[:4] if len(d) >= 4 and d[:4].isdigit() else ""


def _month(d: Optional[str]) -> str:
    """日期字符串 → YYYY-MM 或空串。"""
    d = d or ""
    return d[:7] if len(d) >= 7 and d[4:5] == "-" else ""


def _is_unknown_text(value) -> bool:
    """文本是否视作"未知"（空或占位符）；对齐前端 isUnknownText。"""
    if not value:
        return True
    return str(value).strip().lower() in _UNKNOWN_TEXTS


def passes_baseline(p: dict) -> bool:
    """projects_flat 行是否进入网页可视化口径（逐条对齐前端 shouldExcludeByPolicy）。

    ⚠️ 改这里须同步前端 src/contexts/DataContext.tsx 的 shouldExcludeByPolicy；
    TS/Python 无法共享代码，靠本文件测试钉住一致性。
    """
    iso = (p.get("country_iso") or "").upper()
    if iso in _EXCLUDED_ISO3:
        return False
    if p.get("first_event_year") is None or p.get("last_event_year") is None:
        return False
    if p["last_event_year"] < BASELINE_YEAR:
        return False
    category = p.get("category") or ""
    if enums.is_excluded_category(category) or enums.is_hidden_category(category):
        return False
    if _is_unknown_text(p.get("primary_entity")):
        return False
    if category and not enums.is_no_capacity(category) and p.get("capacity_mw") is None:
        return False
    return True


def _eligible_ids(flat_db) -> Optional[set]:
    """读 projects_flat 取通过基线政策的 project_id 集合；flat_db 为 None 返回 None（不做基线）。"""
    if flat_db is None:
        return None
    con = sqlite3.connect(flat_db)
    try:
        con.row_factory = sqlite3.Row
        return {
            r["project_id"]
            for r in con.execute("SELECT * FROM projects_flat")
            if passes_baseline(dict(r))
        }
    finally:
        con.close()


def export_events_csv(
    src_db,
    out_paths: list,
    flat_db=None,
    iso3_to_en: Optional[dict] = None,
    province_map: Optional[dict] = None,
) -> int:
    """读 s02a 三表，按 事件×公司 一行写出实体级 CSV 到 out_paths（多个目录）。

    合格项目集：flat_db 提供时走 projects_flat + passes_baseline；否则仅排 excluded=1。
    事件只含 year>=2022；无中方公司的事件保留 1 行（实体列留空）。返回写出行数。
    """
    iso3_to_en = iso3_to_en or {}
    province_map = province_map or {}
    eligible = _eligible_ids(flat_db)

    con = sqlite3.connect(src_db)
    try:
        con.row_factory = sqlite3.Row
        projects = {r["id"]: dict(r) for r in con.execute("SELECT * FROM canonical_projects")}
        entities_by_event: dict[str, list[dict]] = {}
        for e in con.execute("SELECT * FROM chinese_entities"):
            entities_by_event.setdefault(e["event_id"], []).append(dict(e))
        events = [dict(r) for r in con.execute("SELECT * FROM project_events")]
    finally:
        con.close()

    rows: list[dict] = []
    for ev in events:
        proj = projects.get(ev["canonical_id"])
        if proj is None or proj.get("excluded", 0):
            continue
        if eligible is not None and ev["canonical_id"] not in eligible:
            continue
        d = ev.get("publish_date") or ev.get("investment_date") or ""
        yr = _year(d)
        if not yr or int(yr) < BASELINE_YEAR:
            continue

        stage = ev.get("project_stage") or ""
        grp = enums.viz_group(stage)
        grp_meta = enums.viz_groups().get(grp, {}) if grp else {}
        cap = ev.get("capacity_mw")
        inv = ev.get("total_investment_usd")

        base = {
            "canonical_id": ev["canonical_id"],
            "event_id": ev["id"],
            "project_name_en": proj.get("project_name_en") or "",
            "project_name_cn": proj.get("project_name") or "",
            "country_en": iso3_to_en.get(proj.get("country_iso") or "", ""),
            "country_iso": proj.get("country_iso") or "",
            "province": province_map.get(ev["canonical_id"]) or "",
            "category_en": enums.category_en(proj.get("category") or "") or (proj.get("category") or ""),
            "category_cn": proj.get("category") or "",
            "stage_cn": stage,
            "stage_en": enums.stage_en(stage) or stage,
            "stage_group_cn": grp_meta.get("zh", ""),
            "stage_group_en": grp_meta.get("en", ""),
            "year": yr,
            "month": _month(d),
            "capacity_mw": cap if cap is not None else "",
            "total_investment_usd": inv if inv is not None else "",
            "financing_model_en": enums.financing_en(ev.get("financing_model") or "") or (ev.get("financing_model") or ""),
            "verification_status": ev.get("verification_status") or "",
        }

        ents = entities_by_event.get(ev["id"], [])
        if not ents:
            rows.append({**base, **{c: "" for c in _ENTITY_COLS}})
            continue
        for e in ents:
            inv_type = e.get("involvement_type") or ""
            soe = e.get("is_central_soe")
            rows.append({
                **base,
                "entity_cn": e.get("implementing_entity") or "",
                "entity_en": e.get("implementing_entity_en") or "",
                "subsidiary_cn": e.get("subsidiary_name") or "",
                "subsidiary_en": e.get("subsidiary_name_en") or "",
                "involvement_type_cn": inv_type,
                "involvement_type_en": enums.involvement_en(inv_type) or inv_type,
                "is_central_soe": soe if soe is not None else "",
            })

    for out in out_paths:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    return len(rows)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd s03_data_exporter && uv run pytest tests/test_events_exporter.py -v`
Expected: PASS（baseline 8 项 + entity_level + no_flat_db 共 10 项全绿）

- [ ] **Step 5: 提交**

```bash
git add s03_data_exporter/src/s03_data_exporter/events_exporter.py s03_data_exporter/tests/test_events_exporter.py
git commit -m "$(cat <<'EOF'
feat(s03): 下载 CSV 改实体级（一事件×一公司）+ 网页口径全量

行粒度改为事件×公司，补全公司中英文名/子公司/参与角色中英文/各自央企标志，
删除拼串 entities；新增 flat_db 走 projects_flat+passes_baseline 对齐网页口径，
事件只含 year>=2022；无公司事件保留 1 行。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: main.py 接入 `flat_db`

**Files:**
- Modify: `s03_data_exporter/src/s03_data_exporter/main.py`（第 [6/6] 步，约 134-139 行）

- [ ] **Step 1: 修改调用补传 flat_db**

把 main.py 中：

```python
    n_ev = events_exporter.export_events_csv(
        SRC_DB,
        [DST_DIR / "events_export.csv", S04_DATA_DIR / "events_export.csv"],
        iso3_to_en=flat_builder.ISO3_TO_EN_NAME,
        province_map=province_map,
    )
```

改为（新增 `flat_db=DST_DB`，使下载口径与网页一致）：

```python
    n_ev = events_exporter.export_events_csv(
        SRC_DB,
        [DST_DIR / "events_export.csv", S04_DATA_DIR / "events_export.csv"],
        flat_db=DST_DB,
        iso3_to_en=flat_builder.ISO3_TO_EN_NAME,
        province_map=province_map,
    )
```

- [ ] **Step 2: 跑全套 s03 测试确认未破坏**

Run: `cd s03_data_exporter && uv run pytest -v`
Expected: PASS（含 test_stage_events、test_events_exporter 等全绿）

- [ ] **Step 3: 提交**

```bash
git add s03_data_exporter/src/s03_data_exporter/main.py
git commit -m "$(cat <<'EOF'
feat(s03): run_export 给 events 导出补传 flat_db，下载口径对齐网页

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 文档同步（项目规则）+ 真实数据重跑（GATED）

**Files:**
- Modify: `docs/status/`（最新状态文件）
- Modify: `docs/changes/`（修改日志）

- [ ] **Step 1: 同步 docs/status 与 docs/changes**

在 `docs/status` 当前状态文件记录：下载 CSV 已从事件级（拼串）改为实体级（一事件×一公司），列含公司中英文名/子公司/参与角色中英文/各自央企标志；口径对齐网页（passes_baseline + year>=2022）；前端未改。
在 `docs/changes` 追加一条带日期（2026-06-12）的修改日志，列出改动文件与行为变化、关联 spec/plan 路径。
（具体文件名先 `ls docs/status docs/changes` 确认当前活动文件，沿用既有格式。）

- [ ] **Step 2: 提交文档**

```bash
git add docs/status docs/changes
git commit -m "$(cat <<'EOF'
docs(v2.1): 记录下载 CSV 实体级重做

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3（GATED，需用户确认）: 在真实数据上重跑生成新 CSV**

> ⚠️ 前置条件，需用户确认后再做：
> - worktree 无源库；真实库在主工作区 `s02a_duplicate_merger/data/db/bri_re_database.db`（新 schema）。重跑需让 s03 的 `SRC_DB` 指向该库，且数据需处于用户认可的可发布状态（注意 MEMORY 中 P3 去重 / C2 落库尚未完成）。
> - `run_export` 第 [1/6] 步省份提取调用 Gemini（有缓存），需 `GEMINI_API_KEY`。
>
> 用户确认后：在 s03_data_exporter 目录 `uv run run_export`，完成后核对
> `s04_web_visualization/public/data/events_export.csv`：表头 == `_COLUMNS`、
> 抽查一行多公司项目的实体列、确认 2022 前事件与中港澳台项目已剔除。
> 浏览器点「下载」验证文件正确（preview workflow）。

由于数据管线仍在调整（P3/C2 未完），此步默认交给用户在数据就绪时执行；代码与测试（Task 1-3）本身已可独立验证。

---

## Self-Review

**Spec coverage：**
- §3 列规格 → Task 2 `_COLUMNS` 完整覆盖（含删除 entities、新增 7 实体列）✓
- §4 行生成（事件×公司、空实体保留、year>=2022）→ Task 2 实现 + entity_level 测试 ✓
- §5 合格项目集（基线政策逐条）→ Task 1 `passes_baseline` + 8 项单测 ✓
- §6 函数签名（新增 flat_db）→ Task 2 签名 + Task 3 接线 ✓
- §7 测试项 → Task 1/2 测试覆盖行粒度/实体列/空实体/基线/year/列集 ✓
- §9 文档同步 → Task 4 ✓

**Placeholder 扫描：** 无 TBD/TODO；所有代码步给出完整代码；Task 4 Step1 文档内容为指引（沿用既有格式，需先 ls 确认文件名）——属合理，因 docs 活动文件名随项目演进。

**类型/命名一致性：** `passes_baseline`/`_eligible_ids`/`_ENTITY_COLS`/`export_events_csv(flat_db=)` 在 Task 1-3 间一致；列名 `entity_cn/en`、`subsidiary_cn/en`、`involvement_type_cn/en`、`is_central_soe` 在 `_COLUMNS`、写入逻辑、测试断言三处一致 ✓。

**已知口径细节（实现者注意）：**
- `hasCapacity` 对空 category 返回 false → `passes_baseline` 容量检查带 `category and ...` 守卫，勿删。
- 当前无 `excluded` 类别，`is_excluded_category` 检查为前向兼容保留。
- `is_central_soe` 输出：present→"1"/"0"，None 或无公司→""。
