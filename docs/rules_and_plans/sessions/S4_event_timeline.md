# S4 实施计划：事件级时间建模（v2 核心价值）

> 上级计划：[v2_plan.md](../v2_plan.md) §三 S4
> 性质：**纯代码、不改 s02a 主库数据**（只新增 s03 导出表 + 前端消费）。中风险点在前端语义变化，需浏览器实测。
> 执行：TDD（后端聚合可单测）。完成后更新 `docs/status` 与 `docs/changes`。

**目标**：修"2024 签约、2026 竣工的项目只算进 2026 → 早期年份被掏空"。做法：新增**事件级**统计表 `stats_timeline_events`，**一个项目在它经历的每个阶段-年份各计一次**；前端趋势图改用它，分里程碑 tab（新签约/新开工/新投运），移除"项目数"单值卡片，单项目详情展示完整时间点（到月）。

**关键事实（调研确认）**：
- `stats_builder.py`（107 行）现在**只读 `projects_flat`**（已塌缩成一项目一行，丢了各阶段年份）；`build_stats(dst_db)` 由 `main.py:95` 调用。
- `flat_builder.py` 读 s02a 三表、已加载 `project_events`（带 stage + 日期）。
- 前端 `TimelineChart.tsx` 现在**自己从 `projects` 按 `event_year`（终态年）客户端聚合**，并不消费 `stats_timeline`；`statsTimeline/statsEntity/statsCountry` 三个 JSON 虽加载进 DataContext 但**无人消费**（dead data）。
- 源库 `bri_re_database.db`（s02a），导出库 `bri_re_export.db`（s03）。两个输出目录：`s03_data_exporter/data/output/` 与 `s04_web_visualization/public/data/`。

---

## 设计决策

1. **事件级表在 stats_builder 里读源库 project_events 构建**（不依赖 projects_flat）。`build_stats` 增参 `src_db`。计数用 `COUNT(DISTINCT canonical_id)`（同项目同阶段同年只计一次）。
2. **里程碑 tab** = 签约 / 开工 / 投入运营（对应"新签约/新开工/新投运"）；竣工也保留为可选 series。tab 切换 = 选当前展示的 stage。
3. **移除"项目数"**：去掉 MapPage 里以"项目总数/项目数"为标题的 KPI 卡（problems 数据呈现#2）；趋势图语义从"项目数"改为"里程碑数"，i18n 文案相应改。
4. **保留旧 `stats_timeline`**（终态分布）不删，仅前端趋势图不再用它。

---

## Task 1：stats_builder 新增事件级聚合表（TDD）

**Files:** Modify `s03_data_exporter/src/s03_data_exporter/stats_builder.py`; Modify `s03_data_exporter/src/s03_data_exporter/main.py`; Test `tests/test_stats_timeline_events.py`

- [ ] **Step 1：写失败测试**——构造临时源库+导出库，2024 签约 + 2026 竣工的单项目，断言事件级表里 2024 和 2026 各出现一次。

```python
"""事件级时间统计：一个项目在每个阶段-年份各计一次。"""
import sqlite3, pathlib, sys
from s03_data_exporter.stats_builder import build_stats

def _mk_src(p):
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE canonical_projects(id TEXT PRIMARY KEY, category TEXT, excluded INTEGER DEFAULT 0);
      CREATE TABLE project_events(id TEXT, canonical_id TEXT, project_stage TEXT,
        publish_date TEXT, investment_date TEXT, capacity_mw REAL);
    """)
    c.execute("INSERT INTO canonical_projects VALUES('IND-PV-0001','光伏',0)")
    c.execute("INSERT INTO project_events VALUES('IND-PV-0001-01','IND-PV-0001','签约','2024-03-01',NULL,50)")
    c.execute("INSERT INTO project_events VALUES('IND-PV-0001-02','IND-PV-0001','竣工','2026-09-01',NULL,50)")
    c.commit(); c.close()

def test_event_level_counts_each_milestone_year(tmp_path):
    src = tmp_path/"src.db"; dst = tmp_path/"dst.db"
    _mk_src(src)
    # dst 需有 projects_flat 才能跑旧三表；这里只验证事件级表，故造一个空 projects_flat
    d = sqlite3.connect(dst); d.execute("CREATE TABLE projects_flat(project_id TEXT, category TEXT, event_year INTEGER, latest_stage TEXT, capacity_mw REAL, primary_entity TEXT, primary_entity_en TEXT, country_iso TEXT)"); d.commit(); d.close()
    build_stats(dst, src)
    con = sqlite3.connect(dst)
    rows = con.execute("SELECT year, stage, project_count FROM stats_timeline_events ORDER BY year").fetchall()
    assert (2024, '签约', 1) in rows
    assert (2026, '竣工', 1) in rows
```

