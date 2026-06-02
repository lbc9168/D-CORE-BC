# BRI_RE_Database_v2 项目概述

> 最后更新：2026-06-02

---

## v2 大版本（2026-05-23 起，worktree `worktree-v2`）

按 `docs/rules_and_plans/v2_plan.md` 分 S1–S5 逐 session 执行，主题：数据正确性 + 时间建模。

- **S1 枚举单一可信源 ✅（2026-05-23）**：新增根目录 `enums_mapping.json`（补全至 12 类完整规范集 + color/no_capacity/hidden/excluded 标记 + stage priority）+ `bri_enums.py`（Python 加载器）+ `s04 src/utils/enums.ts`（前端加载器，构建期拷贝）。删除 s01b prompt / s02a `CATEGORY_CODE` / s03 flat_builder 六处映射 / s04 `types`、`DataContext`、`FilterBar`、`capacityPolicy` 的全部硬编码枚举与死代码。详见各子项目 status 与 `docs/changes/2026-W21.md`。
- **S2a 数值化 schema（纯代码）✅（2026-05-24）** + **S2b 主库一次性迁移 ✅（2026-05-24）**：`*_raw(TEXT)`+REAL 双列、储能/容量来源字段、`excluded` 排除非 RE；主库经 `scripts/migrate_v2.py --apply` 迁移（备份 `.bak.20260524_012313`），34 非 RE 项目排除。详见 `docs/changes/2026-W21b.md`、`2026-W21c.md`。
- **S3 编号稳定 + 排序 bug + display_order ✅（2026-05-24）**：s02a 编号生成改数值排序（修 >9999/99 重复 bug，`canonical_id` 稳定不变）；s03 `projects_flat` 新增 `display_order`（按最早事件日期升序实时计算）；前端列表/表格默认老项目在前。详见 `docs/changes/2026-W21d.md`。
- S4–S5：待执行（事件级时间建模 / 去重分组与地点归一化）。
- **v2.1 P0 提取评测框架 ✅（2026-05-24）**：新增根 `eval/` 模块（metrics/predict/article_store/build_draft/gold_loader/run_eval + README），为 s01b 提取建立约 150 篇金标准 + 字段级准确率「尺子」，使每次 prompt/agent 改动可量化判断进步/退步；含端到端 mock 测试（无真实 Gemini）。**待人工步骤：金标准标注（~150 篇）→ `gold_set.jsonl`** 后才能跑真实评测。详见 [`eval.md`](eval.md) 与 `docs/changes/2026-W21h.md`。
- **v2.1 P2 复核机制 ✅ + P2.1 exclude 机制/范围对齐/critic 约束 ✅（代码+单测，2026-06-02）**：详见 [`review.md`](review.md)。⚠️ **机制已建好但尚未在真实数据上跑出效果**（主库仍是旧 prompt 数据）；真实数据落地 + P2/P2.1 未完事项 + P3/P4/P5 提醒见 [`backlog-v2.1.md`](backlog-v2.1.md)。
- **v2.1 P5 去 crewAI 薄壳清理 ✅（2026-06-02）**：把 crewAI 从仓库彻底移除（老版 `s01_data_collector_filter` 除外，归档候选）。s01a/s02a/s02c 三个「1 agent + 1 task」薄壳 → 工具去 `BaseTool` 改普通类、删 `crew.py`+config、`main.run()` 直调 `Tool()._run()`、pyproject 去 crewai；`pipeline/flow.py` 由 crewAI `Flow` 改纯 Python `run_pipeline(cfg)`（并修 P1 后已断的 s01b crew import）。纯机械、行为不变、零 LLM 花费。详见 `docs/changes/2026-W22h.md`。

---

## 项目概述

**"一带一路"可再生能源项目数据库** — 追踪中国企业参与的海外可再生能源项目。

**流水线架构：**

```
数据收集 → 去重合并 → 实体归一化 ──→ 数据库整理 → 网页可视化 → 数据分析 → 报告撰写
   ✅         ✅          ✅    ↓        ✅          🚧          ⬜        ⬜
                    交叉验证（可选）⚠️
```

> 阶段二已拆分为三个独立子项目：`s02a_duplicate_merger`（必选）+ `s02b_entity_normalizer`（建议）+ `s02c_cross_verification`（可选）。
> 去重合并后可直接进入数据库整理，也可先做实体归一化和/或交叉验证再整理。

---

## 2026-03-23 更新

