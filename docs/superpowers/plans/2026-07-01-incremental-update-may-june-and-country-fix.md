# 2026-05/06 增量更新 + CSV country_en 修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 用 superpowers:executing-plans 分阶段执行（本任务含实时流水线运行，须在主会话内串行执行、每阶段一个人工检查点）。Part A 走 TDD；Part B 为分阶段运维，每阶段以"数据断言"作为验证。

**Goal:** 把主库从"数据截至 2026-04-30"增量更新到 2026-06-30，并修复导出 CSV/JSON 中 `country_en` 全空的路径 bug，最后部署到 GitHub Pages。

**Architecture:** 流水线本就按增量设计（raw_news 按 URL 去重跨批持久化；s01b 只提取新 URL；s02a 只处理 `imported_to_s02a=0` 并并入既有主库）。因此设 `start_date=2026-05-01 / end_date=2026-06-30` 分阶段跑 s01a→s01b→s02a→s02b，跳过 s02c，再 s03 导出、s04 部署。country_en 空是 `flat_builder.py` 一处 `parents[5]` 应为 `parents[3]` 的路径 bug，先修再跑 s03 一次产出正确输出。

**Tech Stack:** Python（各子项目 uv 管理各自 venv）、SQLite、Gemini + DeepSeek（s01b 分阶段路由）、pytest、React/Vite（s04）。

## Global Constraints

- 主库、projects.db、raw_news.db 在写操作前必须先备份（带时间戳副本）。
- 交叉验证 s02c 全程**跳过**（`run_cross_verification=False`，本就默认关）。
- s01b LLM 路由保持 `.env` 现状（`S01B_STAGE_PROVIDERS` = S3/S4/S5→DeepSeek），与既有库提取口径一致，本次不改。
- 编码规则：禁止静默吞异常；批量写库用事务；每 function 一句注释。
- 爬取日期范围精确为 `2026-05-01` 至 `2026-06-30`（含）。
- 各阶段命令在**对应子项目目录**下用其自身 `uv` 环境运行。
- 每阶段结束设人工检查点，汇报数据断言，用户确认后再进下一阶段。

---

## Part A — country_en 路径 bug 修复（TDD，先做）

### Task A1: 修复 ISO3→英文名映射加载路径 + 消除静默吞异常

**Files:**
- Modify: `s03_data_exporter/src/s03_data_exporter/flat_builder.py:26-41`
- Test: `s03_data_exporter/tests/test_flat_builder_numeric.py`（追加测试）

**Interfaces:**
- Consumes: `s01b_project_extractor/WorldCountryList.json`（数组，每项含 `iso` / `name_en`）。
- Produces: `flat_builder.ISO3_TO_EN_NAME: dict[str,str]`（非空，`"CHL"→"Chile"`）；被 `flat_builder.build_flat`（projects_flat.country_en）与 `main.py` 注入 `events_exporter.export_events_csv(iso3_to_en=...)` 共用。

- [ ] **Step 1: 写失败测试**（追加到 `test_flat_builder_numeric.py` 末尾）

```python
def test_iso3_to_en_name_loaded():
    """路径修复回归测试：ISO3→英文名映射必须成功加载且含已知国家。"""
    from s03_data_exporter import flat_builder
    assert len(flat_builder.ISO3_TO_EN_NAME) > 100          # WorldCountryList 有 200+ 国
    assert flat_builder.ISO3_TO_EN_NAME.get("CHL") == "Chile"
    assert flat_builder.ISO3_TO_EN_NAME.get("CHN") == "China"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv --project s03_data_exporter run pytest s03_data_exporter/tests/test_flat_builder_numeric.py::test_iso3_to_en_name_loaded -v`
Expected: FAIL（`parents[5]` 指到 `E:\`，文件找不到 → 映射为空 → `len==0`）

- [ ] **Step 3: 修路径 + 记录异常**（改 `flat_builder.py` 顶部）

把：
```python
_WORLD_LIST_PATH = (
    Path(__file__).resolve().parents[5]
    / "s01b_project_extractor"
    / "WorldCountryList.json"
)

