# BRI_RE_Database_v2 项目概述

> 最后更新：2026-06-24

**"一带一路"可再生能源项目数据库** —— 追踪中国企业参与的海外可再生能源项目。
纯本地流水线（Python）+ 静态前端（React），无后端服务；主存储为 SQLite，前端消费静态 JSON/CSV。

---

## 流水线架构

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
| 二B 实体/地点归一化 | `s02b_entity_normalizer` | ✅ 运行中 | [s02b.md](s02b.md) |
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
      → s02b: 原地归一化 implementing_entity / location_norm
        →（可选 s02c: 写回验证字段）
          → s03: projects_flat + 3 张 stats 表 → JSON/CSV
            → s04: 前端直接 fetch 静态 JSON/CSV
```

主库为 SQLite 三表模型（`canonical_projects` / `project_events` / `chinese_entities`）+ 一张 `non_project_records` 扁平表，详见 [s02a.md](s02a.md)。

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 编排 | 纯 Python 顺序编排（无 crewAI） |
| LLM（主） | Gemini gemini-2.5-flash-lite；s01b 分阶段可路由 DeepSeek（见 `rules_and_plans/eval.md`） |
| LLM（嵌入） | Gemini text-embedding-004（s02c） |
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
| `supporting_docs/company_hierarchy.json` | 母子公司层级 + 央企标记（s01b 提取注入） |
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
├── s02b_entity_normalizer/    # 阶段二B：实体名 + 地点归一化（原地更新主库）
├── s02c_cross_verification/   # 阶段二C：外部交叉验证（可选）
├── s03_data_exporter/         # 阶段三：整理导出 JSON/CSV（自动同步到 s04）
├── s04_web_visualization/     # 阶段四：React 前端（静态数据）
├── eval/                      # s01b 提取评测框架（金标准 + 字段级准确率）
├── review/                    # s01b 提取复核机制（critic + 复核队列）
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
