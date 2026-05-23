# S1 实施计划：枚举单一可信源（纯代码，不动数据）

> 上级计划：[v2_plan.md](../v2_plan.md) §三 S1
> 性质：**纯代码重构，零数据改动**（DB 脏值清洗在 S2）。低风险。
> 执行方式：TDD —— 先写"一致性测试"，再重构到测试通过。
> 完成后更新 `docs/status` 与 `docs/changes`。

**目标（一句话）**：把所有枚举（category / project_stage / financing_model / involvement_type / name_en_quality 的 中文键 ↔ 英文 ↔ code ↔ 颜色/优先级/标记）收敛到根目录 `enums_mapping.json` 单一可信源，让 s01b / s02a / s03 / s04 全部从它读取，删除所有硬编码副本。

---

## 一、设计决策（执行前确认，可在 JSON 里随手改）

1. **英文标签取"完整准确"版**（flat_builder 的长形式），而非 s04 现在的缩写。例：风电 `Wind Power`（不再 `Wind`）、水电 `Hydropower`、新能源制造业 `New Energy Manufacturing`。理由：单一可信源只能留一份，长形式更准确；前端显示会随之变长一点，不满意改 JSON 一列即可。
2. **category 的 code 取 s02a 现有 `CATEGORY_CODE`**：PV/WD/HY/NC/GT/BM/ES/HB/TH/MF/TX（与现有 canonical_id 前缀一致，**不可改**，否则破坏主键）。
3. **颜色取 s04 `types/index.ts` 现值**（前端已在用，保持视觉不变）。
4. **flags**：
   - category `no_capacity`（无装机容量概念）= 储能 / 输变电建设 / 新能源制造业。
   - category `hidden`（前端默认隐藏）= 核电 / 新能源制造业 / 输变电建设。
   - category `excluded`（基线策略剔除）= 火电。
   - stage `hidden`（前端基线剔除）= 拟开发。
5. **死代码**：`DataContext.tsx` 用英文串匹配隐藏类别（`thermal/oil/gas` 比中文 + `categoryEn.includes("nuclear"...)`）全部删除，改用 enums 的 `isHidden(zh)/isExcluded(zh)` 按中文键判断。
6. **范围**：S1 只补**规范集**进 JSON。DB 里的 39/47 个脏值不在 JSON、不在 S1 处理；S2 用映射表把它们归一/排除。

---

## 二、目标文件结构

- **改写** `enums_mapping.json`（根目录，单一可信源）→ 见 §三 完整内容。
- **新增** `bri_enums.py`（根目录）：Python 加载器 + 助手函数。一份，被 s01b/s02a/s03 共用（DRY）。
- **新增** `s04_web_visualization/scripts/copy-enums.mjs`：构建前把根 `enums_mapping.json` 拷进 `src/generated/enums.json`。
- **新增** `s04_web_visualization/src/utils/enums.ts`：前端加载器（import 上面拷贝的 json）+ 助手。
- **新增** `tests/test_enums_consistency.py`（根目录）：TDD 一致性测试（断言各 Python 模块枚举集合 == JSON）。
- **改写**（删硬编码、改读 enums）：
  - `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`（prompt 内 L505-514 四个枚举列表）
  - `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py`（`CATEGORY_CODE` L33-45）
  - `s03_data_exporter/src/s03_data_exporter/flat_builder.py`（`NO_CAPACITY_CATS` L36、`STAGE_PRIORITY` L39-48、`CATEGORY_EN` L52-64、`STAGE_EN` L66-88、`FINANCING_MODEL_EN` L90-97、`INVOLVEMENT_TYPE_EN` L99-108）
  - `s04_web_visualization/src/types/index.ts`（`CATEGORIES` L101-113、`STAGES` L115-124、`STAGE_COLORS` L126-135）
  - `s04_web_visualization/src/contexts/DataContext.tsx`（`EXCLUDED_CATEGORIES` L22、`HIDDEN_CATEGORY_KEYS` L23、`EXCLUDED_STAGES` L24，及 L40-47 英文串匹配）
  - `s04_web_visualization/src/components/filters/FilterBar.tsx`（`HIDDEN_CATEGORY_KEYS` L14）
  - `s04_web_visualization/src/utils/capacityPolicy.ts`（`NO_CAPACITY_CATEGORIES` L9）
- **暂不动**：`scripts/clean_historical_data.py` 的 VALID_*/MAP 表（它服务 S2 的清洗，S2 会让它也读 enums）。`display.ts` 的国家映射（属国家表，非本次枚举范围，check-record §3.6#5 留待后续）。

---

## 三、`enums_mapping.json` 目标完整内容

