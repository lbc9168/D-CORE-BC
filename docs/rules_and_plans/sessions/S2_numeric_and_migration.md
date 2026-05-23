# S2 实施计划：数值化 schema + 储能字段 + 迁移 + 脏值清洗 + 非 RE 排除

> 上级计划：[v2_plan.md](../v2_plan.md) §三 S2
> **建议拆成两个会话执行**（S2 体量最大、最吃风险）：
> - **S2a（纯代码，前向路径）**：schema + 解析 + 写入/读取逻辑，不动现有 DB。Task 1-7。
> - **S2b（一次性数据操作）**：迁移现有 `bri_re_database.db`（副本→主库），含数值回填、储能正则、脏枚举清洗、非 RE 排除。Task 8-12。
> S2b 依赖 S2a 定下的最终 schema；S2a 跑完再开 S2b。
> 完成后更新 `docs/status` 与 `docs/changes`。

**目标**：把容量/投资额/分包额从 TEXT 真正数值化为 REAL（保留 `*_raw` 原文）、新增储能与 capacity_source 字段、修复投资额"取均值"错误；并把现有核心库 `bri_re_database.db` 一次性迁移到位（含脏枚举归一与 ≈25 个非 RE 项目排除）。

**关键设计**：
- **数值列方案**：每个数值字段 `X`（TEXT）→ 重命名为 `X_raw`（TEXT，存原文）+ 新增 `X REAL`（解析值，解析失败 NULL）。原因：SQLite TEXT 亲和性无法原地转 REAL，必须新列 + 重命名。`X` 此后处处表示"数值"，`X_raw` 是审计原文。
- **解析器单一可信源**：新建根 `bri_parse.py`，`_parse_number()` 处理 "约50"/"50-60MW"/"1,200"/"50 MW"/范围/含单位/空。s01b、s02a、s03、迁移脚本全部复用（DRY，呼应 enums 的做法）。
- **非 RE 排除**：`canonical_projects` 加 `excluded INTEGER DEFAULT 0` + `excluded_reason TEXT`；s03 flat_builder 过滤 `excluded=0`。非 RE 集合独立定义，**不走** clean_historical_data 的"→Unknown"。

**涉及数值字段清单**：
- s01b `extracted_projects`：`capacity_mw`、`subcontract_amount_usd`、`total_investment_usd`。
- s02a `project_events`：`capacity_mw`、`total_investment_usd`；`chinese_entities`：`subcontract_amount`；`non_project_records`：`capacity_mw`、`subcontract_amount`（一并处理保持一致）。
- 新增（s01b 与 s02a project_events 同步）：`storage_power_mw REAL`、`storage_energy_mwh REAL`、`capacity_source TEXT`。

---

## 设计决策（执行前确认）

1. **`X_raw` 命名**：`capacity_mw_raw` / `total_investment_usd_raw` / `subcontract_amount_raw` / `subcontract_amount_usd_raw`（s01b 该列名带 _usd）。
2. **capacity_source 取值**：`original`（原文给出数值）/ `derived`（由年发电量反推，v2.1 才会产生）/ `manual` / `legacy`（历史迁移无法判定）。S2 历史行一律 `legacy`；新提取暂填 `original`（反推逻辑属 v2.1，本次不做）。
3. **投资额代表值**：弃用"所有事件均值"，改为**取代表事件（终态事件）的 total_investment_usd**；若代表事件为空，取最新有值事件。单位维持百万美元。
4. **storage 历史回填**：仅正则尽力提取，提不出留 NULL（已与用户确认）。
5. **非 RE 项目**：标记 `excluded=1, excluded_reason='non_renewable'`，不移动行（保留可追溯、可逆），s03 过滤。

---

## Phase A —— S2a（纯代码，不动现有数据）

### Task 1：共享数值解析器 `bri_parse.py`（TDD）

**Files:** Create `bri_parse.py`（repo 根）, `tests/test_parse_number.py`

- [ ] **Step 1：写失败测试**

```python
"""数值解析单一可信源测试。"""
import bri_parse as p

def test_plain():
    assert p.parse_number("50") == 50.0
    assert p.parse_number("50.5") == 50.5

def test_with_unit_and_space():
    assert p.parse_number("50 MW") == 50.0
    assert p.parse_number("100MW") == 100.0
    assert p.parse_number("1,200") == 1200.0

def test_approx():
    assert p.parse_number("约50") == 50.0
    assert p.parse_number("约 50 兆瓦") == 50.0

def test_range_takes_midpoint():
    assert p.parse_number("50-60") == 55.0
    assert p.parse_number("50-60MW") == 55.0
    assert p.parse_number("50~60") == 55.0

def test_unparseable_returns_none():
    assert p.parse_number("") is None
    assert p.parse_number(None) is None
    assert p.parse_number("待定") is None
    assert p.parse_number("nan") is None

def test_negative_and_zero():
    assert p.parse_number("0") == 0.0
```

