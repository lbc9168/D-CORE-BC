# external-tools 状态

> 最后更新：2026-04-14

---

## 概述

`external-tools/` 目录存放不属于主流水线的一次性辅助工具。这些工具独立运行，不依赖 crewai 框架，可直接用 `uv run --with <deps>` 执行。

---

## data-combination — 外部 Excel 数据补充工具

### 功能

将两个手动收集的 Excel 数据源中的网址和财务信息，补充到 `s01b_project_extractor/data/projects.db` 的 `extracted_projects` 表中。

**核心约束**：不修改已有数据、不新增项目行，只填充 NULL / 空 / "Unknown" 字段。

### 文件

| 文件 | 说明 |
|------|------|
| `step1_match_simple.py` | 处理 File 1（精确匹配） |
| `step2_match_ai.py` | 处理 File 2（模糊 + Gemini AI 匹配） |
| `requirements.txt` | 依赖：pandas, openpyxl, rapidfuzz, google-genai, python-dotenv |
| `2025-BRI RE-10to12_Intern_checked.xlsx` | 数据源 1（459 行，微信公众号手工整理） |
| `202210-202509-full-old.xlsx` | 数据源 2（943 行，旧数据库） |
| `logs/step1_log.csv` | 运行时生成：Step 1 匹配审计日志 |
| `logs/step2_log.csv` | 运行时生成：Step 2 匹配审计日志 |

### 字段映射

**File 1 → projects.db**（匹配键：`Source` ↔ `source_link` 精确匹配）

| Excel 列 | DB 列 |
|---|---|
| Official Website link | official_website |
| Non-official Website Link | non_official_website |
| Total Project Investment (Million USD) | total_investment_usd |
| Subcontract Amount (Million USD) | subcontract_amount_usd |
| Investment (or Financial Close) Date | investment_date |
| Financing Model | financing_model |
| Additional Information | additional_remarks |

**File 2 → projects.db**（匹配方式：rapidfuzz 模糊评分 + Gemini 确认）

| Excel 列（索引） | DB 列 |
|---|---|
| col 15 Remarks (Official Website Link) | official_website |
| col 16 Remarks (Non-Official Source Link) | non_official_website |
| col 17 Total Project Investment (Million USD) | total_investment_usd |
| col 18 Subcontract Amount (Million USD) | subcontract_amount_usd |
| col 19 Investment (or Financial Close) Date | investment_date |
| col 21 Remarks（备注） | additional_remarks |
| col 22 Financing Model | financing_model |

### 匹配策略（Step 2）

1. **预过滤**：按 `country_iso` 精确过滤 DB 候选集
2. **模糊评分**：project_name 40% + implementing_entity 30% + capacity 20% + location 10%
   - ≥ 90 分：自动匹配
   - 55–90 分：Gemini（gemini-2.5-flash-lite）二次确认
   - < 55 分：跳过
3. **填充规则**：DB 字段为 NULL / 空 / "Unknown" 时才填入

### 运行方式

```bash
cd external-tools/data-combination

# Step 1（精确匹配，无需 API Key）
uv run --with pandas --with openpyxl step1_match_simple.py
uv run --with pandas --with openpyxl step1_match_simple.py --dry-run

# Step 2（AI 辅助匹配，需要 GEMINI_API_KEY）
uv run --with pandas --with openpyxl --with rapidfuzz --with google-genai --with python-dotenv step2_match_ai.py
uv run ... step2_match_ai.py --dry-run
uv run ... step2_match_ai.py --skip-gemini   # 仅执行 ≥90 分的自动匹配
```

### 状态

| 项目 | 状态 |
|------|------|
| step1_match_simple.py | ✅ 已完成（待运行验证） |
| step2_match_ai.py | ✅ 已完成（待运行验证） |

---

## external-link-adder — 自动填充项目网站链接

### 功能

为 `projects_by_source.xlsx`（约 6389 条全球能源项目记录）的 `official_website` 和
`non_official_website` 两列自动搜索并填充链接。

**跳过条件**：任意一列已有值则跳过该行。

**搜索策略（两轮）**：
1. 第1轮：`{implementing_entity} {project_name}`（`implementing_entity` 为空时跳至第2轮）
2. 第2轮：`{project_name} {country}`（第1轮无结果时执行）

**链接判断**：Gemini 2.5 Flash Lite 分析搜索结果，区分 official / non-official 类型：
- official：实施企业官网或项目专属官网
- non-official：行业媒体、政府公告、国际机构报告等可信第三方

### 文件

| 文件 | 说明 |
|------|------|
| `fill_links.py` | 主脚本 |
| `requirements.txt` | 依赖：pandas, openpyxl, google-genai, python-dotenv, tqdm, requests |
| `.env` | 存放 `SERPER_API_KEY`（`GEMINI_API_KEY` 从根目录 .env 读取） |
| `projects_by_source.xlsx` | 输入数据（约 6389 行） |
| `projects_by_source_filled.xlsx` | 运行时生成：填充后的输出 |
| `search_log.txt` | 运行时生成：TSV 格式搜索日志 |

### 工程特性

| 特性 | 实现 |
|------|------|
| 断点续传 | 启动时读取已有输出文件合并进度；每50条自动保存 |
| 速率限制 | Serper 搜索后 sleep 0.5s；Gemini 调用后 sleep 0.3s |
| 错误处理 | 单条异常记录 status=error 并继续 |
| 进度显示 | tqdm 进度条 |
| 日志 | search_log.txt（TSV），字段：row_num / query1 / query2 / 链接 / reason / status |

### 运行方式

```bash
cd external-tools/external-link-adder

# 测试：仅处理前10条，不保存文件
uv run --with pandas --with openpyxl --with google-genai --with python-dotenv --with tqdm --with requests fill_links.py --limit 10 --dry-run

# 正式运行
uv run --with pandas --with openpyxl --with google-genai --with python-dotenv --with tqdm --with requests fill_links.py
```

### 状态

| 项目 | 状态 |
|------|------|
| fill_links.py | ✅ 已完成（待运行验证） |