```json
{
  "_comment": "枚举字段单一可信源。DB 只存中文键/code，渲染时按此表转换。Python 经 bri_enums.py、前端经 src/utils/enums.ts 读取。新增枚举值必须先加这里。",

  "category": {
    "光伏":         { "zh": "光伏",         "en": "Solar PV",                     "code": "PV", "color": "#F59E0B", "no_capacity": false, "hidden": false, "excluded": false },
    "风电":         { "zh": "风电",         "en": "Wind Power",                   "code": "WD", "color": "#3B82F6", "no_capacity": false, "hidden": false, "excluded": false },
    "水电":         { "zh": "水电",         "en": "Hydropower",                   "code": "HY", "color": "#06B6D4", "no_capacity": false, "hidden": false, "excluded": false },
    "地热及余热":   { "zh": "地热及余热",   "en": "Geothermal & Waste Heat",      "code": "GT", "color": "#10B981", "no_capacity": false, "hidden": false, "excluded": false },
    "生物质":       { "zh": "生物质",       "en": "Biomass",                      "code": "BM", "color": "#84CC16", "no_capacity": false, "hidden": false, "excluded": false },
    "储能":         { "zh": "储能",         "en": "Energy Storage",               "code": "ES", "color": "#64748B", "no_capacity": true,  "hidden": false, "excluded": false },
    "综合能源":     { "zh": "综合能源",     "en": "Integrated Energy",            "code": "HB", "color": "#EC4899", "no_capacity": false, "hidden": false, "excluded": false },
    "核电":         { "zh": "核电",         "en": "Nuclear Power",                "code": "NC", "color": "#8B5CF6", "no_capacity": false, "hidden": true,  "excluded": false },
    "火电":         { "zh": "火电",         "en": "Thermal Power",                "code": "TH", "color": "#78716C", "no_capacity": false, "hidden": false, "excluded": true  },
    "新能源制造业": { "zh": "新能源制造业", "en": "New Energy Manufacturing",     "code": "MF", "color": "#A78BFA", "no_capacity": true,  "hidden": true,  "excluded": false },
    "输变电建设":   { "zh": "输变电建设",   "en": "Transmission & Distribution",  "code": "TX", "color": "#F97316", "no_capacity": true,  "hidden": true,  "excluded": false },
    "Unknown":      { "zh": "未知",         "en": "Unknown",                      "code": "UNK","color": "#9CA3AF", "no_capacity": false, "hidden": false, "excluded": false }
  },

  "project_stage": {
    "拟开发":   { "zh": "拟开发",   "en": "Planned",              "priority": 0, "color": "#94a3b8", "hidden": true  },
    "收购":     { "zh": "收购",     "en": "Acquired",             "priority": 1, "color": "#6B7280", "hidden": false },
    "中标":     { "zh": "中标",     "en": "Awarded",              "priority": 2, "color": "#EC4899", "hidden": false },
    "签约":     { "zh": "签约",     "en": "Signed",               "priority": 3, "color": "#9333EA", "hidden": false },
    "开工":     { "zh": "开工",     "en": "Construction Started", "priority": 4, "color": "#F59E0B", "hidden": false },
    "在建":     { "zh": "在建",     "en": "Under Construction",   "priority": 5, "color": "#D97706", "hidden": false },
    "竣工":     { "zh": "竣工",     "en": "Completed",            "priority": 6, "color": "#2563EB", "hidden": false },
    "投入运营": { "zh": "投入运营", "en": "Operational",          "priority": 7, "color": "#16A34A", "hidden": false },
    "Unknown":  { "zh": "未知",     "en": "Unknown",              "priority": -1,"color": "#9CA3AF", "hidden": false }
  },

  "financing_model": {
    "信贷融资": { "zh": "信贷融资", "en": "Credit Financing" },
    "债券融资": { "zh": "债券融资", "en": "Bond Financing" },
    "股权融资": { "zh": "股权融资", "en": "Equity Financing" },
    "混合融资": { "zh": "混合融资", "en": "Blended Financing" },
    "其他":     { "zh": "其他",     "en": "Other" },
    "Unknown":  { "zh": "未知",     "en": "Unknown" }
  },

  "involvement_type": {
    "BOT/BOOT":       { "zh": "BOT/BOOT",       "en": "BOT/BOOT" },
    "EPC总承包":      { "zh": "EPC总承包",      "en": "EPC General Contractor" },
    "设计/施工服务方": { "zh": "设计/施工服务方", "en": "Design/Construction Service" },
    "股权投资方":     { "zh": "股权投资方",     "en": "Equity Investor" },
    "设备供应商":     { "zh": "设备供应商",     "en": "Equipment Supplier" },
    "投融资+EPC":     { "zh": "投融资+EPC",     "en": "Investment + EPC" },
    "融资方":         { "zh": "融资方",         "en": "Financier" },
    "Unknown":        { "zh": "未知",           "en": "Unknown" }
  },

  "name_en_quality": {
    "ai_translated":   { "zh": "AI翻译",   "en": "AI Translated",     "display_tag": "[译]" },
    "source_verified": { "zh": "信源确认", "en": "Source Verified",   "display_tag": "" },
    "manual_verified": { "zh": "人工确认", "en": "Manually Verified", "display_tag": "" },
    "official":        { "zh": "官方名称", "en": "Official Name",     "display_tag": "" }
  }
}
```