- [ ] **Step 2：跑测试确认失败**
  Run: `uv --project s02a_duplicate_merger run python -m pytest tests/test_parse_number.py -v`
  Expected: FAIL（ModuleNotFoundError: bri_parse）

- [ ] **Step 3：实现 `bri_parse.py`**

```python
"""数值字段解析单一可信源：把脏文本（约50 / 50-60MW / 1,200）解析为 float。"""
import re
from typing import Any, Optional

_NA = {"", "nan", "none", "na", "n/a", "<na>", "待定", "未知", "未披露", "—", "-"}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")

def parse_number(val: Any) -> Optional[float]:
    """解析数值文本为 float；范围取中点；失败返回 None。"""
    if val is None:
        return None
    s = str(val).strip().lower().replace(",", "").replace("，", "")
    if s in _NA:
        return None
    nums = _NUM.findall(s)
    if not nums:
        return None
    try:
        vals = [float(n) for n in nums]
    except ValueError:
        return None
    # 形如 50-60 / 50~60 的范围取中点（仅当原串含连接符且恰两个数）
    if len(vals) == 2 and re.search(r"\d\s*[-~～]\s*\d", s):
        return round((vals[0] + vals[1]) / 2, 6)
    return vals[0]
```

- [ ] **Step 4：跑测试转绿。** Run 同 Step 2。Expected: PASS
- [ ] **Step 5：提交。** `git add bri_parse.py tests/test_parse_number.py && git commit -m "feat(parse): 数值解析单一可信源 bri_parse"`

### Task 2：s01b schema + 写入数值化

**Files:** Modify `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`

- [ ] **Step 6**：`CREATE TABLE extracted_projects`（L284-323）改：把 `capacity_mw TEXT` 等三个数值列保留为 `*_raw TEXT`，并加 REAL 列与储能列：
  - 把 `capacity_mw TEXT` → `capacity_mw_raw TEXT, capacity_mw REAL`
  - `subcontract_amount_usd TEXT` → `subcontract_amount_usd_raw TEXT, subcontract_amount_usd REAL`
  - `total_investment_usd TEXT` → `total_investment_usd_raw TEXT, total_investment_usd REAL`
  - 末尾加 `storage_power_mw REAL, storage_energy_mwh REAL, capacity_source TEXT`
- [ ] **Step 7**：`_init` 的 ALTER 兼容列表（L326-333）补齐上述新列的 `ALTER TABLE ... ADD COLUMN`（idempotent，捕获 `sqlite3.OperationalError` —— 按 CLAUDE.md 规则1，此处为"列已存在"的已知无害边界，保留 try/except 但加注释）。
- [ ] **Step 8**：顶部加 `bri_parse` 导入 shim（上溯找根，同 S1 bri_enums 写法）。
- [ ] **Step 9**：`INSERT INTO extracted_projects`（L939-）：原 `capacity_mw` 值改写入 `capacity_mw_raw`（原文）+ `capacity_mw=parse_number(原文)`；投资额、分包额同理；`capacity_source` 暂填 `'original'`；storage 暂不提取（提取属 v2.1，留空）。
- [ ] **Step 10**：写最小单测：构造一行含 `"约50MW"` 的提取结果，跑入库，断言 `capacity_mw==50.0` 且 `capacity_mw_raw=="约50MW"`。提交。

### Task 3：s02a schema 数值化 + excluded 列

**Files:** Modify `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py`

- [ ] **Step 11**：`SCHEMA_SQL`（L62-140）：
  - `project_events`：`capacity_mw TEXT`→`capacity_mw_raw TEXT, capacity_mw REAL`；`total_investment_usd TEXT`→`total_investment_usd_raw TEXT, total_investment_usd REAL`；加 `storage_power_mw REAL, storage_energy_mwh REAL, capacity_source TEXT`。
  - `chinese_entities`：`subcontract_amount TEXT`→`subcontract_amount_raw TEXT, subcontract_amount REAL`。
  - `non_project_records`：`capacity_mw`、`subcontract_amount` 同样拆 raw+REAL。
  - `canonical_projects`：加 `excluded INTEGER DEFAULT 0, excluded_reason TEXT`。