- [ ] **Step 2：跑测试确认失败**
  Run: `uv --project s03_data_exporter run python -m pytest tests/test_stats_timeline_events.py -v`
  Expected: FAIL（build_stats 仅接受 1 参 / 无 stats_timeline_events 表）

- [ ] **Step 3：实现**——`build_stats` 签名改 `build_stats(dst_db, src_db)`；末尾新增事件级表构建（读 src_db）：

```python
    # ── stats_timeline_events（事件级：每个项目在每个阶段-年份各计一次）──────────
    src = sqlite3.connect(src_db)
    src.row_factory = sqlite3.Row
    ev_rows = src.execute("""
        SELECT pe.canonical_id AS cid, cp.category AS category,
               pe.project_stage AS stage,
               COALESCE(NULLIF(pe.publish_date,''), pe.investment_date) AS d,
               pe.capacity_mw AS cap
        FROM project_events pe
        JOIN canonical_projects cp ON cp.id = pe.canonical_id
        WHERE COALESCE(cp.excluded,0) = 0
    """).fetchall()
    src.close()

    agg: dict[tuple, dict] = {}
    seen: dict[tuple, set] = {}  # 去重 (key)->set(cid)，同项目同年同阶段只计一次
    for r in ev_rows:
        d = (r["d"] or "")
        if len(d) < 4 or not d[:4].isdigit():
            continue
        year = int(d[:4])
        month = d[:7] if (len(d) >= 7 and d[4] == "-") else None
        key = (year, month, r["category"] or "", r["stage"] or "Unknown")
        bucket = agg.setdefault(key, {"count": 0, "cap": 0.0})
        s = seen.setdefault(key, set())
        if r["cid"] not in s:
            s.add(r["cid"]); bucket["count"] += 1
        if r["cap"] is not None:
            bucket["cap"] += r["cap"]

    conn.execute("DROP TABLE IF EXISTS stats_timeline_events")
    conn.execute("""
        CREATE TABLE stats_timeline_events (
            year INTEGER, month TEXT, category TEXT, stage TEXT,
            project_count INTEGER, capacity_mw REAL,
            PRIMARY KEY (year, month, category, stage)
        )
    """)
    conn.executemany(
        "INSERT INTO stats_timeline_events VALUES (?,?,?,?,?,?)",
        [(k[0], k[1], k[2], k[3], v["count"], round(v["cap"], 2))
         for k, v in agg.items()],
    )
    conn.commit()
```

  并把汇总打印列表（L102-104）加入 `"stats_timeline_events"`。

- [ ] **Step 4**：`main.py:95` 调用改 `build_stats(DST_DB, SRC_DB)`（SRC_DB 已在 main 内定义为 s02a 库路径，确认变量名后传入）。
- [ ] **Step 5**：跑测试转绿。Run 同 Step 2。Expected: PASS。提交。

## Task 2：json_exporter 导出新表

**Files:** Modify `s03_data_exporter/src/s03_data_exporter/json_exporter.py:53`