> 注意：火电/核电 留在规范集（DB 里有 473/29 个，且 canonical_id 已用 TH/NC 前缀），用 `hidden/excluded` 标记控制前端可见性，行为与现状一致。非 RE 脏类别（石油/油气/矿产…）**不进** JSON，S2 清洗时排除。

---

## 四、TDD 步骤

### Task 1：写一致性测试（先红）

**Files:** Create `tests/test_enums_consistency.py`

- [ ] **Step 1**：写测试——断言 `bri_enums` 暴露的规范集与各模块"曾经硬编码"的集合一致，并断言 JSON 自洽（code 唯一、必填字段齐全）。

```python
"""S1 枚举单一可信源一致性测试：保证 bri_enums 与 enums_mapping.json 自洽。"""
import json, pathlib
import bri_enums as e

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = json.loads((ROOT / "enums_mapping.json").read_text(encoding="utf-8"))

def test_category_codes_unique_and_present():
    codes = [v["code"] for v in RAW["category"].values()]
    assert len(codes) == len(set(codes)), "category code 必须唯一"
    for zh, v in RAW["category"].items():
        for f in ("zh", "en", "code", "color", "no_capacity", "hidden", "excluded"):
            assert f in v, f"category {zh} 缺字段 {f}"

def test_stage_priorities_unique():
    prios = [v["priority"] for k, v in RAW["project_stage"].items() if k != "Unknown"]
    assert len(prios) == len(set(prios)), "stage priority 必须唯一"

def test_loader_matches_json():
    assert e.category_code("光伏") == "PV"
    assert e.category_en("风电") == "Wind Power"
    assert e.stage_priority("竣工") == 6
    assert e.is_no_capacity("储能") is True
    assert e.is_hidden_category("核电") is True
    assert e.is_excluded_category("火电") is True
    assert set(e.valid_categories()) == set(RAW["category"].keys())
    assert set(e.valid_stages()) == set(RAW["project_stage"].keys())
```

- [ ] **Step 2**：运行确认失败（`bri_enums` 尚不存在）。
  Run: `uv --project s03_data_exporter run python -m pytest tests/test_enums_consistency.py -v`
  Expected: FAIL（ModuleNotFoundError: bri_enums）

### Task 2：实现 Python 加载器 `bri_enums.py`（转绿）

**Files:** Create `bri_enums.py`（repo 根）

- [ ] **Step 3**：实现加载器。

```python
"""枚举单一可信源的 Python 加载器：读根目录 enums_mapping.json，供 s01b/s02a/s03 共用。"""
import json
import pathlib
from functools import lru_cache

@lru_cache(maxsize=1)
def _data() -> dict:
    # 从本文件所在目录读取，避免 parents[N] 脆弱路径
    path = pathlib.Path(__file__).resolve().parent / "enums_mapping.json"
    return json.loads(path.read_text(encoding="utf-8"))

# ── 集合 ──
def valid_categories() -> list[str]: return list(_data()["category"].keys())
def valid_stages() -> list[str]: return list(_data()["project_stage"].keys())
def valid_financing() -> list[str]: return list(_data()["financing_model"].keys())
def valid_involvement() -> list[str]: return list(_data()["involvement_type"].keys())

# ── category 助手 ──
def category_code(zh: str) -> str | None: return _data()["category"].get(zh, {}).get("code")
def category_en(zh: str) -> str | None: return _data()["category"].get(zh, {}).get("en")
def is_no_capacity(zh: str) -> bool: return _data()["category"].get(zh, {}).get("no_capacity", False)
def is_hidden_category(zh: str) -> bool: return _data()["category"].get(zh, {}).get("hidden", False)
def is_excluded_category(zh: str) -> bool: return _data()["category"].get(zh, {}).get("excluded", False)

# ── stage 助手 ──
def stage_priority(zh: str) -> int: return _data()["project_stage"].get(zh, {}).get("priority", -1)
def stage_en(zh: str) -> str | None: return _data()["project_stage"].get(zh, {}).get("en")

# ── 通用 en ──
def financing_en(zh: str) -> str | None: return _data()["financing_model"].get(zh, {}).get("en")
def involvement_en(zh: str) -> str | None: return _data()["involvement_type"].get(zh, {}).get("en")

def prompt_value_list(enum: str) -> str:
    """给 s01b prompt 用：返回某枚举所有中文键的逗号串。"""
    return ", ".join(_data()[enum].keys())
```

