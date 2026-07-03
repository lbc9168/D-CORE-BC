# s01b 非能源误分类治本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 s01b 写库前加确定性非能源兜底 + 收紧 S1/S2 prompt + 容量吨/年守卫 + projects.db 存量回溯清扫，根除"矿业/油气/化工等非能源项目被误分类为综合能源上页面"的系统性问题（基准案 CAN-HB-0001 钾肥矿 → 3.8TW）。

**Architecture:** 四层：① 新增纯 Python 模块 `guardrails/nonenergy_check.py`（不依赖 LLM 的关键词+语义保护判定），在 `project_extractor_tool.py` 写库前对 valid 与 chinese_unnamed 两条分支强制 exclude（顺带修复 unnamed 分支丢失 `in_scope=false` 的存量缺口）；② 容量单位守卫：容量字段以"吨"计则 `capacity_mw` 置 NULL；③ S1/S2 prompt 收紧并编码用户判定规则（绿氢须"明确 RE 种类+装机"）；④ 新增 `sweep_nonenergy` CLI 对 projects.db 存量行回溯清扫（dry-run 默认，--apply 前备份）。s02a 已验证会把 `excluded` 从 extracted_projects 传播到 canonical（组级 any-excluded），故清扫可防未来重建复活。

**Tech Stack:** Python 3.10-3.12, pytest（`uv run pytest`）, sqlite3, 无新依赖。

## Global Constraints

- **工作目录**：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix`（分支 `feat/s01b-nonenergy-filter`，已 ff 到 main `936c4be`）。所有 cd/测试/提交都在此 worktree。
- **测试命令**：`cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix\s01b_project_extractor && uv run pytest tests/<file> -v`（pyproject 已配 `pythonpath = ["src", "tests", ".."]`，无需手动设 PYTHONPATH）。
- **CLAUDE.md 编码规则**：禁 `except: pass`（须指定类型 + `logging.error`；唯一例外是已知无害边界且加注释）；运行时输出用 `logging`；批量写库用事务 + `executemany`（>50 条）；`print()` 仅限脚本顶层人机提示。
- **Windows 编码**：不要向 console print 可能含非 GBK 字符的内容（项目名等）——用 logging 或写文件。
- **提交风格**：中文一句话，`feat(s01b): …` / `fix(s01b): …` / `test(s01b): …`，遵循 `git log` 现有风格。每个 Task 至少一个 commit。
- **精准修改**：只动本计划列出的文件与区段；prompt 文件不做无关措辞"顺手优化"。
- **真实数据 GATED**：Task 7 对真实 `projects.db` 只跑 dry-run 产出 CSV；`--apply` 必须等用户复看后再执行。

**背景材料**（实现时可参考，不是依赖）：
- 审计明细：`E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-02-misclassified-nonenergy-audit.csv`（git-ignored，仅主工作区有；84 行：confirmed_nonenergy 71 / legit_energy 7 / borderline 6）。
- 变更日志背景：`docs/changes/2026-W27b.md` 追加节（2026-07-02 审计 + 治标）。
- 用户判定规则（2026-07-02）：绿氢/矿山类项目须**同时**有"明确可再生能源种类 + 明确装机容量"才纳入，且按能源种类分类（光伏制氢→光伏，不进综合能源）；只提"绿氢/电解"而无能源种类 → exclude。

---

### Task 1: 确定性非能源判定模块 `nonenergy_check.py`

**Files:**
- Create: `s01b_project_extractor/src/s01b_project_extractor/guardrails/nonenergy_check.py`
- Test: `s01b_project_extractor/tests/test_nonenergy_check.py`

**Interfaces:**
- Produces（后续 Task 2/3/6 依赖，签名必须一字不差）:
  - `check_nonenergy(name, summary, category, capacity_mw, capacity_details) -> str | None` — 返回排除理由字符串（命中）或 None（放行）。
  - `capacity_is_tonnage(capacity_raw: str | None, capacity_details: str | None) -> bool` — 容量字段是否以吨计且无 MW 语义。
  - `DETERMINISTIC_GUARD_SOURCE = "deterministic_guard"` — exclude_source 常量。

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_nonenergy_check.py`：

