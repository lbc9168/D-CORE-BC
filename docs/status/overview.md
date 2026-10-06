# BRI_RE_Database_v2 项目概述

> 最后更新：2026-10-06（S01B/S02A/S02B 模型路由与 S02C 嵌入迁移；生产数据快照截至此前批次）

本次2026-09-22—09-30含首尾正常增量已沿既有Workflow运行到生产入库及本地导出。新增54项目、60事件、112关系，生产canonical/event/关系4811/5680/6917。1身份协调任务阻断2源行，另16条pending_review未入正式业务，最终status=partial。现有成功及13篇结果缓存复用，最终文章级技术失败0；重复原入口请求0且四库SHA256不变。详见`output/incremental-20260922-20260930-20261004/report.md`。

当前本地metadata.last_updated=2026-09-30（只表示最新已入库事件日期）；projects_flat3745、TX474、MF161、事件CSV3689行。默认发电/储能1600、输变电/制造业492。S03离线省份缓存导出完成无警告，npm build及本地地图/表格检查通过；预览http://127.0.0.1:5178/，未推送或远程部署。已发现药厂/LAB两条普通工业项目误归MF并进入网页/CSV，以及伊塞克湖垃圾发电疑似漏并；保留检查证据，尚未另起纠错。备份、ID/URL映射、原始响应和剩余待办见`output/incremental-20260922-20260930-20261004/report.md`。

此前纠错恢复已经获批并完成生产回填，不能以旧隔离阶段production_applied=false判断现状；正式历史回执为`output/correction-recovery-20260923/production-commit-20260923-authorized-v2/report.md`。主工作区main，已有修改保留，旧工作树已归档。

---

## 当前 Git 工作区布局

历史更正沿已获批的精确delta及协调入口完成生产回填；正常增量按URL续跑，不清空全库import标记。当前09-22—09-30批次的事实、备份和未决项目以上方最新摘要及本轮report为准。

- 仓库根目录 `E:/Projects/BRI_RE_Database_v2` 固定检出 `main`，作为稳定代码线。
- 企业身份修复工作区的受控名录、证据门控、补录工具及S1召回已收拢并本地提交到main；新命令使用主线修复代码。历史试跑与备份位于 `output/entity-s1-recovery-20260917/`。
- 2026-09-23受控写入后的四库hash、备份及精确增量见`output/production-pilot-commit-20260923-161131-v2/production-result.json`；历史候选203行/146篇已在纠错副本逐项审定并实际修复可恢复项，已完成生产回填；13个已恢复项目直接核验保持不变。响应结构、设备参与和容量修复的离线/真实验证产物在 `output/entity-recovery-20260923/`。本轮136条项目源与21条非项目源已依据既有全文完成生产恢复，28条实质排除、2条地点协调，其余16条跳过；不要求重复模型调用。逐项验收与精确生产方案见`output/correction-recovery-20260923/report.md`。
- 2026-09-23已完成工作区整理：当前仅注册根目录main。entity-identity-repair无独有代码，web-display-cleanup的有效筛选/能源详情改动已择取，database-worktree-safety仅吸收只读核验工具与安全原则；旧整库promote及8月manifest未纳入。
- 三个旧worktree已在外部`E:/Projects/BRI_RE_Database_v2-workspace-backup-20260923/`完成逐文件hash核验归档后移除，分支和Git bundle仍保留；output、备份、日志及临时目录留在磁盘但不进入新提交。未推送、未部署；生产纠错delta随后已获批应用。
- SQLite、审计 CSV、导出 JSON 等被 Git 忽略的运行数据按 worktree 分别保存；分支合并只带入
  schema、迁移/回填脚本、测试与文档，不会自动合并数据库文件。正式更新稳定库时，必须先备份，
  再在 `main` 上显式执行已验证的幂等迁移/回填流程。

---

## 流水线架构

S02E现为默认增量阶段：S02B之后、S03之前发现新增/变更及到期项目/事件，主库持久保存参考链接，符合严格证据条件时顺带补空发电容量。私有找源范围覆盖2018年以来，网页2022年基线不变。当前全量任务为2654参考项目及311缺容量项目（3665事件）；本轮累计20美元预算及持久队列见[s02e.md](s02e.md)。费用耗尽/服务失败保留待处理并让流水线标记partial，不阻断已有数据导出。

```
数据收集 ──→ 去重合并 ──→ 实体/地点归一化 ──→ 数据库整理 ──→ 网页可视化 ──→ 数据分析 ──→ 报告撰写
s01a + s01b    s02a            s02b              s03            s04        （未开始）   （未开始）
                          交叉验证 s02c（可选支线）
```