- [ ] **Step 4**：把目标 JSON（§三）写入 `enums_mapping.json`（覆盖旧 7 类版本）。
- [ ] **Step 5**：运行测试转绿。
  Run: `uv --project s03_data_exporter run python -m pytest tests/test_enums_consistency.py -v`
  Expected: PASS
- [ ] **Step 6**：提交。`git add bri_enums.py enums_mapping.json tests/test_enums_consistency.py && git commit -m "feat(enums): 建立枚举单一可信源 + Python 加载器"`

### Task 3：s03 flat_builder 改读 enums

**Files:** Modify `s03_data_exporter/src/s03_data_exporter/flat_builder.py`

- [ ] **Step 7**：文件顶部加导入 shim（健壮上溯找根，避免 parents[N]）：

```python
import sys, pathlib
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums as enums
```

- [ ] **Step 8**：删除 `NO_CAPACITY_CATS`(L36)、`STAGE_PRIORITY`(L39-48)、`CATEGORY_EN`(L52-64)、`STAGE_EN`(L66-88)、`FINANCING_MODEL_EN`(L90-97)、`INVOLVEMENT_TYPE_EN`(L99-108)；把引用处替换为 `enums.is_no_capacity(c)` / `enums.stage_priority(s)` / `enums.category_en(c)` / `enums.stage_en(s)` / `enums.financing_en(f)` / `enums.involvement_en(i)`。`_stage_rank` 直接用 `enums.stage_priority`。
- [ ] **Step 9**：跑 s03 导出确认无报错、输出 JSON 与改前关键字段一致。
  Run: `uv run run_export`（在 s03 目录或按 Makefile）
  Expected: 成功；projects_flat 的 category_en/stage_en 有值（长形式）。
- [ ] **Step 10**：提交。

### Task 4：s02a CATEGORY_CODE 改读 enums

**Files:** Modify `s02a_duplicate_merger/src/s02a_duplicate_merger/tools/duplicate_merger_tool.py`

- [ ] **Step 11**：加同样的导入 shim；删 `CATEGORY_CODE`(L33-45)，引用处改 `enums.category_code(cat)`（注意保留 fallback：未知类别返回什么 code？现状未知则 KeyError；改为 `enums.category_code(cat) or "UNK"` 并 logging.warning，符合 CLAUDE.md 规则1）。
- [ ] **Step 12**：冒烟测试（不跑全量去重，import 该模块 + 单测 `_gen_canonical_id` 路径不报错）。提交。

### Task 5：s01b prompt 改读 enums

**Files:** Modify `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`

- [ ] **Step 13**：加导入 shim；把 prompt L505-514 四个硬编码枚举列表改为 f-string 注入 `enums.prompt_value_list("category")` 等。注意 category 给 LLM 的列表应排除 `Unknown`？——保留与现状一致（现状含 Unknown 仅 stage/involvement/financing，category 不含）。按现状：category 列表去掉 Unknown，其余照旧。
- [ ] **Step 14**：import 该模块确认 prompt 能正常构建（写一个最小单测调用 `_build_prompt` 检查四类枚举词都出现在 prompt 文本里）。提交。

### Task 6：前端 enums 加载器 + 构建拷贝

**Files:** Create `s04_web_visualization/scripts/copy-enums.mjs`, `s04_web_visualization/src/utils/enums.ts`; Modify `s04_web_visualization/package.json`

- [ ] **Step 15**：`copy-enums.mjs`：

```javascript
// 构建前把根 enums_mapping.json 拷进 src/generated/enums.json，保证前端单一可信源。
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../../enums_mapping.json");
const destDir = resolve(here, "../src/generated");
mkdirSync(destDir, { recursive: true });
copyFileSync(src, resolve(destDir, "enums.json"));
console.log("[copy-enums] enums.json 已同步");
```

- [ ] **Step 16**：`package.json` scripts 加 `"copy:enums": "node scripts/copy-enums.mjs"`，并加 `"predev": "npm run copy:enums"`、`"prebuild": "npm run copy:enums"`。把 `src/generated/` 加入 `.gitignore`（生成物）。
- [ ] **Step 17**：`src/utils/enums.ts`：