```python
"""确定性非能源兜底：check_nonenergy / capacity_is_tonnage 纯函数判定。

夹具取自 2026-07-02 审计 CSV（71 confirmed / 7 legit / 6 borderline）代表样本 + 常规能源项目负例。
"""
import pytest
from s01b_project_extractor.guardrails.nonenergy_check import (
    DETERMINISTIC_GUARD_SOURCE,
    capacity_is_tonnage,
    check_nonenergy,
)

# ── 必须拦住：审计 confirmed_nonenergy 代表样本（各行业至少一条）──
CONFIRMED_NONENERGY = [
    # (name, summary, category, capacity_mw, capacity_details)
    ("加拿大盐湖钾肥矿业并购项目", "并购世界级钾盐矿，产能380万吨/年", "综合能源", "3800000", "380万吨/年"),  # 基准案 CAN-HB-0001
    ("阿联酋乌姆盖万150MIGD海水淡化项目", "", "综合能源", "", ""),
    ("阿根廷青山实业控股Jujuy氯碱化工厂项目", "", "综合能源", "", ""),
    ("玻利维亚阿玛雅金矿采矿项目", "", "综合能源", "", ""),
    ("巴林7个天然气增压站工程项目", "", "综合能源", "", ""),
    ("伊拉克SCP水泥生产线项目", "", "综合能源", "", ""),
    ("塞尔维亚普拉霍沃港口项目", "", "综合能源", "", ""),
    ("阿尔及利亚Gara Djebilet铁矿开发及一体化钢铁厂项目", "", "综合能源", "", ""),
    ("埃及苏伊士炼油及石化厂项目", "", "综合能源", "", ""),
    ("马里Bougouni锂矿项目", "", "综合能源", "", ""),
    ("印尼莫罗瓦利青山工业园区焦炉气产LNG联产甲醇项目", "", "综合能源", "", ""),
    ("南非姆普马兰加省Hiryo煤制肥综合体", "", "综合能源", "", ""),
    ("中哈（连云港）物流合作基地及相关通道项目", "", "综合能源", "", ""),
    ("Muga Potash Greenfield Project (Spain)", "", "综合能源", "", ""),
    ("印尼纬达贝工业园区重卡充换电站项目", "", "综合能源", "", ""),  # 「电站」字样不得保护充换电站
    ("莫桑比克鲁伍马盆地1号区块天然气项目", "", "综合能源", "", ""),
    ("吉布提阿萨尔盐湖溴化工项目", "", "综合能源", "", ""),
]

# ── 不许误杀：审计 legit_energy 全部 7 条 ──
LEGIT_ENERGY = [
    ("刚果（金）三大矿区3个光储柴微网项目", "", "综合能源", "30", "光储柴微网"),
    ("塞尔维亚2GW风光发电及绿氢项目", "", "综合能源", "2000", "风电+光伏 2GW"),
    ("塞尔维亚1.5吉瓦风光制氢项目", "", "综合能源", "1500", "风光 1.5GW"),
    ("中老115千伏联网电力贸易项目", "", "输变电建设", "", ""),
    ("赞比亚乔瓦矿山光储微网项目", "", "综合能源", "30", "PV 20MW + BESS 10MWh"),
    ("塞尔维亚博尔地区2GW可再生能源及绿氢项目", "", "综合能源", "2000", "2GW"),
    ("秘鲁马尔科纳区2MW低压微电网项目", "", "综合能源", "2", "2MW"),
]

# ── 不许误杀：常规能源项目负例 ──
NORMAL_ENERGY = [
    ("巴基斯坦光伏电站项目", "中国电建签署100MW光伏EPC合同", "光伏", "100", ""),
    ("印度尼西亚卡扬河9000MW水电项目", "", "水电", "9000", ""),
    ("沙特阿拉伯Rumah 2联合循环1800MW发电站项目", "", "火电", "1800", ""),
    ("孟加拉帕亚拉1320MW燃煤电站项目", "", "火电", "1320", ""),
    ("埃及2.5吉瓦太阳能电池供应项目", "整笔供货", "新能源制造业", "", "2.5GW/年产能"),
    ("印尼10万吨/年多晶硅厂项目", "", "新能源制造业", "", "10万吨/年"),  # 制造业吨/年豁免
]

# ── 绿氢用户规则（2026-07-02）：须同时有明确 RE 种类 + 装机 ──
HYDROGEN_EXCLUDE = [
    ("西班牙韦尔瓦160MW绿氢项目", "", "综合能源", "160", ""),        # 有容量无种类 → exclude
    ("肯尼亚绿色氨项目", "", "综合能源", "", ""),                    # 都无 → exclude
    ("阿联酋绿色氢炼钢项目", "", "综合能源", "", ""),                # 绿色氢+炼钢 → exclude
    ("西班牙远景电解槽工厂项目", "只提绿氢电解，无能源种类", "综合能源", "", ""),
]


@pytest.mark.parametrize("name,summary,cat,mw,details", CONFIRMED_NONENERGY)
def test_confirmed_nonenergy_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"应拦住: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", LEGIT_ENERGY + NORMAL_ENERGY)
def test_legit_energy_not_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is None, f"误杀: {name}"


@pytest.mark.parametrize("name,summary,cat,mw,details", HYDROGEN_EXCLUDE)
def test_hydrogen_without_re_type_excluded(name, summary, cat, mw, details):
    assert check_nonenergy(name, summary, cat, mw, details) is not None, f"绿氢规则应拦住: {name}"


def test_empty_inputs_pass():
    assert check_nonenergy("", "", "", "", "") is None
    assert check_nonenergy(None, None, None, None, None) is None


def test_capacity_is_tonnage():
    assert capacity_is_tonnage("3800000", "380万吨/年") is True
    assert capacity_is_tonnage("", "60万吨LNG") is True
    assert capacity_is_tonnage("100", "PV: 100MW, BESS: 50MWh") is False
    assert capacity_is_tonnage("30", "日处理1万吨垃圾, 装机30MW") is False  # 有 MW 语义不置空
    assert capacity_is_tonnage("", "") is False
    assert capacity_is_tonnage(None, None) is False


def test_guard_source_constant():
    assert DETERMINISTIC_GUARD_SOURCE == "deterministic_guard"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix\s01b_project_extractor && uv run pytest tests/test_nonenergy_check.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 's01b_project_extractor.guardrails.nonenergy_check'`

- [ ] **Step 3: 实现模块**

创建 `s01b_project_extractor/src/s01b_project_extractor/guardrails/nonenergy_check.py`：