def _load_iso3_to_en() -> dict[str, str]:
    try:
        with open(_WORLD_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {c["iso"]: c["name_en"] for c in data if c.get("iso") and c.get("name_en")}
    except Exception:
        return {}
```
改为（`parents[3]` = 仓库根；异常改为 logging 记录路径与原因，不再静默）：
```python
import logging

_WORLD_LIST_PATH = (
    Path(__file__).resolve().parents[3]      # s03_data_exporter/src/s03_data_exporter → 仓库根
    / "s01b_project_extractor"
    / "WorldCountryList.json"
)

def _load_iso3_to_en() -> dict[str, str]:
    """从 WorldCountryList.json 读 {ISO3: 英文名}；文件缺失/解析失败时记录错误并返回空表。"""
    try:
        with open(_WORLD_LIST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {c["iso"]: c["name_en"] for c in data if c.get("iso") and c.get("name_en")}
    except (OSError, json.JSONDecodeError) as e:
        logging.error("加载 WorldCountryList 失败 path=%s err=%s", _WORLD_LIST_PATH, e)
        return {}
```
（`parents[3]` 校验：`flat_builder.py` 位于 `s03_data_exporter/src/s03_data_exporter/`，`parents[0..2]`=…/s03_data_exporter，`parents[3]`=仓库根 `BRI_RE_Database_v2`。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv --project s03_data_exporter run pytest s03_data_exporter/tests/test_flat_builder_numeric.py::test_iso3_to_en_name_loaded -v`
Expected: PASS

- [ ] **Step 5: 全量回归 s03 测试**

Run: `uv --project s03_data_exporter run pytest s03_data_exporter/tests/ -v`
Expected: 全绿（含 test_events_exporter / test_stage_events / test_flat_builder_numeric）

- [ ] **Step 6: 提交**

```bash
git add s03_data_exporter/src/s03_data_exporter/flat_builder.py s03_data_exporter/tests/test_flat_builder_numeric.py
git commit -m "fix(s03): 修 country_en 映射路径 parents[5]→[3] 并消除静默吞异常"
```

---

## Part B — 增量数据更新（分阶段，每阶段一检查点）

### Task B0: 预检与备份

- [ ] **Step 1: 备份三个库（带时间戳）**

```bash
cd /e/Projects/BRI_RE_Database_v2
cp s02a_duplicate_merger/data/db/bri_re_database.db s02a_duplicate_merger/data/db/bri_re_database.db.bak.20260701
cp s01b_project_extractor/data/projects.db s01b_project_extractor/data/projects.db.bak.20260701
cp s01a_news_crawler/data/raw_news.db s01a_news_crawler/data/raw_news.db.bak.20260701
```

- [ ] **Step 2: 记录基线计数（更新前）**

```bash
python -c "import sqlite3; c=sqlite3.connect('s02a_duplicate_merger/data/db/bri_re_database.db'); print('canonical',c.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0]); print('events',c.execute('SELECT COUNT(*) FROM project_events').fetchone()[0]); print('entities',c.execute('SELECT COUNT(*) FROM chinese_entities').fetchone()[0]); print('max_pub',c.execute('SELECT MAX(publish_date) FROM project_events').fetchone()[0])"
```
基线（已知）：canonical=4407, events=5218, entities=6316, max_pub=2026-04-30。

- [ ] **Step 3: goalfore.cn 可达性探测**

Run: `python -c "import requests; r=requests.get('https://news.goalfore.cn/topstories/project?page=1', headers={'User-Agent':'Mozilla/5.0'}, timeout=20); print(r.status_code, len(r.text))"`
Expected: 200 + 非空 HTML。**检查点**：可达则继续；不可达则停下与用户确认网络。

### Task B1: 阶段1 — s01a 爬虫（2026-05-01 → 2026-06-30）

- [ ] **Step 1: 后台运行单批爬取**

```bash
cd /e/Projects/BRI_RE_Database_v2/s01a_news_crawler
uv run python -c "from s01a_news_crawler.main import run_single; print(run_single('2026-05-01','2026-06-30'))"
```
（后台执行 + 监控日志；预计 30–60 分钟。新 batch_id=`2026-05-01_2026-06-30`，从 page 1 起，翻到 <2026-05-01 连续 5 条自动停。）

- [ ] **Step 2: 检查点 — 断言爬取结果**

```bash
python -c "import sqlite3; c=sqlite3.connect('s01a_news_crawler/data/raw_news.db'); print('新批次行数', c.execute(\"SELECT COUNT(*) FROM raw_news WHERE batch_id='2026-05-01_2026-06-30'\").fetchone()); print('2026-05/06 月分布', c.execute(\"SELECT substr(publish_date,1,7), COUNT(*) FROM raw_news WHERE publish_date LIKE '2026-05%' OR publish_date LIKE '2026-06%' GROUP BY 1\").fetchall())"
```
**检查点**：确认新增了 5–6 月的 raw_news 行、覆盖到 6 月底；汇报后经用户确认再继续。

### Task B2: 阶段2 — s01b AI 提取（仅新 URL）

- [ ] **Step 1: 运行批量提取**

```bash
cd /e/Projects/BRI_RE_Database_v2/s01b_project_extractor
uv run run_bulk_extract
```
（默认读 s01a 的 raw_news.db，运行前预加载已处理 URL 只提取新的；自动 .bak 备份 projects.db。DeepSeek 路由不变。）

- [ ] **Step 2: 检查点 — 断言新增提取**

```bash
python -c "import sqlite3; c=sqlite3.connect('s01b_project_extractor/data/projects.db'); print('未导入(待s02a)', c.execute('SELECT COUNT(*) FROM extracted_projects WHERE imported_to_s02a=0').fetchone()); print('新提取max_pub', c.execute('SELECT MAX(publish_date) FROM extracted_projects').fetchone())"
```
**检查点**：确认出现 `imported_to_s02a=0` 的新项目、max_pub 进到 6 月；汇报后继续。

### Task B3: 阶段3 — s02a 去重合并入主库

- [ ] **Step 1: 运行去重合并**

```bash
cd /e/Projects/BRI_RE_Database_v2/s02a_duplicate_merger
uv run s02a_duplicate_merger
```
（只处理 `imported_to_s02a=0`，预加载既有 canonical 缓存并入主库，回写标记。）

- [ ] **Step 2: 检查点 — 断言主库增量**

```bash
python -c "import sqlite3; c=sqlite3.connect('s02a_duplicate_merger/data/db/bri_re_database.db'); print('canonical',c.execute('SELECT COUNT(*) FROM canonical_projects').fetchone()[0]); print('events',c.execute('SELECT COUNT(*) FROM project_events').fetchone()[0]); print('entities',c.execute('SELECT COUNT(*) FROM chinese_entities').fetchone()[0]); print('max_pub',c.execute('SELECT MAX(publish_date) FROM project_events').fetchone()[0]); print('新增2026-05/06事件', c.execute(\"SELECT COUNT(*) FROM project_events WHERE publish_date LIKE '2026-05%' OR publish_date LIKE '2026-06%'\").fetchone()[0])"
```
**检查点**：canonical/events/entities 均较基线增长、max_pub 到 6 月；抽查 2–3 条新项目名称/国家/容量合理；`projects.db` 应无残留 `imported_to_s02a=0`。汇报后继续。

### Task B4: 阶段4 — s02b 实体+地点归一化（原地更新主库）

- [ ] **Step 1: 实体名归一化**

```bash
cd /e/Projects/BRI_RE_Database_v2/s02b_entity_normalizer
uv run normalize_entities
```

- [ ] **Step 2: 地点归一化**（写 `location_norm`，不动 `location`；下游不消费，软信息）

先确认入口：`uv run` 脚本列表见 `s02b_entity_normalizer/pyproject.toml`；若有 `normalize_locations` 脚本则直接跑，否则用 one-liner：
```bash
uv run python -c "from s02b_entity_normalizer.location_normalizer import run; run(db_path='../s02a_duplicate_merger/data/db/bri_re_database.db')"
```

- [ ] **Step 3: 检查点**

汇报实体/地点归一对数（脚本自身打印）。无报错即继续。

### Task B5: （跳过 s02c 交叉验证）

- [ ] 确认不运行 s02c。无操作。

### Task B6: 阶段5 — s03 导出（含 country_en 修复验证）

- [ ] **Step 1: 运行导出**

```bash
cd /e/Projects/BRI_RE_Database_v2/s03_data_exporter
uv run run_export
```
（省份提取带缓存，仅新项目调 API；重算 projects_flat + 3 stats + JSON/CSV + events_export.csv，自动同步 s04/public/data。）

- [ ] **Step 2: 检查点 — 断言 country_en 已补 + 数据到 6 月**

```bash
python -c "
import json,csv
d=json.load(open('s03_data_exporter/data/output/projects_flat.json',encoding='utf-8'))
rows=d if isinstance(d,list) else d.get('projects',d.get('data',[]))
empt=sum(1 for r in rows if not r.get('country_en'))
print('projects_flat country_en 空的:', empt, '/', len(rows))
with open('s04_web_visualization/public/data/events_export.csv',encoding='utf-8-sig') as f:
    rd=list(csv.DictReader(f)); ce=sum(1 for r in rd if not r['country_en'])
    print('events_export country_en 空的:', ce, '/', len(rd))
print('metadata', json.load(open('s03_data_exporter/data/output/metadata.json',encoding='utf-8')))
"
```
**检查点**：两个 country_en 空数应大幅降为 0（仅缺失/无 ISO 的极少数除外）；`metadata.last_updated` 到 2026-06。汇报后继续。

### Task B7: 阶段6 — s04 构建与部署 GitHub Pages

- [ ] **Step 1: 确认省级 geo 已存在**（无则见 s04 README 用 `download_geo.py`）

```bash
ls s04_web_visualization/public/geo/provinces/ | head
```

- [ ] **Step 2: 构建**

```bash
cd /e/Projects/BRI_RE_Database_v2/s04_web_visualization
npm install
npm run build
```
Expected: `dist/` 生成，无报错（predev/prebuild 会自动 copy-enums）。

- [ ] **Step 3: 部署到 gh-pages**（按子项目 `DEPLOY.md` 的确切流程执行；核对分支/命令后再推）

- [ ] **Step 4: 检查点 — 线上核对**

打开线上站点确认：数据表 CSV 下载的 country 列有值、Overview/地图含 5–6 月新数据、About 页"最后更新"到 6 月。

### Task B8: 收尾文档（按 CLAUDE.md 规则）

- [ ] **Step 1:** `docs/changes/2026-W27a.md`（或当周文件）追加一条：日期·模块(s01a-s04+s03 bugfix)·做了什么·为什么·影响文件/表。
- [ ] **Step 2:** 覆盖更新 `docs/status/s03.md`（country_en 修复属输出契约层面）；其余 status 若运行方式/契约未变则不动。
- [ ] **Step 3:** 提交文档变更。

---

## Self-Review

- **Spec 覆盖**：Part A 覆盖 country 修复（路径+静默异常+TDD+回归）；Part B 覆盖 s01a→s01b→s02a→s02b→(跳s02c)→s03→s04 全链路 + 备份 + 部署 + 文档。✅
- **Placeholder 扫描**：Task B4 Step2 地点归一入口、B7 Step3 部署命令标注"执行时按 pyproject/DEPLOY.md 核对"——因这两处确切命令依赖运行时文件内容，已给出 fallback one-liner 与核对来源，非空泛占位。其余命令均具体。
- **类型一致性**：`ISO3_TO_EN_NAME`、`_load_iso3_to_en`、`export_events_csv(iso3_to_en=...)` 前后一致。✅
- **回退**：三库均有 `.bak.20260701` 备份；爬虫新批次可按 batch_id 删除；s02a/s02b 原地写前有主库备份。✅