```typescript
// 前端枚举单一可信源加载器：读构建期拷贝的 enums.json。
import raw from "@/generated/enums.json";

type CategoryMeta = { zh: string; en: string; code: string; color: string; no_capacity: boolean; hidden: boolean; excluded: boolean };
type StageMeta = { zh: string; en: string; priority: number; color: string; hidden: boolean };
type SimpleMeta = { zh: string; en: string };

export const CATEGORIES = raw.category as Record<string, CategoryMeta>;
export const STAGES = raw.project_stage as Record<string, StageMeta>;
export const FINANCING_MODELS = raw.financing_model as Record<string, SimpleMeta>;
export const INVOLVEMENT_TYPES = raw.involvement_type as Record<string, SimpleMeta>;

export const isHiddenCategory = (zh: string) => CATEGORIES[zh]?.hidden ?? false;
export const isExcludedCategory = (zh: string) => CATEGORIES[zh]?.excluded ?? false;
export const isNoCapacityCategory = (zh: string) => CATEGORIES[zh]?.no_capacity ?? false;
export const isHiddenStage = (zh: string) => STAGES[zh]?.hidden ?? false;
export const stageColor = (zh: string) => STAGES[zh]?.color ?? "#9CA3AF";
```

- [ ] **Step 18**：运行 `npm run copy:enums` 生成 enums.json，`npx tsc --noEmit` 确认类型通过。提交。

### Task 7：前端各处改读 enums.ts，删硬编码

**Files:** Modify `types/index.ts`, `DataContext.tsx`, `FilterBar.tsx`, `capacityPolicy.ts`

- [ ] **Step 19**：`types/index.ts` 删 `CATEGORIES`/`STAGES`/`STAGE_COLORS`(L101-135)，从 `@/utils/enums` re-export（或全局改引用源）。修所有 import 这些常量的文件指向 enums.ts。
- [ ] **Step 20**：`capacityPolicy.ts` 删 `NO_CAPACITY_CATEGORIES`(L9)，`hasCapacity` 改 `!isNoCapacityCategory(cat)`（来自 enums.ts）。
- [ ] **Step 21**：`FilterBar.tsx` 删 `HIDDEN_CATEGORY_KEYS`(L14)，改 `isHiddenCategory`。
- [ ] **Step 22**：`DataContext.tsx`：删 `EXCLUDED_CATEGORIES`(L22)/`HIDDEN_CATEGORY_KEYS`(L23)/`EXCLUDED_STAGES`(L24)；`shouldExcludeByPolicy`（L34-51）改为：`isExcludedCategory(p.category) || isHiddenCategory(p.category)` + `isHiddenStage(p.latest_stage)`，**删除 L40-47 的英文串匹配死代码**。其余（ISO/年份/primary_entity/容量）不变。
- [ ] **Step 23**：`npm run build` 通过；浏览器实测——类别/阶段下拉、地图着色、筛选行为与改前一致（火电/核电/制造业/输变电仍隐藏，拟开发仍剔除）。提交。

### Task 8：收尾

- [ ] **Step 24**：删临时文件 `docs/rules_and_plans/sessions/_db_enum_values.txt`（已并入 v2_plan §一）。
- [ ] **Step 25**：更新 `docs/status/`（overview + s03/s04 相关）与 `docs/changes/` 本周日志，记录 S1 完成。
- [ ] **Step 26**：最终提交。

---

## 五、验证清单（DoD）

- [ ] `pytest tests/test_enums_consistency.py` 全绿。
- [ ] `npm run build` exit 0；`npx tsc --noEmit` 无错。
- [ ] 全仓 grep 确认枚举硬编码已清除：`CATEGORY_EN`/`STAGE_PRIORITY`/`STAGE_EN`/`CATEGORY_CODE`/`NO_CAPACITY_CATS`/`HIDDEN_CATEGORY_KEYS`/`EXCLUDED_CATEGORIES` 不再有定义（只在 enums 加载器/JSON）。
- [ ] s03 导出成功，projects_flat 关键字段与改前对照无回归。
- [ ] 前端浏览器实测：隐藏/排除/无容量行为与改前一致。
- [ ] docs/status 与 docs/changes 已更新。

## 六、风险与回滚

- 纯代码，DB 不动；如出错 `git reset` 即可。
- 唯一行为变化：前端英文标签变长形式（设计决策1），如不接受改 JSON 一列。
- s03 `category_en` 输出从短→长形式，若前端别处依赖短形式字符串匹配需一并改（已在 Task 7 处理 DataContext 的英文匹配）。