- supporting_docs/company_name_cache.json 已从 supporting_docs/company_hierarchy.json 自动补充中英文公司名映射（新增 302 条，保留原有已验证条目不覆盖）。

---

## 各阶段状态速览

| 阶段     | 子项目                         | 状态                      | 详情                         |
| ------ | --------------------------- | ----------------------- | -------------------------- |
| 阶段一（旧） | `s01_data_collector_filter` | ✅ 已完成（2026-03-21 更新知识库） | [s01.md](s01.md)           |
| 阶段一A   | `s01a_news_crawler`         | ✅ 已完成                   | —                          |
| 阶段一B   | `s01b_project_extractor`    | ✅ 已完成（2026-03-21 更新知识库） | [s01b.md](s01b.md)         |
| 阶段二A   | `s02a_duplicate_merger`     | ✅ 已完成                   | [s02a.md](s02a.md)         |
| 阶段二B   | `s02b_entity_normalizer`    | ✅ 已完成                   | [s02b.md](s02b.md)         |
| 阶段二C   | `s02c_cross_verification`   | ⚠️ 待修改（可选）              | [s02c.md](s02c.md)         |
| 阶段三    | `s03_data_exporter`         | ✅ 已完成（细节待调整）            | [s03.md](s03.md)           |
| 阶段四    | `s04_web_visualization`     | 🚧 调试中                  | [s04.md](s04.md)           |
| 编排     | `pipeline`                  | ✅ 已完成                   | [pipeline.md](pipeline.md) |
| 阶段五    | 数据分析                        | ⬜ 未开始                   | —                          |
| 阶段六    | 报告撰写                        | ⬜ 未开始                   | —                          |

---

## 阶段五：数据分析 ⬜ 未开始

---

## 阶段六：报告撰写 ⬜ 未开始

---

## 项目结构

```
BRI_RE_Database_v2/
├── pipeline/                           # 全流水线纯 Python 顺序编排
│   ├── pyproject.toml
│   └── src/bri_pipeline/
│       └── flow.py                 # run_pipeline(cfg) + PipelineConfig（v2.1 P5 去 crewAI Flow）
│
├── s01_data_collector_filter/          # 阶段一（旧版合并子项目，仍可独立使用）
│   ├── pyproject.toml
│   ├── knowledge/user_preference.txt
│   ├── WorldCountryList.json
│   └── src/s01_data_collector_filter/
│       ├── main.py                 # run() + run_parallel() + run_bulk() + split_date_range()
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       ├── tools/news_crawler_tool.py
│       ├── tools/project_extractor_tool.py
│       └── tools/custom_tool.py
│
├── s01a_news_crawler/                  # 阶段一A：新闻爬虫（拆分版，v2.1 P5 去 crewAI）
│   ├── pyproject.toml
│   ├── WorldCountryList.json
│   └── src/s01a_news_crawler/
│       ├── main.py                 # run() 直调 tool，无 crewAI
│       └── tools/news_crawler_tool.py   # NewsCrawlerTool 纯 Python 类
│
├── s01b_project_extractor/             # 阶段一B：AI 提取（拆分版，含三层 Guardrail；P1 已去 crewAI）
│   ├── pyproject.toml
│   ├── WorldCountryList.json
│   └── src/s01b_project_extractor/
│       ├── main.py                 # run_bulk_extract（分阶段 orchestrator）
│       ├── stages/                 # S1-S5 分阶段提取
│       ├── prompts/                # 各阶段 prompt
│       ├── llm/                    # 统一 LLM client + StageRouter
│       ├── knowledge/user_preference.txt
│       ├── tools/project_extractor_tool.py
│       └── guardrails/             # format_checks / semantic_checks / batch_checks
│
├── s02a_duplicate_merger/               # 阶段二A 子项目（去重合并与入库，v2.1 P5 去 crewAI）
│   ├── pyproject.toml
│   ├── data/db/                    # SQLite 数据库（bri_re_database.db）
│   └── src/s02a_duplicate_merger/
│       ├── main.py                 # run() 直调 tool，无 crewAI
│       └── tools/duplicate_merger_tool.py   # DuplicateMergerTool 纯 Python 类
│
├── s02b_entity_normalizer/              # 阶段二B 独立子项目（实体名称归一化）
│   ├── pyproject.toml
│   └── src/s02b_entity_normalizer/
│       └── entity_normalizer.py    # 三信号候选筛查 + LLM 归一化
│
├── s02c_cross_verification/             # 阶段二C 子项目（外部交叉验证，可选，v2.1 P5 去 crewAI）
│   ├── pyproject.toml
│   ├── data/verification_cache/    # GDELT 文章 & 向量嵌入缓存
│   ├── data/manual_review/         # 低置信度人工核查 Excel
│   └── src/s02c_cross_verification/
│       ├── main.py                 # run() 直调 tool，无 crewAI
│       └── tools/cross_verification_tool.py   # CrossVerificationTool 纯 Python 类
│
├── s03_data_exporter/                  # 阶段三：数据库整理与导出
│   ├── pyproject.toml
│   └── src/s03_data_exporter/
│       ├── main.py
│       ├── province_extractor.py
│       ├── admin1_lookup.py
│       ├── flat_builder.py
│       ├── stats_builder.py
│       └── json_exporter.py
│
├── s04_web_visualization/              # 阶段四：React 前端
│   ├── package.json
│   ├── vite.config.ts
│   ├── public/
│   │   ├── data/                   # 静态 JSON（s03 自动同步）
│   │   └── geo/provinces/          # 省级 GeoJSON（download_geo.py 生成）
│   ├── scripts/
│   │   └── download_geo.py
│   └── src/
│       ├── components/
│       └── utils/isoMapping.ts
│
├── supporting_docs/                    # 项目辅助数据（AI 提取知识库）
│   ├── company_hierarchy.json      # 母子公司层级对照表（17集团，三级，含 is_central_soe）
│   ├── company_name_cache.json     # 中文公司名 → 英文缓存（自动增长，verified 字段区分人工/AI）
│   ├── soe.md                      # 官方100家央企名录（兜底判断）
│   └── Archived/                   # 已归档的旧格式 Markdown 文件
│
├── docs/                               # 项目文档（本目录）
│   ├── status/                     # 各阶段详细状态
│   └── changes/                    # 按周改动记录
│
├── enums_mapping.json              # 枚举字段映射表
├── project_status.md               # 状态文档快速索引
├── change_record.md                # 改动记录快速索引
└── .env                            # API Key（不入 git）
```