```python
"""Layer 0: 确定性非能源兜底——纯 Python 判定非能源重工业项目与吨/年容量，不依赖 LLM。

背景：2026-07-02 审计发现 77 条矿业/油气/化工等项目被 LLM 误分类为综合能源上页面
（基准案 CAN-HB-0001 钾肥矿 → capacity_mw=3,800,000 即 3.8TW）。排除规则在 prompt
里本已齐全但 gemini-2.5-flash-lite 不遵守，故写库前加本确定性兜底。
审计明细：docs/rules_and_plans/2026-07-02-misclassified-nonenergy-audit.csv。
"""
import re

# exclude_source 标记：与 S2 in_scope 判定（"extraction"）区分，便于日后审计
DETERMINISTIC_GUARD_SOURCE = "deterministic_guard"

# 非能源产业词（命中「项目名称」为强信号；源自审计 71 例归纳）
_NONENERGY_WORDS = [
    # 矿业采选/冶金
    "矿业", "采矿", "选矿", "矿山", "矿区", "铁矿", "铜矿", "金矿", "镍矿", "锂矿",
    "铝土矿", "磷矿", "石墨矿", "煤矿", "多金属矿", "采选", "矿服", "尾矿",
    "钾肥", "钾盐", "锂盐", "氧化铝", "电解铝", "镍铁", "冶炼", "冶金", "钻孔", "爆破",
    # 油气炼化
    "油气", "气田", "油田", "炼油", "炼化", "石化", "LNG", "液化天然气",
    "天然气处理", "天然气增压", "输气管道", "天然气管道", "天然气站", "凝析油",
    "焦炉气", "煤气柜", "FPU",
    # 化工化肥
    "化工", "化肥", "氯碱", "甲醇", "MTBE", "硫磺", "溴化", "煤制肥",
    # 建材/钢铁
    "水泥", "熟料", "钢铁", "钢厂", "高炉", "炼钢",
    # 港口物流（「电力贸易」由发电语义词保护，故不含泛词「贸易」）
    "港口", "码头", "物流", "清关", "货运站", "边关站",
    # 水务
    "海水淡化", "供水", "污水",
]

# 非能源英文词（部分项目名为纯英文，如 "Muga Potash Greenfield Project"）；小写匹配
_NONENERGY_WORDS_EN = [
    "potash", "gold mine", "copper", "iron ore", "refinery", "petrochemical",
    "cement", "alumina", "smelter", "gas field", "chlor-alkali",
]

# 硬排除词：含「电站」字样但属用电设施，先于发电语义保护判定
_NONENERGY_OVERRIDE_WORDS = ["充换电站", "充电站"]

# 发电语义词（负信号：项目名或容量明细命中则不排除——保护矿区光储微网等合法项目）
_POWER_WORDS = [
    "发电", "电站", "电厂", "光伏", "风电", "水电", "储能", "光储", "微网", "微电网",
    "太阳能", "风能", "风光", "光热", "地热", "生物质", "并网", "输变电", "变电站",
    "输电", "联网", "电力贸易", "装机", "mw", "gw", "兆瓦",
]

# 明确可再生能源种类词（绿氢规则用；用户判定规则 2026-07-02）
_RE_TYPE_WORDS = ["光伏", "风电", "水电", "太阳能", "风能", "风光", "光热", "地热", "生物质", "可再生能源"]

# 绿氢/电解类词：须同时有「明确 RE 种类 + 明确装机容量」才纳入
_HYDROGEN_WORDS = ["绿氢", "绿色氢", "绿氨", "绿色氨", "制氢", "电解槽", "电解水"]

# 吨/年产能模式 与 MW 装机语义
_TONNAGE_RE = re.compile(r"[\d,.]+\s*万?\s*吨|吨/年|万吨|t/a|tpa|mtpa", re.IGNORECASE)
_MW_TOKEN_RE = re.compile(r"MW|GW|兆瓦|千瓦|kW", re.IGNORECASE)


def _has_positive_mw(capacity_mw, name: str, details: str) -> bool:
    """是否有装机容量证据：capacity_mw 为正数，或名称/明细含 MW/GW 语义。"""
    try:
        if float(capacity_mw) > 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(_MW_TOKEN_RE.search(name + " " + details)) or "吉瓦" in (name + details)


def capacity_is_tonnage(capacity_raw, capacity_details) -> bool:
    """容量字段本身是否以吨计（且无任何 MW 语义）——是则该数值不是装机，应置空。"""
    text = f"{capacity_raw or ''} {capacity_details or ''}"
    if not text.strip():
        return False
    return bool(_TONNAGE_RE.search(text)) and not _MW_TOKEN_RE.search(text)


def check_nonenergy(name, summary, category, capacity_mw, capacity_details):
    """确定性非能源判定：返回排除理由字符串（命中）或 None（放行）。

    规则按序：⓪ 硬排除词（充换电站）；① 绿氢/电解类须同时有明确 RE 种类+装机容量；
    ② 名称命中非能源词且名称+容量明细无发电语义；③ 发电类别但容量只有吨/年（制造业豁免）。
    """
    name = str(name or "")
    summary = str(summary or "")
    details = str(capacity_details or "")
    category = str(category or "")
    text = name + " " + summary
    protect_text = (name + " " + details).lower()

    # 规则 0：硬排除词（「充换电站」含「电站」字样，须先于发电语义保护判定）
    for w in _NONENERGY_OVERRIDE_WORDS:
        if w in name:
            return f"非发电设施（{w}）"

    # 规则 1：绿氢/电解类——须同时有明确可再生能源种类 + 装机容量（用户规则 2026-07-02）
    if any(w in text for w in _HYDROGEN_WORDS):
        if any(w in text for w in _RE_TYPE_WORDS) and _has_positive_mw(capacity_mw, name, details):
            return None  # 合法（如风光制氢）；类别归属由 S2 prompt 规则处理
        return "绿氢/电解类项目缺明确可再生能源种类或装机容量（用户规则2026-07-02）"

    # 规则 2：名称命中非能源产业词，且名称+容量明细无发电语义 → 排除
    hit = next((w for w in _NONENERGY_WORDS if w in name), None) or next(
        (w for w in _NONENERGY_WORDS_EN if w in name.lower()), None
    )
    if hit and not any(w in protect_text for w in _POWER_WORDS):
        return f"非能源产业项目（名称命中「{hit}」且无发电语义）"

    # 规则 3：发电类别但容量字段只有吨/年（新能源制造业以产能计，豁免）
    if category != "新能源制造业" and capacity_is_tonnage(None, details):
        if not any(w in name.lower() for w in _POWER_WORDS):
            return "容量为吨/年产能而非装机，疑似非发电项目"

    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_nonenergy_check.py -v`
Expected: 全部 PASS。若个别夹具不过，调整关键词表（不改测试预期——夹具就是验收标准）。

- [ ] **Step 5: 对完整审计 CSV 做覆盖率验证（一次性，不入库）**

在 worktree 根运行（脚本不提交）：

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix\s01b_project_extractor
uv run python -X utf8 -c "
import csv, sys
sys.path.insert(0, 'src'); sys.path.insert(0, '..')
sys.stdout.reconfigure(encoding='utf-8')
from s01b_project_extractor.guardrails.nonenergy_check import check_nonenergy
rows = list(csv.DictReader(open(r'E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-02-misclassified-nonenergy-audit.csv', encoding='utf-8-sig')))
for label in ('confirmed_nonenergy', 'legit_energy', 'borderline'):
    grp = [r for r in rows if r['verdict_label'] == label]
    caught = [r for r in grp if check_nonenergy(r['project_name'], r['reasoning'], r['category'], '', '')]
    print(label, f'{len(caught)}/{len(grp)}')
    if label == 'legit_energy':
        for r in caught: print('  误杀!', r['project_name'])