- [ ] **Step 12**：`_init_db` 的 ALTER 列表（L176-185）补齐全部新列（idempotent）。
- [ ] **Step 13**：加 `bri_parse` 导入 shim；`_parse_capacity`（L151-156）改为 `return bri_parse.parse_number(val)`（删除本地简易实现，复用单一可信源）。
- [ ] **Step 14**：所有写 `capacity_mw` / `total_investment_usd` / `subcontract_amount` 的 INSERT/UPDATE（L1107-1122、L1150-1165、L1189-1199、L1310-1340 等，见 §写入点清单）：原文写入 `*_raw`，`parse_number(原文)` 写入 REAL 列；`capacity_source='original'`。
- [ ] **Step 15**：冒烟测试：import 模块 + 跑一次小批量去重到临时 DB，断言 project_events.capacity_mw 为数值类型、capacity_mw_raw 保留原文。提交。

> **写入点清单（capacity_mw / total_investment_usd / subcontract_amount）**：duplicate_merger_tool.py 约 L1107-1122（UPDATE 合并）、L1150-1165（INSERT event）、L1189-1199（INSERT entity）、L1310-1340（写 non_project / 其它）。逐处把"写原值"改成"原值→_raw、parse→REAL"。

### Task 4：s02a 去重逻辑改用 REAL 列

**Files:** Modify 同上

- [ ] **Step 16**：容量比较处（L540-577 Signal 4 / 容差）、容量守卫 `_capacity_guard`（L926-939）、canonical 代表容量取值（L1013-1021）：现在 `capacity_mw` 已是 REAL，去掉运行时 `_parse_capacity(text)` 的二次解析，直接用数值列（仍用 `bri_parse` 兜底处理 None）。canonical 代表容量取值 `LIMIT 1` 改为优先取终态事件（与 check-record §3.2#7 一并修：`ORDER BY` 加 stage 优先级；stage 优先级从 `enums.stage_priority` 来）。
- [ ] **Step 17**：单测覆盖：两条 capacity 接近（5% 容差内）的事件应判同项目候选。提交。

### Task 5：s03 flat_builder 读 REAL + 修投资额 + 储能透传

**Files:** Modify `s03_data_exporter/src/s03_data_exporter/flat_builder.py`

- [ ] **Step 18**：`_parse_capacity`（L101-107）改为复用 `bri_parse.parse_number`（或直接读 REAL 列，因 capacity_mw 现已是 REAL；保留 parse 兜底旧数据）。容量选取（L176-182）逻辑不变。
- [ ] **Step 19**：`total_investment_usd` 均值（L251-261）**改为代表值**：取 `latest`（终态代表事件）的 `total_investment_usd`；为空则取"最新有值事件"。删除求平均逻辑。
- [ ] **Step 20**：`projects_flat` 加列 `storage_power_mw REAL, storage_energy_mwh REAL, capacity_source TEXT`（CREATE_TABLE_SQL L42-77 + 行 dict L280-317 同步）；取代表事件的对应值。
- [ ] **Step 21**：读 canonical 时加 `WHERE excluded = 0`（或读后过滤），非 RE 项目不进 projects_flat。
- [ ] **Step 22**：跑 s03 导出（对 S2b 迁移后的副本 DB），确认 projects_flat 数值列正确、投资额不再是均值、excluded 项目已消失。提交。

### Task 6：前端储能视图（最小，可选延后）

**Files:** Modify `s04_web_visualization`（视情况）

- [ ] **Step 23**：projects_flat 新增 storage 字段经 json_exporter 导出后，前端 zod schema（若有）补字段；储能专门视图属增量，**v2 可只保证不报错**，详细展示留后续。`npm run build` 通过即可。提交。

### Task 7：S2a 收尾

- [ ] **Step 24**：跑 `pytest tests/`（含 S1 + S2a 新测试）全绿；`npm run build` 通过。
- [ ] **Step 25**：更新 docs/status（s01b/s02a/s03）+ docs/changes 记录 S2a。提交。

---

## Phase B —— S2b（一次性迁移现有 `bri_re_database.db`）

> 全程在**副本**上做；验证通过后再对主库操作，**动主库前 `.bak` 备份**。
> 主库路径（主工作区，不在 worktree）：`E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db`。
> Windows 查中文用 `py`，控制台 GBK 会乱码，需 UTF-8 写文件再看。

### Task 8：迁移脚本骨架 + schema 升级

**Files:** Create `scripts/migrate_v2.py`

- [ ] **Step 26**：脚本接受 `--db <path>` 与 `--apply`（默认 dry-run）。先 `shutil.copy` 出 `.bak`（带时间戳）。
- [ ] **Step 27**：schema 升级函数：对 project_events / chinese_entities / non_project_records 执行 `ALTER TABLE ... RENAME COLUMN capacity_mw TO capacity_mw_raw` 等（SQLite ≥3.25）；`ADD COLUMN capacity_mw REAL` 等 REAL 列、storage、capacity_source；canonical_projects `ADD COLUMN excluded INTEGER DEFAULT 0` + `excluded_reason TEXT`。全部 idempotent（捕获 OperationalError 已存在，加注释）。

