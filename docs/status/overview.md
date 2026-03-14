# BRI_RE_Database_v2 项目概述

> 最后更新：2026-03-14

---

## 项目概述

**"一带一路"可再生能源项目数据库** — 追踪中国企业参与的海外可再生能源项目。

**流水线架构：**
```
数据收集 → 去重合并 ──→ 数据库整理 → 网页可视化 → 数据分析 → 报告撰写
   ✅       ✅    ↓      ✅         🚧         ⬜        ⬜
            交叉验证（可选）✅
```

> 阶段二已拆分为两个独立子项目：`s02a_duplicate_merger`（必选）+ `s02b_cross_verification`（可选）。
> 去重合并后可直接进入数据库整理，也可先做交叉验证再整理。

---

## 各阶段状态速览

| 阶段 | 子项目 | 状态 | 详情 |
|------|--------|------|------|
| 阶段一 | `s01_data_collector_filter` | ✅ 已完成 | [s01.md](s01.md) |
| 阶段二A | `s02a_duplicate_merger` | ✅ 已完成 | [s02a.md](s02a.md) |
| 阶段二B | `s02b_cross_verification` | ⚠️ 待修改（可选） | [s02b.md](s02b.md) |
| 阶段三 | `s03_data_exporter` | ✅ 已完成（细节待调整） | [s03.md](s03.md) |
| 阶段四 | `s04_web_visualization` | 🚧 调试中 | [s04.md](s04.md) |
| 编排 | `pipeline` | ✅ 已完成 | [pipeline.md](pipeline.md) |
| 阶段五 | 数据分析 | ⬜ 未开始 | — |
| 阶段六 | 报告撰写 | ⬜ 未开始 | — |

---

## 阶段五：数据分析 ⬜ 未开始

---

## 阶段六：报告撰写 ⬜ 未开始

---

## 项目结构

```
BRI_RE_Database_v2/
├── pipeline/                           # 全流水线 crewai Flow 编排
│   ├── pyproject.toml
│   └── src/bri_pipeline/
│       └── flow.py                 # BRIPipelineFlow + PipelineState
│
├── s01_data_collector_filter/          # 阶段一 crewai 子项目
│   ├── pyproject.toml
│   ├── Crawler.py                  # 独立爬虫脚本
│   ├── filter.py                   # 独立 AI 筛选脚本
│   └── src/s01_data_collector_filter/
│       ├── main.py                 # run() + run_parallel() + split_date_range()
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       ├── tools/news_crawler_tool.py
│       ├── tools/project_extractor_tool.py
│       └── knowledge/user_preference.txt
│
├── s02a_duplicate_merger/               # 阶段二A crewai 子项目（去重合并与入库）
│   ├── pyproject.toml
│   ├── data/db/                    # SQLite 数据库（bri_re_database.db）
│   └── src/s02a_duplicate_merger/
│       ├── main.py
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       └── tools/duplicate_merger_tool.py
│
├── s02b_cross_verification/             # 阶段二B crewai 子项目（外部交叉验证，可选）
│   ├── pyproject.toml
│   ├── data/verification_cache/    # GDELT 文章 & 向量嵌入缓存
│   ├── data/manual_review/         # 低置信度人工核查 Excel
│   └── src/s02b_cross_verification/
│       ├── main.py
│       ├── crew.py
│       ├── config/{agents,tasks}.yaml
│       └── tools/cross_verification_tool.py
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
├── docs/                               # 项目文档（本目录）
│   ├── status/                     # 各阶段详细状态
│   └── changes/                    # 按周改动记录
│
├── company_name_cache.json         # 中文公司名 → 英文缓存（41条）
├── enums_mapping.json              # 枚举字段映射表
├── WorldCountryList.csv            # 国家数据库（ISO/BRI/LDC 信息）
├── project_status.md               # 状态文档快速索引
├── change_record.md                # 改动记录快速索引
└── .env                            # API Key（不入 git）
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| AI 框架 | crewai 1.9.3 |
| LLM（主） | Gemini gemini-2.5-flash-lite |
| LLM（嵌入） | Gemini text-embedding-004 |
| AI SDK | google-genai >= 1.49.0 |
| 外部数据源 | GDELT 国际新闻数据库（免费，无 API Key） |
| 模糊匹配 | rapidfuzz >= 3.0.0 |
| 数据处理 | pandas >= 2.0.0 |
| 数据库 | SQLite（sqlite3，内置） |
| 网络请求 | requests >= 2.31.0 |
| 前端框架 | React 18 + TypeScript + Vite |
| 前端样式 | Tailwind CSS |
| 前端地图 | react-simple-maps + world-atlas TopoJSON |
| 前端图表 | recharts |

---

## 已知问题

| 问题 | 优先级 | 状态 |
|------|--------|------|
| 根目录 `pyproject.toml` 的 `requires-python = ">=3.14"` 错误 | 低 | 未修复 |
| `Crawler.py` 第178行重复的 `is_relevant` 赋值 | 低 | 未修复 |
| `s01_data_collector_filter/custom_tool.py` 仍为模板占位符 | 低 | 未修复 |
| Windows 终端 GBK 编码导致 emoji 乱码（crewai 事件总线） | 低 | 未修复（仅影响显示） |
| `pipeline/` 首次 `uv sync` 需要各子项目已安装依赖 | 低 | 已知，文档说明 |
| s04 省级地图数据（10m zip）在国内网络下载受限，需 VPN | 中 | 已知，待解决 |