"
```

Expected 验收线: `confirmed_nonenergy ≥ 55/71`，`legit_energy 0/7 误杀`（borderline 尽力即可）。若 legit 有误杀 → 必须修词表；若 confirmed 低于 55 → 补词表后重跑 Step 4+5。把最终三行数字记下来（Task 7 写变更日志用）。

- [ ] **Step 6: Commit**

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix
git add s01b_project_extractor/src/s01b_project_extractor/guardrails/nonenergy_check.py s01b_project_extractor/tests/test_nonenergy_check.py
git commit -m "feat(s01b): 新增确定性非能源兜底判定模块 nonenergy_check（治本 Layer 0）"
```

---

### Task 2: 写库路径接线（valid + unnamed 两分支）+ 修 unnamed 丢 in_scope 缺口

**Files:**
- Modify: `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`（约 881-884 行 valid 分支 exclude 出口处；约 939-975 行 unnamed 分支 base dict；文件顶部 guardrails import 处约 24-25 行）
- Test: `s01b_project_extractor/tests/test_write_deterministic_guard.py`

**Interfaces:**
- Consumes: Task 1 的 `check_nonenergy`、`DETERMINISTIC_GUARD_SOURCE`。
- Produces: 写库行为——命中兜底的行 `excluded=1, exclude_source='deterministic_guard'`；unnamed 分支从此尊重 `in_scope=false`（`exclude_source='extraction'`）。

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_write_deterministic_guard.py`（仿照 `tests/test_write_exclude.py` 的 fake `extract_one` + tmp DB 模式）：

```python
"""s01b: 写库前确定性非能源兜底（Layer 0）+ unnamed 分支 in_scope 出口修复。"""
import sqlite3
import pandas as pd
import pytest
from s01b_project_extractor.tools import project_extractor_tool as pet


@pytest.fixture
def _patch_side_effects(monkeypatch):
    """屏蔽外部副作用：央企查表/翻译；只留写库逻辑。"""
    monkeypatch.setattr(pet, "_resolve_is_central_soe", lambda *a, **k: 0)
    monkeypatch.setattr(pet, "_resolve_entity_en", lambda *a, **k: "EN")


def _df_one_row():
    return pd.DataFrame([{
        "原文链接": "http://x1", "文章标题": "标题", "正文内容": "正文", "发布日期": "2026-01-01",
    }])


def _run(monkeypatch, tmp_path, status, proj):
    def fake_extract_one(title, content, date, *, client, model="x"):
        return {"analysis": {"project_subject": "s", "reasoning": "r"},
                "status": status, "projects": [proj]}
    monkeypatch.setattr(pet, "extract_one", fake_extract_one)
    pdb = str(tmp_path / "projects.db")
    tool = pet.ProjectExtractorTool()
    tool._process_df(_df_one_row(), batch_id="b1", ts="20260703_t", client=None,
                     country_db={}, projects_db_path=pdb,
                     review_db_path=str(tmp_path / "review.db"))
    conn = sqlite3.connect(pdb)
    row = conn.execute(
        "SELECT excluded, exclude_source, exclude_reason, capacity_mw FROM extracted_projects"
    ).fetchone()
    conn.close()
    return row


def test_valid_nonenergy_forced_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """S2 说 in_scope=true 但名称是钾肥矿 → 兜底强制 excluded=1, source=deterministic_guard。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "并购世界级钾盐矿", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "加拿大盐湖钾肥矿业并购项目", "Category": "综合能源",
                        "Capacity (MW)": "3800000", "Capacity Details": "380万吨/年",
                        "Country": "加拿大", "Project Stage": "签约"},
        "chinese_entities": [{"Implementing Chinese Entity": "某矿业公司",
                              "Type of involvement of Chinese Entities": "投资"}],
    })
    assert row[0] == 1, "兜底应强制 excluded=1"
    assert row[1] == "deterministic_guard"
    assert row[2]


def test_valid_legit_energy_not_touched(tmp_path, monkeypatch, _patch_side_effects):
    """矿山光储微网（audit legit）→ 兜底放行 excluded=0。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "赞比亚乔瓦矿山光储微网项目", "Category": "综合能源",
                        "Capacity (MW)": "30", "Capacity Details": "PV 20MW + BESS 10MWh",
                        "Country": "赞比亚", "Project Stage": "在建"},
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
    })
    assert row[0] == 0


def test_unnamed_in_scope_false_now_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """修复缺口：chinese_unnamed 分支此前丢失 in_scope=false → 现在应写 excluded=1/extraction。"""
    row = _run(monkeypatch, tmp_path, "chinese_unnamed", {
        "project_summary": "", "in_scope": False, "out_of_scope_reason": "油气管道，非发电",
        "common_info": {"Project Name": "某油气管道项目", "Category": "综合能源",
                        "Capacity (MW)": "", "Capacity Details": "",
                        "Country": "肯尼亚", "Project Stage": "在建"},
        "chinese_entities": [],
    })
    assert row[0] == 1, "unnamed 分支应尊重 in_scope=false"
    assert row[1] == "extraction"
    assert "油气" in (row[2] or "")


def test_unnamed_nonenergy_forced_excluded(tmp_path, monkeypatch, _patch_side_effects):
    """chinese_unnamed + in_scope=true 但名称非能源 → 兜底同样生效。"""
    row = _run(monkeypatch, tmp_path, "chinese_unnamed", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "伊拉克迈赫穆尔水泥厂项目", "Category": "综合能源",
                        "Capacity (MW)": "", "Capacity Details": "",
                        "Country": "伊拉克", "Project Stage": "在建"},
        "chinese_entities": [],
    })
    assert row[0] == 1
    assert row[1] == "deterministic_guard"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_write_deterministic_guard.py -v`
Expected: 4 个测试 FAIL（excluded=0 / source 为 None）。

- [ ] **Step 3: 实现接线**

`project_extractor_tool.py` 三处修改：

(a) 文件顶部 guardrails import 区（现约 24-25 行）追加：

```python
from s01b_project_extractor.guardrails.nonenergy_check import (
    check_nonenergy, capacity_is_tonnage, DETERMINISTIC_GUARD_SOURCE,
)
```

(b) valid 分支：找到注释 `# ── exclude 出口（来自 S2 的 in_scope 判定）──`（约 881 行），在 `base = {...}` 字典闭合之后、`if not entities:` 之前插入：

