# BRI_RE_Database_v2

"一带一路"可再生能源项目数据库——追踪中国企业参与的海外可再生能源项目。

**流水线**：数据收集（s01a 爬虫 → s01b AI 提取）→ 去重合并（s02a）→ 实体归一化（s02b）→ 交叉验证（s02c，可选）→ 数据导出（s03）→ 网页可视化（s04）。

## 目录导览

| 目录 | 内容 |
|---|---|
| `s01a_news_crawler/` … `s04_web_visualization/` | 流水线各阶段子项目（`sNN_name` 约定，各自独立 venv） |
| `pipeline/` | 全流水线纯 Python 顺序编排 |
| `eval/` | s01b 提取质量评测框架（金标准 + 字段级准确率） |
| `review/` | 提取结果复核机制（critic 批判 + 复核队列） |
| `enums_mapping.json` + `bri_enums.py` / `bri_parse.py` | 枚举与数值解析的单一可信源 |
| `scripts/` | 管线/迁移脚本；`scripts/oneoff/` 为一次性脚本与其输出（仅留档） |
| `supporting_docs/` | AI 提取知识库（母子公司层级、央企名录等） |
| `external-tools/` | 独立的外部小工具 |
| `archive/` | 已被取代的旧版子项目（仅留档，勿使用） |
| `tests/` | 根模块（eval / review / bri_*）的测试；各子项目测试在各自 `tests/` |

## 文档

- **项目状态**：`docs/status/`（总览 `overview.md`）
- **修改日志**：`docs/changes/`（按周拆分）
- **计划与规范**：`docs/rules_and_plans/`、`docs/superpowers/`

## 常用命令

见 `Makefile`（`make help`）或 `bash bri.sh help`。