- 阶段一拆为 **s01a（新闻爬虫）** + **s01b（AI 提取）** 两个子项目。
- 阶段二拆为 **s02a（去重合并，必选）** + **s02b（实体名 + 地点归一化）** + **s02c（外部交叉验证，可选）**。
- 去重合并后即可进入数据库整理；交叉验证是可选支线。
- 各子项目有独立的 `pyproject.toml` 与 venv；`pipeline/` 提供一键顺序编排。

---

## 各模块当前状态

| 阶段 | 子项目 | 状态 | 文档 |
|------|--------|------|------|
| 一A 新闻爬虫 | `s01a_news_crawler` | ✅ 运行中 | [s01a.md](s01a.md) |
| 一B AI 提取 | `s01b_project_extractor` | ✅ 运行中 | [s01b.md](s01b.md) |
| 二A 去重合并 | `s02a_duplicate_merger` | ✅ 运行中 | [s02a.md](s02a.md) |
| 二B 实体审核建议/地点等后处理 | `s02b_entity_normalizer` | ✅ 运行中 | [s02b.md](s02b.md) |
| 二C 交叉验证（可选） | `s02c_cross_verification` | ⚠️ 受限（GDELT 无匹配信源） | [s02c.md](s02c.md) |
| 三 数据库整理 | `s03_data_exporter` | ✅ 运行中 | [s03.md](s03.md) |
| 四 网页可视化 | `s04_web_visualization` | ✅ 运行中（GitHub Pages 已部署） | [s04.md](s04.md) |
| 编排 | `pipeline` | ✅ 运行中 | [pipeline.md](pipeline.md) |
| 五 数据分析 | — | ⬜ 未开始 | — |
| 六 报告撰写 | — | ⬜ 未开始 | — |

> 旧版阶段一 `s01_data_collector_filter`（爬虫 + 提取合并、依赖 crewAI）已归档至 `archive/`，被 s01a + s01b 取代，不再维护。
> `external-tools/` 下的独立小工具（不属主流水线）另见 [external-tools.md](external-tools.md)。

---

## 数据流

```
s01a: raw_news.db
  → s01b: projects.db（extracted_projects）
    → s02a: bri_re_database.db（主库，三表 + non_project_records）
      → s02b: 原地更新主库——实体/地点/业主归一化（implementing_entity / location_norm / project_developer）
              + 英文名回填(*_en 三列) + TX/MF 指标回填(5 稀疏列) + 剩余名称翻译（流水线 2B~2B-6）
              + 严格储能事实回填(2B-7) → 能源分类影子快照与 CSV/JSON 差异报告(2B-8)
        →（可选 s02c: 写回验证字段）
          → s03: projects_flat + tx/mf_flat + 3 张 stats 表 → JSON/CSV
            → s04: 前端直接 fetch 静态 JSON/CSV
```

主库为 SQLite 三表模型（`canonical_projects` / `project_events` / `chinese_entities`）+ `non_project_records`，并新增内部 `project_classification_shadow` 审计表。`energy_taxonomy_v2` 先在 shadow 记录差异；只有证据与 Terra/Sol 门禁通过的 resolved 项才写正式类别、技术标签和事件能源事实，未决项只写 `needs_review`，不会进入自动覆盖。

### 当前光储分类口径 v2

- 储能不再作为发电源：单一新能源配储回归光伏/风电等主类，多种新能源才归综合能源，独立储能归储能；组合信息改由 `technology_tags_json` 表达。
- 809 个候选完成 Terra 全量重读，789 个风险项目完成 Sol 复核；594 个 resolved 项安全应用，215 个未决项只写 shadow。resolved 正式记录中旧 `综合能源/光储|其他` 子类为 0；57 个未决旧子类保持原值，但 s03/s04 不再导出。
- 当前生产canonical/event/entity为`4811/5680/6917`，non_project_records=`1591`、project_classification_shadow=`3779`，integrity=ok/FK=0。本地导出projects/TX/MF/events=`3745/474/161/3689`，metadata=2026-09-30；1协调及16待复核源行仍未完成，线上未部署。
- Golden Standard Studio 已暂停并按旧 guideline 原样封存；未用本轮 taxonomy 静默改写 Pilot、40 篇候选或人工草稿。

### 2026-08-15：封存 Gold-18 模型替换诊断