```python
                    # ── Layer 0 确定性非能源兜底：LLM 判定不作数，命中即强制排除（2026-07-02 审计治本）──
                    if not base["_excluded"]:
                        _guard_reason = check_nonenergy(
                            base["Project Name"], project_summary, base["Category"],
                            base["Capacity (MW)"], base["Capacity Details"],
                        )
                        if _guard_reason:
                            base["_excluded"] = 1
                            base["_exclude_reason"] = _guard_reason
                            base["_exclude_source"] = DETERMINISTIC_GUARD_SOURCE
```

(c) unnamed 分支：在 base dict（约 939-975 行）的 `"_is_unnamed_placeholder": True,` 一行后补上此前缺失的 exclude 出口三键：

```python
                        # ── exclude 出口（来自 S2 的 in_scope 判定；此前 unnamed 分支缺失，导致 in_scope=false 被静默丢弃）──
                        "_excluded": 0 if proj.get("in_scope", True) else 1,
                        "_exclude_reason": "" if proj.get("in_scope", True) else str(proj.get("out_of_scope_reason", "")),
                        "_exclude_source": "" if proj.get("in_scope", True) else "extraction",
```

并在该 base dict 闭合后、`row_data = base.copy()` 之前插入与 (b) 完全相同的 Layer 0 兜底块（缩进按该分支上下文）。

- [ ] **Step 4: 跑测试确认通过（含既有 exclude 测试不回归）**

Run: `uv run pytest tests/test_write_deterministic_guard.py tests/test_write_exclude.py tests/test_write_unnamed.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py s01b_project_extractor/tests/test_write_deterministic_guard.py
git commit -m "feat(s01b): 写库前接入确定性非能源兜底；修 unnamed 分支丢 in_scope=false 缺口"
```

---

### Task 3: 容量单位守卫（吨/年 → capacity_mw 置 NULL）

**Files:**
- Modify: `s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py`（DB 写入循环 `cap_raw` 处，约 1075 行与 INSERT 参数约 1107 行）
- Test: `s01b_project_extractor/tests/test_write_deterministic_guard.py`（追加 2 个测试）