---

## 技术栈

| 组件      | 技术                                       |
| ------- | ---------------------------------------- |
| 编排      | 纯 Python（v2.1 P5 已移除 crewAI；老版 s01_data_collector_filter 仍用 crewai 1.9.3，归档候选） |
| LLM（主）  | Gemini gemini-2.5-flash-lite（可选 DeepSeek，见 eval.md A/B；分阶段路由 StageRouter） |
| LLM（嵌入） | Gemini text-embedding-004                |
| AI SDK  | google-genai >= 1.49.0                   |
| 外部数据源   | GDELT 国际新闻数据库（免费，无 API Key）              |
| 模糊匹配    | rapidfuzz >= 3.0.0                       |
| 数据处理    | pandas >= 2.0.0                          |
| 数据库     | SQLite（sqlite3，内置）                       |
| 网络请求    | requests >= 2.31.0                       |
| 前端框架    | React 18 + TypeScript + Vite             |
| 前端样式    | Tailwind CSS                             |
| 前端地图    | react-simple-maps + world-atlas TopoJSON |
| 前端图表    | recharts                                 |

---

## 已知问题

| 问题                                                                      | 优先级 | 状态              |
| ----------------------------------------------------------------------- | --- | --------------- |
| `pipeline` 预留模块导入耦合、`s01a` 归档目录不存在崩溃、`s01b` Guardrail 潜在误删（batch_id 缺失） | 高   | 已修复（2026-03-22） |
| `s02b` CLI 参数解析覆盖流程传参、`s02a` imported 标记失败静默导致潜在重复导入                    | 高   | 已修复（2026-03-22） |
| `s01_data_collector_filter/custom_tool.py` 仍为模板占位符                      | 低   | 未修复             |
| `s01_data_collector_filter/README.md` 仍是 crewAI 默认模板（路径与说明未同步）          | 低   | 未修复（老版归档候选）     |
| 老版 `s01_data_collector_filter` 仍依赖 crewAI（s01a+s01b 已取代它）               | 低   | 归档/删除候选，单独决策   |
| s01b orchestrator 3 个 s34 测试预存失败（`_FakeLLM` 缺 's34' 键，源自 e0cc102）       | 中   | 未修复（s01b 线单独处理，与去 crewAI 无关） |
| `pipeline/` 首次 `uv sync` 需要各子项目已安装依赖                                    | 低   | 已知，文档说明         |
| s04 省级地图数据（10m zip）在国内网络下载受限，需 VPN                                      | 中   | 已知，待解决          |