- [ ] **Step 6**：把 `"stats_timeline_events"` 加进导出表名列表（L52-58 的 for 循环）。
- [ ] **Step 7**：跑 `uv run run_export`，确认两个输出目录都生成 `stats_timeline_events.json` 且非空。提交。

## Task 3：前端类型 + 加载

**Files:** Modify `s04_web_visualization/src/types/index.ts`, `src/contexts/DataContext.tsx`

- [ ] **Step 8**：types 加接口：

```typescript
export interface StatTimelineEvent {
  year: number;
  month: string | null;
  category: string;
  stage: string;
  project_count: number;
  capacity_mw: number | null;
}
```

- [ ] **Step 9**：DataContext `Promise.all` 加载 `stats_timeline_events.json`（L143-149），context value 暴露 `statsTimelineEvents`（L193-195 区）。

## Task 4：TimelineChart 改事件级 + 里程碑 tab + 移除项目数

**Files:** Modify `s04_web_visualization/src/components/charts/TimelineChart.tsx`, `src/pages/MapPage.tsx`, `src/i18n/zh.ts`+`en.ts`, `src/components/projects/ProjectDetail.tsx`

- [ ] **Step 10**：`TimelineChart` 改为消费 `statsTimelineEvents`（经 props 或 useData），按选中 stage（tab）过滤后 `按 year × category` 堆叠。加 tab 状态：`签约 | 开工 | 投入运营`（默认签约）。数据需经当前筛选（国家/类别/年份）联动——若 stats 表无法按国家筛，则保持 TimelineChart 仍接收 `filteredProjects` 不可行（事件级数据不在 projects 里）；**决策**：事件级趋势图按全局（不随地图国家筛选联动）或在 stats 表加 country_iso 维度。本计划采用**在 stats_timeline_events 加 `country_iso` 维度**（Task 1 的 key 加一列），前端按选中国家过滤；若无选中国家则汇总。
  > 注：若 country 维度使表过大或暂不需要联动，可先不加 country_iso，趋势图走全局口径并在 UI 注明。执行时二选一，**默认加 country_iso**（联动体验更好）。
- [ ] **Step 11**：移除"项目数"单值展示：MapPage 中以项目总数为标题的 KPI 卡删除/改为"里程碑数"；趋势图标题 i18n `timelineTitle` 改为"各年各里程碑数趋势"，`chart.projects` 文案改"里程碑数"。
- [ ] **Step 12**：`ProjectDetail.tsx` 展示完整时间点：用 `timeline_notes`（已有"签约: 2022-03 → 开工: 2023-06"格式）或解析 all events，确保签约/开工/竣工三个关键时间点到月可见。
- [ ] **Step 13**：`npm run build` + `tsc --noEmit` 通过。提交。

## Task 5：验证与收尾

- [ ] **Step 14**：`pytest tests/` 全绿；导出后 `SELECT * FROM stats_timeline_events WHERE year=2024 AND stage='签约'` 有数据。
- [ ] **Step 15（需人工浏览器实测）**：起 `npm run dev`，确认：① 2024 年的"签约"里程碑出现在 2024（不再被算到竣工年）；② 早期年份不再被掏空；③ tab 切换正确；④ 项目数卡片已移除；⑤ 单项目详情时间点完整。
- [ ] **Step 16**：更新 docs/status（s03/s04）+ docs/changes 记录 S4。提交。

---

## 验证清单（DoD）
- [ ] `stats_timeline_events` 表/JSON 生成，同项目跨阶段在多个年份各计一次。
- [ ] 趋势图按里程碑 tab 展示，早期年份不再被掏空。
- [ ] 项目数单值卡已移除；单项目详情时间点到月。
- [ ] build/tsc 通过；**浏览器实测通过（人工）**。
- [ ] docs/status 与 docs/changes 已更新。

## 风险
- 不改 s02a 主库数据，低数据风险；主要风险是前端语义/联动，**必须人工浏览器实测**（自动化只能验证 build + 数据断言）。
- country_iso 维度二选一在 Task 4 Step 10 决策。