**Interfaces:**
- Consumes: Task 1 的 `capacity_is_tonnage`（Task 2 已 import）。
- Produces: 写库行为——容量字段以吨计的行 `capacity_mw=NULL`（`capacity_mw_raw` 保留原文供审计）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_write_deterministic_guard.py` 末尾追加：

```python
def test_tonnage_capacity_blanked(tmp_path, monkeypatch, _patch_side_effects):
    """容量明细为吨/年 → capacity_mw 置 NULL（raw 保留），不再产出 3.8TW 怪值。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "并购钾盐矿", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "加拿大盐湖钾肥矿业并购项目", "Category": "综合能源",
                        "Capacity (MW)": "3800000", "Capacity Details": "380万吨/年",
                        "Country": "加拿大", "Project Stage": "签约"},
        "chinese_entities": [{"Implementing Chinese Entity": "某矿业公司",
                              "Type of involvement of Chinese Entities": "投资"}],
    })
    assert row[3] is None, "吨/年容量应置 NULL"


def test_mw_capacity_kept(tmp_path, monkeypatch, _patch_side_effects):
    """正常 MW 容量不受守卫影响。"""
    row = _run(monkeypatch, tmp_path, "valid_project", {
        "project_summary": "", "in_scope": True, "out_of_scope_reason": "",
        "common_info": {"Project Name": "肯尼亚50MW光伏项目", "Category": "光伏",
                        "Capacity (MW)": "50", "Capacity Details": "",
                        "Country": "肯尼亚", "Project Stage": "在建"},
        "chinese_entities": [{"Implementing Chinese Entity": "中国电建",
                              "Type of involvement of Chinese Entities": "EPC总承包"}],
    })
    assert row[3] == 50.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_write_deterministic_guard.py -v -k tonnage`
Expected: `test_tonnage_capacity_blanked` FAIL（现值为 3800.0——被既有 ÷1000 启发式"修"成了看似合理的数）。

- [ ] **Step 3: 实现守卫**

DB 写入循环内（约 1074-1077 行），把：

```python
                cap_raw = row_data.get("Capacity (MW)", "")
```

改为：

```python
                cap_raw = row_data.get("Capacity (MW)", "")
                # ── 容量单位守卫：容量字段以吨计（产能非装机）→ 数值列置 NULL，raw 保留供审计。
                # 注意不能交给 _normalize_capacity_mw 的 ÷1000 启发式——那会把吨位"修"成貌似合理的 MW。
                if capacity_is_tonnage(str(cap_raw), str(row_data.get("Capacity Details", ""))):
                    cap_mw_val = None
                else:
                    cap_mw_val = _normalize_capacity_mw(bri_parse.parse_number(cap_raw))
```

并把 INSERT 参数中的 `_normalize_capacity_mw(bri_parse.parse_number(cap_raw)),`（约 1107 行）替换为 `cap_mw_val,`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_write_deterministic_guard.py tests/test_capacity_normalize.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add s01b_project_extractor/src/s01b_project_extractor/tools/project_extractor_tool.py s01b_project_extractor/tests/test_write_deterministic_guard.py
git commit -m "feat(s01b): 容量单位守卫——吨/年产能不再倾入 capacity_mw（治本 Layer 3）"
```

---

### Task 4: S1 prompt 收紧

**Files:**
- Modify: `s01b_project_extractor/src/s01b_project_extractor/prompts/s1_relevance.py`
- Test: `s01b_project_extractor/tests/test_prompt_nonenergy_rules.py`（新建）

**Interfaces:**
- Consumes: 无。
- Produces: `build_s1_prompt` 文本含三条新规则（测试以子串锚定防回归）。

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_prompt_nonenergy_rules.py`：

```python
"""S1/S2 prompt 非能源收紧规则的存在性锚定（防止后续改 prompt 时误删）。"""
from s01b_project_extractor.prompts.s1_relevance import build_s1_prompt
from s01b_project_extractor.prompts.s2_basic import build_s2_prompt


def test_s1_manufacturing_scope_limited_to_ne_equipment():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "仅限**新能源装备/材料**产能" in p
    assert "钾肥、锂盐、氧化铝、甲醇、水泥、钢铁" in p


def test_s1_mineral_products_excluded():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "矿产/化肥/化工/冶金/建材产品的开采、生产与加工本身" in p


def test_s1_hydrogen_rule():
    p = build_s1_prompt("t", "c", "2026-01-01")
    assert "只提「绿氢/电解」而无明确能源种类" in p
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_prompt_nonenergy_rules.py -v`
Expected: 3 个 s1 测试 FAIL（子串不存在）。

- [ ] **Step 3: 修改 S1 prompt**

`prompts/s1_relevance.py` 三处：

(a) ② 制造业条目（现第 18 行）整行替换为：

```
- ② **新能源制造业**：以产能计的**新能源装备工厂**——光伏组件厂、电池厂、光伏板/多晶硅厂、新能源装备产业园等（判别线索：产出以「GW/年」「万吨/年」计；这类**不发电**但属录入范围，**不要**当成普通工业园区排除掉）。⚠️ 仅限**新能源装备/材料**产能；矿产/化肥/化工品/冶金/建材产品（钾肥、锂盐、氧化铝、甲醇、水泥、钢铁等）的「万吨/年」产能**不属于**新能源制造业，也不在录入范围。
```

(b) 排除情形 2.（现第 22 行）末尾例外句之前追加一句，整行替换为：

```
  2. 油气/矿业的非发电环节：油气田开采、炼油炼化、油气管道、LNG 接收/储运站、采矿、冶炼等本身不发电的环节，以及**矿产/化肥/化工/冶金/建材产品的开采、生产与加工本身**（钾肥/钾盐矿、锂矿、铜金矿、氧化铝、甲醇、水泥、钢铁等，即使以「万吨/年」计产能）。例外：燃气电厂、煤电厂、核电厂、垃圾/生物质发电厂等**发电项目**进第二步。
```

(c) 排除情形列表末尾（现第 26 行 `6. 纯领导访问...` 之后）新增：

```
  7. **绿氢/绿氨/电解水制氢项目**：仅当正文明确给出为其供电的**可再生能源发电种类**（光伏/风电/水电等）时才进第二步；只提「绿氢/电解」而无明确能源种类 → irrelevant。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_prompt_nonenergy_rules.py tests/test_stage_s1.py -v`
Expected: 全部 PASS（test_stage_s1 既有断言不回归）。

- [ ] **Step 5: Commit**

```bash
git add s01b_project_extractor/src/s01b_project_extractor/prompts/s1_relevance.py s01b_project_extractor/tests/test_prompt_nonenergy_rules.py
git commit -m "feat(s01b): S1 prompt 收紧——制造业限新能源装备、矿产化工产品排除、绿氢须明确能源种类"
```

---

### Task 5: S2 prompt 收紧 + Unknown 出口

**Files:**
- Modify: `s01b_project_extractor/src/s01b_project_extractor/prompts/s2_basic.py`
- Test: `s01b_project_extractor/tests/test_prompt_nonenergy_rules.py`（追加）

**Interfaces:**
- Consumes: 无。
- Produces: `build_s2_prompt` 文本含收紧规则；越界条目 Category 引导填 "Unknown"（`bri_enums.valid_categories()` 已含 Unknown，`validate_s2` 本就接受，无枚举面改动）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_prompt_nonenergy_rules.py` 追加：

```python
def test_s2_hybrid_requires_generation_subitems():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "每个子项都必须是发电/储能类别且以 MW/MWh 计" in p


def test_s2_hydrogen_classified_by_source():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "如光伏制氢→归「光伏」" in p
    assert "不要**仅因带「绿氢」就归「综合能源」" in p


def test_s2_out_of_scope_category_unknown():
    p = build_s2_prompt("t", "c", "2026-01-01")
    assert "in_scope=false 时 Category 一律填 \"Unknown\"" in p


def test_validate_s2_accepts_unknown_category():
    from s01b_project_extractor.stages.s2_basic import validate_s2
    errs = validate_s2([{"common_info": {"Category": "Unknown", "Project Stage": "在建",
                                         "Project Name": "某油气项目"}}])
    assert errs == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_prompt_nonenergy_rules.py -v -k s2`
Expected: 3 个 prompt 测试 FAIL；`test_validate_s2_accepts_unknown_category` PASS（既有行为确认）。

- [ ] **Step 3: 修改 S2 prompt**

`prompts/s2_basic.py` 三处：

(a) 「混合能源」小节的 ⚠️ 行（现第 45 行）整行替换为：

```
  - ⚠️ **综合能源仅指多种「发电类别」的组合**（如光伏+风电+储能、光热+光伏），且**每个子项都必须是发电/储能类别且以 MW/MWh 计**；氢、氨、化工品、矿产等**非电力产出不计入容量、也不构成综合能源**。油气/化工/民用/水务/灌溉/数据中心等**不是综合能源**，也**不应成为项目**——若本文实为这类（S1 漏放进来），按下方 in_scope=false 处理，不要硬塞「综合能源」或其它发电类别。
```

(b) 「关键规则」列表（A.3 容量反推之前）新增一条：

```
- **绿氢/绿氨/制氢项目分类（用户规则）**：按其**发电来源**分类——单一可再生来源（如光伏制氢→归「光伏」）；多种发电来源（风光制氢）→「综合能源」；正文无明确可再生能源种类或无装机容量 → `in_scope: false`（理由写「绿氢无明确能源种类/装机」）。**不要**仅因带「绿氢」就归「综合能源」。
```

(c) 「录入范围出口」小节（现第 51-54 行）在 `in_scope: false` 条目后追加一行：

```
- in_scope=false 时 Category 一律填 "Unknown"（合法枚举值），**不要**硬挑发电类别。
```

同时把输出 JSON 模板中 Category 字段描述（现第 69 行）改为：

```
        "Category": "只能选 [{cat_list}]；若 in_scope=false 则填 Unknown",
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_prompt_nonenergy_rules.py tests/test_stage_s2.py tests/test_orchestrator_s2.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add s01b_project_extractor/src/s01b_project_extractor/prompts/s2_basic.py s01b_project_extractor/tests/test_prompt_nonenergy_rules.py
git commit -m "feat(s01b): S2 prompt 收紧——综合能源须纯发电子项、绿氢按来源分类、越界条目 Category=Unknown"
```

---

### Task 6: projects.db 回溯清扫 CLI `sweep_nonenergy`

**Files:**
- Create: `s01b_project_extractor/src/s01b_project_extractor/tools/nonenergy_sweep.py`
- Modify: `s01b_project_extractor/pyproject.toml`（`[project.scripts]` 加一行）
- Test: `s01b_project_extractor/tests/test_nonenergy_sweep.py`

**Interfaces:**
- Consumes: Task 1 的 `check_nonenergy`、`DETERMINISTIC_GUARD_SOURCE`。
- Produces: `sweep(db_path: str, apply: bool = False, out_csv: str | None = None) -> list[dict]`（返回命中行 `{"id", "project_name", "category", "reason"}`）；CLI 入口 `sweep_nonenergy`。

- [ ] **Step 1: 写失败测试**

创建 `s01b_project_extractor/tests/test_nonenergy_sweep.py`：

```python
"""projects.db 回溯清扫：dry-run 不写库；--apply 先备份再事务批量置 excluded=1。"""
import sqlite3
from pathlib import Path
from s01b_project_extractor.tools.nonenergy_sweep import sweep


def _make_db(tmp_path) -> str:
    db = str(tmp_path / "projects.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE extracted_projects (
        id INTEGER PRIMARY KEY, project_name TEXT, project_summary TEXT,
        category TEXT, capacity_mw REAL, capacity_details TEXT,
        excluded INTEGER DEFAULT 0, exclude_reason TEXT, exclude_source TEXT)""")
    conn.executemany(
        "INSERT INTO extracted_projects (project_name, project_summary, category, capacity_mw, capacity_details, excluded) VALUES (?,?,?,?,?,?)",
        [
            ("加拿大盐湖钾肥矿业并购项目", "钾盐矿380万吨/年", "综合能源", 3800000, "380万吨/年", 0),
            ("赞比亚乔瓦矿山光储微网项目", "", "综合能源", 30, "PV 20MW + BESS 10MWh", 0),
            ("伊拉克SCP水泥生产线项目", "", "综合能源", None, "", 1),  # 已排除的跳过
        ])
    conn.commit(); conn.close()
    return db


