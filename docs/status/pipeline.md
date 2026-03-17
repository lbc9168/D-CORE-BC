# 流水线编排与批量采集

> 最后更新：2026-03-17

---

## 流水线编排（crewAI Flows）

**子项目路径：** `pipeline/`

一次命令运行完整四阶段流水线，支持条件跳转。

### 执行流程

```
BRIPipelineFlow
  stage_01_collect  (@start)          ← 可通过 skip_collection=True 跳过
       ↓
  stage_02a_merge   (@listen)
       ↓
  route_cross_verification (@router)
    ├── "run_verify" → stage_02b_verify ← run_cross_verification=True（默认）
    └── "done"       → finish_no_verify ← run_cross_verification=False
              ↓ (两路均汇入)
         _run_stage_03()               ← 可通过 run_export=False 跳过
              ↓
         s03_data_exporter.run()       ← 省份提取 + 扁平表 + JSON 同步到 s04
```

### 运行方式

```bash
cd pipeline
uv sync                      # 首次安装（会自动安装四个本地子项目）
uv run run_pipeline          # 完整流水线（含交叉验证 + 导出）
uv run run_merge_only        # 采集 + 去重 + 导出（跳过交叉验证）
uv run run_collect_and_merge # 仅采集 + 去重合并（阶段一 → 阶段二A，不导出）
uv run run_export_only       # 仅重新导出（数据库已有时）
```

### PipelineState 参数

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip_collection` | bool | False | 跳过阶段一（直接从现有 projects.db 开始） |
| `run_cross_verification` | bool | True | 是否执行阶段二B |
| `run_export` | bool | True | 是否执行阶段三导出 |

---

## 批量并发采集（kickoff_for_each_async）

在 `s01_data_collector_filter` 中新增，用于历史数据补录。

### 运行方式

```bash
cd s01_data_collector_filter
uv run run_parallel          # 使用默认参数（2023-01-01 → 2026-03-08，按月拆分）
```

或在代码中调用：
```python
from s01_data_collector_filter.main import run_parallel, split_date_range

run_parallel("2024-01-01", "2025-12-31", chunk_months=1)   # 24批并发
run_parallel("2023-01-01", "2026-03-08", chunk_months=3)   # ~13批（限速时推荐）
```

### split_date_range 说明

`split_date_range(start, end, chunk_months)` 将日期范围按月份数均匀拆分，返回 `[(start1, end1), (start2, end2), ...]` 列表，每个区间作为一个独立的 crewai Crew 实例并发执行。

- **并发限制**：crewai 内部通过 `kickoff_for_each_async` 管理并发，默认不限制并发数
- **推荐配置**：大范围历史补录建议使用 `chunk_months=3`，降低反爬风险
- **断点恢复**：每批采集结果写入 `raw_news.db`，可通过 `start_page` 参数从断点恢复单批次

---

## 大规模历史补录（run_bulk）

用于超长时间段补录（例如 2018–2026），通过“两阶段”降低封禁和限流风险：

1. **顺序爬虫阶段**：按日期段倒序抓取，批次间延迟（默认 10s），并通过 checkpoint 进行页码接力  
2. **并发提取阶段**：对 Phase 1 有数据的批次并发执行 AI 提取（默认 4 并发）

```bash
cd s01_data_collector_filter
uv run run_bulk
```

可调参数：
- `chunk_months`：每批月份数（值越大，批次数越少）
- `crawl_delay`：爬虫批次间等待秒数
- `max_extract_workers`：AI 提取并发线程数

---

## 首次运行注意事项

1. `pipeline/` 首次 `uv sync` 需要各子项目已在本地通过 `uv sync` 安装依赖
2. `.env` 文件需在根目录存在，包含 `GEMINI_API_KEY`
3. 阶段二B（交叉验证）当前存在 GDELT 数据匹配问题，建议使用 `run_merge_only` 跳过
