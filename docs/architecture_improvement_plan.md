# 系统架构改进方案

> 创建于课程 Unit 5：前端工程（前后端分离讨论）
> 状态：备忘录，待项目扩张时参考

---

## 当前架构

```
Python 管道（手动触发，月跑一次）
  └─ 爬虫 → AI提取 → 清洗 → 导出 JSON 文件
                                    ↓
                            前端直接读取 JSON
                            浏览器内完成所有过滤
```

**适用条件：** 数据量可控、月度更新、无用户系统需求。

**当前瓶颈：**
- 数据量增大后 JSON 文件体积增大，前端初始加载变慢
- 数据更新需要手动触发 Python 管道，无法自动化
- 所有过滤逻辑在浏览器内执行，数据量大时性能下降

---

## 目标架构

```
Python 管道（定时任务，自动触发）
  └─ 爬虫 → AI提取 → 清洗 → 写入数据库
                                    ↑
                            API 服务（持续运行）
                                    ↑
                            前端发起查询请求
                            （携带筛选参数）
```

---

## 各层改动说明

### 1. Python 数据管道（改动最小）

现有爬虫、AI提取、清洗逻辑**不需要重写**，保持原有结构。
唯一变化：最终写入持久化数据库（如 PostgreSQL），而不是导出 JSON 文件。
运行方式改为定时任务（GitHub Actions cron），无需手动触发。

### 2. 新增 API 层（核心新增部分）

使用 **Python FastAPI** 或 **Node.js Express** 实现，持续运行，连接数据库，
接收前端请求并返回 JSON 数据。

示例接口设计：

```
GET /api/projects?year=2024&category=风电&entity=三峡能源&page=1&page_size=50
GET /api/stats/country
GET /api/stats/timeline
GET /api/metadata
```

代码量估计：200–400 行，复杂度低。

### 3. 前端改动

- 删除 `DataContext.tsx` 里的 `applyFilter` 本地过滤函数
- 改为每次筛选条件变化时向 API 发起请求
- 增加分页逻辑（每次只加载当前页数据）
- 其余组件（地图、图表、表格）基本不需要改动

---

## 推荐技术选型（低成本方案）

| 组件 | 推荐方案 | 费用 |
|------|---------|------|
| 数据库 | Supabase（托管 PostgreSQL） | 免费额度够用 |
| API 服务 | Railway 或 Vercel（部署 FastAPI） | 小项目基本免费 |
| 定时任务 | GitHub Actions（cron 触发 Python 管道） | 免费 |
| 前端托管 | 现有服务器或 Vercel | 不变 |

现有服务器配置足以运行 API 层，无需升级。
升级配置的触发条件：并发用户量大，或将 AI 模型改为本地部署。

---

## 流水线阶段间容错设计

**当前问题：** `pipeline/flow.py` 的各阶段之间没有输出验证。上一阶段静默失败时（如 s01a 爬取 0 条数据），下一阶段仍然触发，错误无声无息地传导到最终输出，整个管道看起来"成功完成"，实际数据没有更新。

**改进方案：** 每个阶段在入口处验证上一阶段的输出是否有效，无效则主动抛出异常，让 Flow 在明确的位置中断。

示例（在 `stage_01b_extract` 入口处添加）：

```python
@listen(stage_01a_crawl)
def stage_01b_extract(self):
    # 验证上一阶段输出
    db = Path(self.state.raw_news_db_path)
    if not db.exists():
        raise RuntimeError("raw_news.db 不存在，s01a 可能未成功执行")
    conn = sqlite3.connect(db)
    count = conn.execute(
        "SELECT COUNT(*) FROM raw_news WHERE passed_keyword_filter=1"
    ).fetchone()[0]
    conn.close()
    if count == 0:
        raise RuntimeError("无关键词匹配记录，跳过 s01b")
    # 验证通过，继续执行
    ProjectExtractor().crew().kickoff(...)
```

每个阶段都应添加类似的入口检查：

| 阶段 | 验证内容 |
|------|---------|
| s01b 入口 | `raw_news.db` 存在且有 `passed_keyword_filter=1` 的记录 |
| s02a 入口 | `projects.db` 存在且有新写入的记录（`imported_to_s02a=0`） |
| s03 入口 | `bri_re_database.db` 存在且 `canonical_projects` 表非空 |

**实施优先级：** 中等。建议在数据管道自动化（定时任务）之前完成，避免无人值守时静默失败。

---

## 测试策略

### 现有覆盖（已完成）

`s01b_project_extractor/tests/` 有 4 个测试文件，覆盖 guardrail 逻辑的单元测试。
使用 `unittest.mock` 替换数据库和 API 调用，测试速度快、无副作用。

### 测试优先级（待补充）

| 优先级 | 模块 | 测试类型 | 原因 |
|--------|------|---------|------|
| 🔴 高 | `s01b/project_extractor_tool.py` | 单元测试 | 字段解析、实体判断逻辑复杂，bug 直接污染数据库 |
| 🔴 高 | `s03/flat_builder.py` | 单元测试 | 阶段优先级排序、实体合并去重，逻辑复杂且当前无测试 |
| 🟡 中 | `DataContext.tsx` 过滤逻辑 | 单元测试 | `applyFilter` 和 `shouldExcludeByPolicy` 是前端核心业务逻辑 |
| 🟡 中 | `s01b → s02a` 写入/读取流程 | 集成测试 | 验证两模块协作是否正确，使用临时测试数据库 |
| 🟢 低 | 完整流水线 | 端到端测试 | 成本高、维护难，等项目稳定后再考虑 |