def test_dry_run_reports_without_writing(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep(db, apply=False)
    assert [h["project_name"] for h in hits] == ["加拿大盐湖钾肥矿业并购项目"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT excluded FROM extracted_projects WHERE id=1").fetchone()[0] == 0
    conn.close()


def test_apply_writes_with_backup(tmp_path):
    db = _make_db(tmp_path)
    hits = sweep(db, apply=True)
    assert len(hits) == 1
    conn = sqlite3.connect(db)
    excl, src = conn.execute(
        "SELECT excluded, exclude_source FROM extracted_projects WHERE id=1").fetchone()
    legit = conn.execute("SELECT excluded FROM extracted_projects WHERE id=2").fetchone()[0]
    conn.close()
    assert (excl, src) == (1, "deterministic_guard")
    assert legit == 0, "legit 行不许被动"
    assert list(Path(db).parent.glob("projects.db.bak.*")), "apply 前应有备份"


def test_out_csv_written(tmp_path):
    db = _make_db(tmp_path)
    out = str(tmp_path / "report.csv")
    sweep(db, apply=False, out_csv=out)
    text = Path(out).read_text(encoding="utf-8-sig")
    assert "钾肥" in text and "reason" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_nonenergy_sweep.py -v`
Expected: FAIL — `ModuleNotFoundError: nonenergy_sweep`。

- [ ] **Step 3: 实现清扫工具**

创建 `s01b_project_extractor/src/s01b_project_extractor/tools/nonenergy_sweep.py`：

```python
"""对 projects.db 存量行回溯执行确定性非能源兜底：默认 dry-run 报告，--apply 先备份再事务落库。

用途：治标只改了主库（bri_re_database.db），projects.db 里误分类行仍 excluded=0，
未来任何 s02a 重建会把它们带回 canonical（s02a 按组传播 excluded）。本工具让 projects.db
与主库口径一致。用法：uv run sweep_nonenergy [--db PATH] [--apply] [--out report.csv]
"""
import argparse
import csv
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from s01b_project_extractor.guardrails.nonenergy_check import (
    DETERMINISTIC_GUARD_SOURCE,
    check_nonenergy,
)

logger = logging.getLogger(__name__)

_DEFAULT_DB = str(Path(__file__).resolve().parents[3] / "data" / "projects.db")


def sweep(db_path: str, apply: bool = False, out_csv: str | None = None) -> list[dict]:
    """扫描 extracted_projects 未排除行，返回命中列表；apply=True 时先备份再事务批量置 excluded=1。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, project_name, project_summary, category, capacity_mw, capacity_details "
        "FROM extracted_projects WHERE COALESCE(excluded, 0) = 0"
    ).fetchall()
    hits: list[dict] = []
    for r in rows:
        reason = check_nonenergy(
            r["project_name"], r["project_summary"], r["category"],
            r["capacity_mw"], r["capacity_details"],
        )
        if reason:
            hits.append({"id": r["id"], "project_name": r["project_name"],
                         "category": r["category"], "reason": reason})
    logger.info("扫描 %d 行未排除记录，命中 %d 行", len(rows), len(hits))

    if out_csv:
        with open(out_csv, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["id", "project_name", "category", "reason"])
            w.writeheader()
            w.writerows(hits)
        logger.info("dry-run 报告已写 %s", out_csv)

    if apply and hits:
        bak = db_path + datetime.now().strftime(".bak.%Y%m%d_%H%M%S")
        shutil.copy2(db_path, bak)
        logger.info("已备份 %s", bak)
        conn.executemany(
            "UPDATE extracted_projects SET excluded=1, exclude_reason=?, exclude_source=? WHERE id=?",
            [(h["reason"], DETERMINISTIC_GUARD_SOURCE, h["id"]) for h in hits],
        )
        conn.commit()
        logger.info("已落库 excluded=1：%d 行（source=%s）", len(hits), DETERMINISTIC_GUARD_SOURCE)
    conn.close()
    return hits


def main() -> None:
    """CLI 入口：解析参数并执行清扫（默认 dry-run；日志同时写文件）。"""
    parser = argparse.ArgumentParser(description="projects.db 非能源回溯清扫")
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--apply", action="store_true", help="落库（默认 dry-run）")
    parser.add_argument("--out", default=None, help="命中清单 CSV 输出路径")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_csv = args.out or str(Path(args.db).parent / f"nonenergy_sweep_{ts}.csv")
    log_path = str(Path(out_csv).with_suffix(".log"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )
    hits = sweep(args.db, apply=args.apply, out_csv=out_csv)
    print(f">>> 命中 {len(hits)} 行（{'已落库' if args.apply else 'dry-run，未写库'}），清单: {out_csv}")


if __name__ == "__main__":
    main()
```

`pyproject.toml` `[project.scripts]` 追加：

```toml
sweep_nonenergy        = "s01b_project_extractor.tools.nonenergy_sweep:main"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_nonenergy_sweep.py -v`
Expected: 3 个 PASS。

- [ ] **Step 5: Commit**

```bash
git add s01b_project_extractor/src/s01b_project_extractor/tools/nonenergy_sweep.py s01b_project_extractor/tests/test_nonenergy_sweep.py s01b_project_extractor/pyproject.toml
git commit -m "feat(s01b): sweep_nonenergy CLI——projects.db 存量非能源行回溯清扫（dry-run/--apply）"
```

---

### Task 7: 全量回归 + 真实库 dry-run + 文档收尾

**Files:**
- Create: `docs/changes/2026-W27e.md`
- Modify: `docs/status/s01b.md`
- 产出（不提交，git-ignored 数据）：`E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-03-nonenergy-retro-sweep-dryrun.csv`

- [ ] **Step 1: 全量测试回归**

Run: `cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix\s01b_project_extractor && uv run pytest`
Expected: 全部 PASS（既有 29 个测试文件 + 本次新增 3 个）。任何失败必须修复后重跑，不得跳过。

- [ ] **Step 2: 真实 projects.db dry-run（只读，GATED）**

真实库在主工作区（worktree 无数据文件）。只跑 dry-run，**绝不加 --apply**：

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix\s01b_project_extractor
uv run sweep_nonenergy --db E:\Projects\BRI_RE_Database_v2\s01b_project_extractor\data\projects.db --out E:\Projects\BRI_RE_Database_v2\docs\rules_and_plans\2026-07-03-nonenergy-retro-sweep-dryrun.csv
```

Expected: 正常退出，报告命中 N 行。合理性核对：N 应与治标的 77 条 canonical 同数量级（projects.db 一个 canonical 对应多行，N 可能更大）；抽看 CSV 前 20 行不应出现明显合法能源项目。若出现误杀 → 回 Task 1 修词表（改词表 → 重跑 Task 1 全部 Step → 重跑本步）。

- [ ] **Step 3: 写变更日志**

创建 `docs/changes/2026-W27e.md`（追加式，含：日期 2026-07-03 · 模块 s01b · 做了什么（四层防御逐条）· 为什么（2026-07-02 审计治本，引 W27b 追加节）· 影响（新文件/改动文件/新 CLI/新测试；audit CSV 覆盖率数字（Task 1 Step 5 记录的三行）；unnamed in_scope 缺口修复；真实库 dry-run 命中数与 CSV 路径；**--apply GATED 待用户复看**）。

- [ ] **Step 4: 更新模块状态文件**

`docs/status/s01b.md` 按覆盖式规则更新「当前功能」与「如何运行」小节：新增 Layer 0 确定性非能源兜底（写库前强制 exclude，source=deterministic_guard）、容量吨/年守卫（capacity_mw 置 NULL）、S1/S2 prompt 含绿氢用户规则与 Unknown 出口、`uv run sweep_nonenergy` 命令。不写变更历史（那在 changes）。

- [ ] **Step 5: Commit**

```bash
cd E:\Projects\BRI_RE_Database_v2\.claude\worktrees\s01b-nonenergy-fix
git add docs/changes/2026-W27e.md docs/status/s01b.md
git commit -m "docs(changes+status): s01b 非能源治本四层防御落地记录 + 状态同步"
```

- [ ] **Step 6: 收尾汇报（给用户）**

汇总：各 Task commit 列表、审计 CSV 覆盖率（confirmed x/71，legit 0/7）、真实库 dry-run 命中数与 CSV 路径。明确两个 GATED 事项等用户决定：① `sweep_nonenergy --apply` 落库；② 分支合并回 main（用 superpowers:finishing-a-development-branch 流程）。

---

## Self-Review 记录

- **Spec 覆盖**：三层防御 → Task 1-5（Layer 0 = Task 1+2，容量守卫 = Task 3，prompt = Task 4+5）；回溯清扫 → Task 6+7；用户绿氢规则 → Task 1 规则 1 + Task 4(c) + Task 5(b)；unnamed 丢 in_scope 缺口（本次评估新发现）→ Task 2(c)；7 条 legit 反例回归 → Task 1 夹具 + Task 2 test_valid_legit_energy_not_touched。
- **已知有意的不覆盖**（写入变更日志即可，不是缺陷）：混合型 confirmed 案（名称同时含发电词，如"协鑫LNG…天然气发电"、"氧化铝厂+配套发电厂"、"PPC水泥线暨8MW光伏"）确定性层不拦——由 prompt 层与后续人工审计兜底；验收线因此定 ≥55/71 而非 71/71，legit 误杀必须为 0。
- **类型一致性**：`check_nonenergy(name, summary, category, capacity_mw, capacity_details) -> str | None` 与 `capacity_is_tonnage(capacity_raw, capacity_details) -> bool` 在 Task 1 定义、Task 2/3/6 消费，签名一致；`DETERMINISTIC_GUARD_SOURCE` 常量三处引用一致。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码；所有命令含预期输出。