### Task 9：数值回填 + 储能正则

- [ ] **Step 28**：用 `bri_parse.parse_number` 把各 `*_raw` 解析回填到 REAL 列；`capacity_source='legacy'`。批量 `executemany`、单事务（CLAUDE.md 规则3）。
- [ ] **Step 29**：储能正则从 `capacity_details` 尽力提取，提不出留 NULL：

```python
import re
_PWR = re.compile(r"(?:储能|bess|储电)[^0-9]{0,8}(\d+(?:\.\d+)?)\s*mw(?!h)", re.I)
_ENG = re.compile(r"(\d+(?:\.\d+)?)\s*mwh", re.I)
def extract_storage(details: str):
    if not details: return (None, None)
    p = _PWR.search(details); e = _ENG.search(details)
    return (float(p.group(1)) if p else None, float(e.group(1)) if e else None)
```

- [ ] **Step 30**：dry-run 打印解析成功/失败计数，人工抽查 20 条 raw vs 解析值。

### Task 10：脏枚举清洗（适配 s02a 三表）

- [ ] **Step 31**：复用 `clean_historical_data.py` 的 `STAGE_MAP / INVOLVEMENT_MAP / CATEGORY_MAP`（import 或拷入），但 **CATEGORY_MAP 去掉非 RE→Unknown 的条目**（非 RE 走 Task 11 排除）。保留合法重分类：光热→综合能源、抽水蓄能→水电、水利水渠→水电、垃圾发电→生物质、新能源→综合能源。
- [ ] **Step 32**：对应三表各自字段做 `UPDATE`（用 `IS` 匹配 NULL，单事务）：
  - `canonical_projects.category` ← CATEGORY_MAP（仅重分类项）
  - `project_events.project_stage` ← STAGE_MAP
  - `chinese_entities.involvement_type` ← INVOLVEMENT_MAP
  - `project_events.financing_model`：无现成 map，**新建 FINANCING_MAP**：`{"PPP":"其他","BOT":"其他","政府和社会资本合作（PPP）":"其他","公私合作伙伴关系":"其他","业主自筹":"其他","":"Unknown"}`（取值见 _db_enum_values.txt）。
- [ ] **Step 33**：清洗后断言：四列 distinct 值 ⊆ `enums.valid_*()` 规范集（不满足则报哪个值漏映射，便于补 map）。

### Task 11：非 RE 项目排除

- [ ] **Step 34**：定义非 RE 集合并标记：

```python
NON_RE = {"石油","石油化工","石油开采","石油开采与处理","石油和天然气",
          "油气","油气开发","油气处理","油气服务","煤炭",
          "矿产开发","矿产资源","矿产资源开发","矿产开采","矿业开发",
          "工业园区","工业安装","基础设施建设","农业灌溉","房建"}
# UPDATE canonical_projects SET excluded=1, excluded_reason='non_renewable' WHERE category IN (...)
```

- [ ] **Step 35**：dry-run 打印将排除的项目数（应 ≈25）与清单，人工确认无误杀。

### Task 12：S2b 验证与落库

- [ ] **Step 36**：在副本上：跑完整迁移 → 跑 s03 导出 → `npm run build` + 浏览器实测，确认数值/储能/排除/枚举全部正确，且手动修正的 8 条记录仍在。
- [ ] **Step 37**：备份主库 `.bak`，对主库 `--apply` 跑迁移；再次抽查。
- [ ] **Step 38**：更新 docs/status + docs/changes 记录 S2b 完成；提交脚本与文档（DB 不入 git）。

---

## 验证清单（DoD）

- [ ] `pytest tests/`（parse + enums + s02a/s03 新测试）全绿。
- [ ] s02a 写入：capacity_mw/investment/subcontract 为 REAL，`*_raw` 保留原文。
- [ ] s03：投资额为代表值（非均值）；projects_flat 含 storage 字段；excluded 项目已剔除。
- [ ] 迁移后 s02a 四个枚举列 distinct ⊆ 规范集；非 RE ≈25 项已 excluded；手动修正记录无损。
- [ ] 主库迁移前已 `.bak`；副本验证先行。
- [ ] docs/status 与 docs/changes 已更新。

## 风险与回滚

- **最高风险在 S2b 改主库**：务必副本先行 + `.bak`。出错用 `.bak` 还原。
- 列重命名后旧代码若直接读 `capacity_mw` 期望文本会拿到数值——S2a 已把所有读点切到 REAL/`_raw`，执行时 grep `capacity_mw` 确认无遗漏文本依赖。
- FINANCING_MAP 是新写的小表，dry-run 必看未覆盖值。
- 储能正则召回率低是预期（已与用户确认提不出留空）。