### Mock 的使用原则

- 数据库调用：始终使用 mock，避免读写真实数据，保持测试隔离性
- Gemini API 调用：必须使用 mock，避免产生 API 费用，保证测试确定性
- 文件 I/O：视情况，简单的可用临时目录（`tmp_path`），复杂的用 mock

---

## CI/CD 自动化方案

### 当前状态

仅有 `Makefile` 本地快捷命令，所有操作需手动执行。无自动化测试、无自动部署。

### 目标：GitHub Actions 两个工作流

**工作流一：代码检查（每次 push 自动触发）**

```yaml
# .github/workflows/ci.yml
on: [push]
jobs:
  test:
    - 运行 pytest（s01b 现有测试）
    - 运行 ruff 检查 Python 代码格式
    - 运行 npm run build 确认前端可构建
```

**工作流二：数据更新（每月定时自动触发）**

```yaml
# .github/workflows/monthly_update.yml
on:
  schedule:
    - cron: "0 2 1 * *"   # 每月1日凌晨2点
jobs:
  update:
    - 运行 make pipeline   # 爬虫 → AI提取 → 清洗
    - 运行 make build      # 构建前端
    - 部署新 dist/ 到服务器
```

### API Key 安全管理

**不能**把 API Key 写进工作流配置文件（会泄露到 git 历史）。

正确做法：
1. 在 GitHub 仓库 Settings → Secrets 里存入 `GEMINI_API_KEY`
2. 工作流中通过 `${{ secrets.GEMINI_API_KEY }}` 注入为环境变量
3. 代码里用 `os.environ["GEMINI_API_KEY"]` 读取
4. 确认 `.env` 文件在 `.gitignore` 里，永远不提交到仓库

### 实施步骤

1. 确认 `.env` 已在 `.gitignore` 中
2. 在 GitHub Secrets 添加所有 API Key
3. 创建 `.github/workflows/ci.yml`（代码检查）
4. 创建 `.github/workflows/monthly_update.yml`（定时数据更新）
5. 手动触发一次工作流验证配置正确

---

## 项目成熟度评估

| 维度 | 评分 | 说明 |
|------|------|------|
| **错误处理** | 4/10 | 有局部重试机制和 guardrail，但 `except: pass` 和无状态码检查是系统性问题 |
| **日志系统** | 2/10 | 全部使用 `print()`，无持久化日志，无级别分类 |
| **数据库设计** | 6/10 | 模块间单向依赖清晰，建表规范，缺少关键索引，逐条 commit 待优化 |
| **并发安全** | 5/10 | `_name_cache_lock` 正确，`_last_batch_id` 竞争条件是已知 bug |
| **前端架构** | 7/10 | 组件划分合理，TypeScript 完整，Context 设计合适，缺运行时数据验证 |
| **模块化设计** | 7/10 | 单向数据流、Flow 编排层、Pydantic 状态模型，明显优于平均 vibe coding 项目 |
| **测试覆盖** | 3/10 | s01b guardrail 有规范测试，其余模块零覆盖 |
| **CI/CD 自动化** | 1/10 | 仅有 Makefile 本地快捷命令，无自动化流程 |

---

## 升级为生产级系统：最优先的 3 件事

### 第一优先：修复错误处理

**涉及文件：**
- `s01a_news_crawler/tools/news_crawler_tool.py`：加 HTTP 状态码检查，替换 `except: pass`
- `s01b_project_extractor/tools/project_extractor_tool.py`：`_last_batch_id` 加锁，替换 `except: pass`

**为什么最优先：** 成本最低、收益最高。不改这个，整个系统是"静默失败"的黑盒，后续所有改进都建立在看不见内部状态的基础上。

### 第二优先：引入 logging + 阶段间输出验证

**涉及文件：**
- 所有 Python 模块：`print()` → `logging`，日志写入文件
- `pipeline/flow.py`：每个阶段入口加输出验证（见"流水线阶段间容错设计"章节）

**为什么第二：** 完成后整个流水线有完整可观测性。月度更新无需盯屏幕，事后从日志还原完整执行过程。

### 第三优先：GitHub Actions 自动化数据更新

**涉及文件：**
- 新建 `.github/workflows/ci.yml`
- 新建 `.github/workflows/monthly_update.yml`

**为什么第三：** 直接实现"数据定期自动更新"目标，同时让现有测试文件真正发挥作用。前两件事完成后再做，确保自动化运行的是健壮的代码。

---

## 改进时机建议

满足以下任一条件时开始改造（静态架构 → 前后端分离）：

1. `projects_flat.json` 超过 **10 MB**，前端加载明显变慢
2. 需要数据**自动更新**，不再依赖手动触发管道
3. 需要引入**用户账号系统**或权限控制

当前阶段无需改动，现有静态架构仍然适用。