- 只读复用暂停快照 18 篇 confirmed development Gold（24 项目）比较 baseline、Gemini 3.5 + DeepSeek、GPT-5.4 Nano + DeepSeek，以及 S1=Gemini 3.5/S2=Gemini 2.5/S34+S5=GPT-5.4 Nano 的混合路由；23627 未确认并排除，生产数据库/模型默认均未修改。
- 四组均完成 18/18。混合路由状态准确率 1.0000、项目 F1 0.7843，接近 baseline 的 0.8077，并修复 14595 的严重过拆；但 1533 仍 8→11 且实体角色退化，因此保持 challenger，不切换生产默认。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 编排 | 纯 Python 顺序编排（无 crewAI） |
| LLM（主） | S01B S1/S2、S02A 身份确认/字段仲裁/地点推断/批内地点归一、S02B 业主归一/地点归一/剩余英文翻译：GPT-6 Luna（按任务使用 Low/Medium，见模块状态）；S01B S3/S4/S5/S34：DeepSeek；其他 Gemini 路由保持原设置 |
| LLM（嵌入） | Gemini Embedding 2（`gemini-embedding-2`，s02c；旧向量缓存不兼容，需重建） |
| AI SDK | google-genai |
| 外部数据源 | GDELT 国际新闻数据库（s02c，免费无 Key） |
| 模糊匹配 | rapidfuzz |
| 数据处理 | pandas + sqlite3（内置） |
| 网络请求 | requests + BeautifulSoup（s01a 爬虫） |
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS |
| 前端地图/图表 | react-simple-maps + world-atlas TopoJSON / recharts |

---

## 单一可信源（跨模块共享）

| 文件 | 用途 |
|------|------|
| `enums_mapping.json` | 全部枚举字段（类别/阶段/参与方式/融资模式 + 类别代码 + color/no_capacity/hidden/excluded 标记 + stage priority + viz_group 四大类映射）的唯一定义 |
| `bri_enums.py` | 枚举加载器（Python）；s04 构建期经 `scripts/copy-enums.mjs` 拷入前端 |
| `bri_parse.py` | 数值字段（容量/投资额等）的统一解析 |
| `bri_location.py` | 地点名归一化的匹配规则（s02b 地点归一用） |
| `supporting_docs/company_hierarchy_v2.json` | 母子公司层级 + 央企标记（152 组；s01b 写库前查表解析 + s02b 查表引擎复用；旧 company_hierarchy.json 已废弃） |
| `developer_registry.json` | developer 名称登记册（73 家：canonical_cn/en + 跨语言别名；s02b 业主归一与英文名回填的最高优先档） |
| `supporting_docs/company_name_cache.json` | 公司英文名主缓存（人工核实口径，en_abbr 优先）；`residual_translation_cache.json` 为 2B-6 的 LLM 长尾翻译缓存（llm_auto 口径，与主缓存隔离） |
| `supporting_docs/soe.md` | 官方央企名录（央企判断兜底） |

---

## 项目结构

```
BRI_RE_Database_v2/
├── pipeline/                  # 全流水线纯 Python 顺序编排（run_pipeline）
├── archive/                   # 已归档的旧版子项目（仅留档，勿用）
│   └── s01_data_collector_filter/
├── s01a_news_crawler/         # 阶段一A：新闻爬虫 → raw_news.db
├── s01b_project_extractor/    # 阶段一B：AI 提取 + 三层校验 → projects.db
├── s02a_duplicate_merger/     # 阶段二A：去重合并入库 → bri_re_database.db（主库）
├── s02b_entity_normalizer/    # 阶段二B：实体只读建议 + 地点/业主/英文名后处理
├── s02c_cross_verification/   # 阶段二C：外部交叉验证（可选）
├── s03_data_exporter/         # 阶段三：整理导出 JSON/CSV（自动同步到 s04）
├── s04_web_visualization/     # 阶段四：React 前端（静态数据）
├── eval/                      # s01b 提取评测框架（金标准 + 字段级准确率）
├── review/                    # s01b 提取复核机制（critic + 复核队列）
├── codex_review/              # Codex 季度分拣管线（T1漏合并/T2范围/T3实体补录，独立于流水线，见 external-tools.md）
├── external-tools/            # 不属于主流水线的独立小工具
├── supporting_docs/           # AI 提取知识库（公司层级 / 央企名录 / 名称缓存）
├── scripts/                   # 流水线/迁移脚本（oneoff/ = 一次性脚本留档）
├── tests/                     # 根模块测试（子项目测试在各自 tests/）
├── enums_mapping.json / bri_enums.py / bri_parse.py / bri_location.py   # 单一可信源
├── docs/                      # status（当前状态）/ changes（变更日志）/ rules_and_plans（规则与计划）
└── .env                       # API Key（不入 git）
```

---

## 当前已知问题与限制

| 问题 | 影响 | 状态 |
|------|------|------|
| s02c GDELT 查询找不到与主库匹配的信源 | 交叉验证（可选步骤）当前不可用，需另寻信源 | 待解决，见 [s02c.md](s02c.md) |
| s04 省级地图 10m zip 在国内网络下载受限 | 首次生成省级 GeoJSON 需 VPN 或镜像 | 已知限制 |
| `pipeline/` 首次 `uv sync` 需各子项目目录已存在 | 自动以 editable 模式安装，目录缺失会失败 | 已知，文档说明 |
| s02b 地点归一化质量依赖 LLM | 音译近似的不同省可能偶发误并；`location_norm` 当前不喂下游，属软信息 | 已知，见 [s02b.md](s02b.md) |
