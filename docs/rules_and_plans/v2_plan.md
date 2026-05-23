# BRI_RE_Database v2 改动主计划（待审）

> 创建：2026-05-23
> 状态：**待你审阅**。本文件是 v2 的总纲与 session 拆分，不含代码改动。
> 依据：综合 [check-record.md](check-record.md) + [change-suggestion.md](change-suggestion.md) + [problems-and-expectations.md](problems-and-expectations.md) + [architecture_improvement_plan.md](architecture_improvement_plan.md) + [coding_rules_draft.md](coding_rules_draft.md)，并核对了当前真实代码与数据库状态。
> 每个 session 真正开工时，会在 `docs/rules_and_plans/sessions/` 下生成该 session 的逐步 TDD 细化计划。

---

## 〇、版本切分（已与用户确认）

| 版本           | 主题                                 | 本计划是否覆盖 |
| ------------ | ---------------------------------- | ------- |
| **v2.0（本次）** | 数据正确性 + 时间建模                       | ✅ 全部    |
| v2.1（紧接）     | 提取多 agent 化 + 分类规则补全 + 金标准评测 + 复核流 | ❌ 仅记锚点  |
| v3（大版本）      | 多领域扩展 + 前后端分离 + 自动运行 + 访问/IP 分析    | ❌ 仅记锚点  |

**v2.0 目标（一句话）**：在不重建数据、不上后端的前提下，修掉"数值字段全 TEXT、枚举四处硬编码、编号倒序、早期项目在时间维度消失、跨语言/音译漏合并"这五个地基级数据正确性问题。

**v2.0 明确不做**（防范围蔓延）：多 agent 提取重构、外部信源换源、多领域 domain 字段、后端 API、定时自动运行、访问统计。任一项若中途想加，先停下来评估是否推迟到 v2.1/v3。

---

## 一、当前真实状态（核对结果，作为计划依据）

**数据库（均不在 git，仅本地，*.db 全部被 .gitignore）：**

| 库                  | 路径                                                 | 规模                                                              |
| ------------------ | -------------------------------------------------- | --------------------------------------------------------------- |
| s01b 提取库           | `s01b_project_extractor/data/projects.db`          | extracted_projects 10815 行                                      |
| s02a 整理库（核心，含手动修正） | `s02a_duplicate_merger/data/db/bri_re_database.db` | canonical 4407 / events 5218 / entities 6316 / non_project 1471 |
| s01a 原始新闻          | `s01a_news_crawler/data/raw_news.db`               | 62 MB（仍在，重跑可行）                                                  |
| s03 导出库            | `s03_data_exporter/data/output/bri_re_export.db`   | —                                                               |

**已确认的关键事实：**

1. **`enums_mapping.json` 形同虚设**：全仓 grep，除文档外**无任何 .py / .ts 引用它**。枚举仍硬编码在 s01b prompt、s02a、s03 flat_builder、s04 多个 ts 文件。
2. **枚举漂移已发生**：`enums_mapping.json` 只有 7 个 category；`scripts/clean_historical_data.py` 的 `VALID_CATEGORIES` 有 12 个（多出 火电/核电/新能源制造业/输变电建设）；实际 DB 在用 12 个那套。**接单一可信源前必须先对账，以 DB 实际值为准补全 enums_mapping.json。**
3. **数值字段全是 TEXT**：`project_events.capacity_mw / total_investment_usd`、`extracted_projects.capacity_mw / subcontract_amount_usd / total_investment_usd` 全部 TEXT。
4. **迁移模板已存在**：`scripts/clean_historical_data.py` 是针对 projects.db 的字段清洗脚本，可作为本次迁移脚本的写法范本。
5. **s02a 核心库枚举值很脏（2026-05-23 S1 调研实测）**：`bri_re_database.db` 的 `canonical_projects.category` 有 **39** 个不同值、`project_events.project_stage` **47** 个、`financing_model` 12 个、`involvement_type` 9 个。规范集之外是大量长尾脏值：
   - **非可再生能源项目 ≈ 25 个**（石油/油气/油气开发/矿产开发/煤炭/石油化工/矿产资源/房建/工业园区/基础设施建设/农业灌溉 等），当前正喂给前端。**决策：S2 标记排除/移出。**
   - **可自动映射的变体**：垃圾发电→生物质、抽水蓄能→储能、光热；融资关闭/融资完成→签约、获批/规划→拟开发；PPP/BOT→对应融资模式等；甚至有整句误入 stage（`'计划于2026年同步启动改造两台机组'`）与英文 `'Concept Reviewed'`。**决策：S2 用映射表清洗。**
   - `clean_historical_data.py` 只清洗过 s01b 的 projects.db，**从未清洗 s02a 核心库**，脏值一路留到前端来源。
   - 完整取值清单见 `docs/rules_and_plans/sessions/_db_enum_values.txt`。

---

## 二、数据迁移评估与方案（你勾选了"先评估"）

**评估结论：推荐"在副本上原地迁移"，不重跑全流水线。**

理由：

- **重跑代价高且不可逆地丢信息**：重新提取 5218 个事件要大量 Gemini 调用（花钱）；s02a 去重用 LLM 判断，**非确定性**，重跑结果与现库不一致；你在 problems #7 手动修正的 ~8 条记录（IDN-HB-0001、SAU-WD-0005、UZB-BM-0001/0003、BRA-ES-0001、MYS-PV-0002、UZB-PV-0001、THA-HB-0001）以及 external-link-adder 补的链接**会全部丢失**。
- **原地迁移可保全**：4407 个项目与所有手动修正原样保留，只新增/转换列。
- DB 不在 git，**没有历史可回滚**，所以迁移**必须在副本上先验证**，主库操作前先 `.bak` 备份。

**原地迁移能做 / 不能做的：**

| 改动                                            | 历史数据能否回填           | 处理方式                                                                                                       |
| --------------------------------------------- | ------------------ | ---------------------------------------------------------------------------------------------------------- |
| capacity_mw TEXT→REAL（+ 保留 capacity_mw_raw）   | ✅ 能（解析现有文本）        | 迁移脚本 `_parse_number()`                                                                                     |
| total_investment / subcontract → REAL（+ _raw） | ✅ 能                | 同上                                                                                                         |
| display_order（按最早事件日期）                        | ✅ 能（现有 event 已带日期） | 迁移脚本计算                                                                                                     |
| storage_power_mw / storage_energy_mwh         | ⚠️ 部分              | 历史值散在 capacity_details 自由文本中；迁移时尽量正则提取，提不出的置 null + capacity_source='legacy'，不强行 AI 回填（留给 v2.1 或后续新数据自然补全） |
| capacity_source（原文/反推/人工/legacy）              | ✅ 能                | 历史行统一标 'legacy'，新提取才区分                                                                                     |

> **待你确认的迁移细节**：历史 storage_* 字段，是接受"提不出就留空+标 legacy"，还是要单独花一次 AI pass 从 capacity_details 反解？我倾向前者（省钱、v2 不引入新 AI 调用）。

---

## 三、Session 拆分（每个 = 一个可独立验证的闭环）

> 通用规则（每个 session 都遵守）：
> 开头读本文件 + 对应 `docs/status/*.md`；结尾更新 `docs/status` 与 `docs/changes`。
> 涉及真实数据的 session（S2/S5）：**先 `.db` 副本验证，再动主库，动前 `.bak` 备份**。
> 遵守 CLAUDE.md 全部规则（见 §四）。

### S1 · 枚举单一可信源（低风险，先做，**纯代码、不动数据**）

**为什么先做**：零数据语义风险；是后续所有"中英文一致"的地基；能立刻消灭"改一处漏三处"。

**范围界定**：S1 只做代码层单一可信源，**不清洗 DB 脏值**（脏值清洗 → S2）。即把规范枚举集中到 `enums_mapping.json` 并让全代码读它；DB 里的脏值由 S2 统一映射/排除。

**改动文件**（详见 `docs/rules_and_plans/sessions/S1_enums.md`）：

- `enums_mapping.json`：补全为规范集（11 类 + Unknown / 8 阶段 + Unknown / 6 融资 / 8 参与 / 4 名称质量），每项带 `zh/en/code` + category 加 `color/no_capacity/hidden/excluded`、stage 加 `color/priority`。
- 新增 `s0X_*/.../enums.py`（Python 加载器）+ `s04_web_visualization/src/utils/enums.ts`（前端加载器）。
- 删除硬编码（精确位置见 S1 细化计划）：s01b prompt 内 4 个枚举列表；s02a `CATEGORY_CODE`；s03 `flat_builder.py` 的 `CATEGORY_EN/STAGE_PRIORITY/STAGE_EN/FINANCING_MODEL_EN/INVOLVEMENT_TYPE_EN/NO_CAPACITY_CATS`；s04 `types/index.ts` 的 `CATEGORIES/STAGES/STAGE_COLORS`、`DataContext.tsx`/`FilterBar.tsx` 的 `HIDDEN_CATEGORY_KEYS`、`capacityPolicy.ts` 的 `NO_CAPACITY_CATEGORIES`。
- 清死代码：`DataContext.tsx` 的 `EXCLUDED_CATEGORIES` 里 `thermal/oil/gas` 英文键比中文（永不命中）。

**验证**：`npm run build` 通过；新增一致性测试断言"各代码模块枚举集合 == enums_mapping.json"；抽查前端类别/阶段下拉显示正确。

---

### S2 · 数值化 schema + 储能字段 + 迁移（中风险，地基）

**改动文件：**

- s01b `project_extractor_tool.py`：入库前 `_parse_number()`；`extracted_projects` 加 REAL 列 + `*_raw` 文本列 + `storage_power_mw` / `storage_energy_mwh` / `capacity_source`。
- s02a `duplicate_merger_tool.py`：对应列改 REAL（+ `*_raw`），新增 storage / capacity_source 列；`_parse_capacity` 改为读 REAL。
- s03 `flat_builder.py`：读 REAL 列；储能字段进 projects_flat。
- 新增 `scripts/migrate_v2_numeric.py`（迁移脚本，范本 = `clean_historical_data.py`）：副本上把 TEXT 数值清洗为 REAL、storage 尽力正则提取、历史 capacity_source='legacy'。
- **并入 S2 的脏枚举清洗（来自 S1 调研发现）**：对 s02a 核心库 `bri_re_database.db`——
  - 用映射表（复用 `clean_historical_data.py` 的 CATEGORY_MAP/STAGE_MAP/INVOLVEMENT_MAP 思路）把变体值归一到 enums_mapping.json 规范集（垃圾发电→生物质、抽水蓄能→储能、融资关闭→签约、获批/规划→拟开发、PPP/BOT→融资模式 等）。
  - 对 ≈25 个非可再生能源项目（石油/油气/矿产/煤炭/房建/工业园区/农业灌溉 等）打 `excluded` 标记（或移入 non_project_records），不进 s03 导出。
  - 清洗后 s02a 各枚举列的取值必须 ⊆ enums_mapping.json 规范集（断言验证）。

**验证**：副本迁移后抽样比对 N 条原文 vs 解析值；统计数值"解析失败（变 null）"占比并人工抽查；确认 `*_raw` 完整保留原文；**断言 s02a 各枚举列已 ⊆ 规范集、非 RE 项目已排除**。

---

### S3 · 编号 display_order + 排序 bug（低风险）

**改动文件：**

- s02a `duplicate_merger_tool.py`：`_gen_canonical_id` 的 `ORDER BY id DESC` 改为按数字段排序（`CAST(substr(...) AS INTEGER)`），修 >9999/99 进位错乱；**canonical_id 保持稳定不重排**。
- `canonical_projects` 新增 `display_order`（按该项目最早 event 日期升序），由迁移脚本一次性回填，入库逻辑增量维护。
- s03 `flat_builder.py` / s04 列表与图表：展示排序改用 `display_order`。

**验证**：单测排序函数（含 >9999 边界）；前端列表"老项目在前"；新增一个测试项目确认编号稳定、display_order 正确插位。

---

### S4 · 事件级时间建模（中风险，v2 核心价值）

**为什么是核心**：直接修你最在意的"2024 签约项目被算成 2026、早期项目消失"。

**改动文件：**

- s03 `stats_builder.py`：新增 `stats_timeline_events`（维度 `year × month × category × stage`，**一个项目在它经历的每个阶段-年份各计一次**）；保留旧 `stats_timeline`（终态分布）但趋势图不再用它。
- s03 `json_exporter.py`：导出新表对应 JSON。
- s04 `DataContext.tsx` / 趋势图组件：趋势改用事件级数据，分 tab（新签约/新开工/新投运）；移除 project count；单项目详情展示完整 signed/start/completion 时间点（精确到月）。

**验证**：浏览器实测——确认 2024 年的"签约"里程碑出现在 2024 而非 2026；早期年份不再被掏空；tab 切换正确；project count 已移除。

---

### S5 · 去重分组 + 地点归一化（**v2.0 大幅缩减**）

> **范围调整（2026-05-24 与用户确认）**：因 v2.1 将大幅重构各 Agent 结构（提取多 agent 化 + 验证/复核结构重设计），S5 的去重改造与地点归一化与 v2.1 的 agent 重构是同一摊事、且最可能被覆盖；其数据操作高风险不可逆且费 LLM。故：
> - **v2.0 只做 S5(d)**：修 `entity_normalizer._update_db` 母公司别名误改 subsidiary_name 的 bug（隔离、零 LLM、零主库变更，现存数据损坏 bug 应即修）。详见 `sessions/S5_dedup_location.md` Task 1。
> - **(a) 分组去 stage、(b) 地点/容量锚点、(c) 地点归一化模块、存量重去重 → 全部推迟到 v2.1**，与 agent 重构一起设计实现（见 §附录 v2.1 锚点）。

**v2.0 S5(d) 验证**：单测覆盖"implementing 映射不改 subsidiary"；entity_normalizer 仍可正常跑。

---

## 四、CLAUDE.md 提案（v2 期间生效）

> 拟在 `.claude/CLAUDE.md` 现有 3 条基础上**追加**以下两部分。措辞最终版会在你批准后写入。

**(A) 编程规则（精简自 coding_rules_draft）**

1. 禁止 `except: pass` / 裸 except；捕获须指定类型 + `logging.error` 记录上下文。唯一例外：已知无害边界（如列已存在），且加注释。
2. 运行时输出统一 `logging` 并落文件（时间戳/模块/级别）；`print` 仅限脚本顶层人机提示。
3. 批量写库必须事务包裹，禁止循环内逐条 commit；>50 条用 `executemany`。
4. 对外函数入口参数用 Pydantic 校验（日期格式、数值范围、路径存在），非法即抛。
5. HTTP 响应必须查 status_code（404 跳过 / 429 等待重试 / 403 终止 / 5xx 指数退避）。
6. 外部数据（JSON/API）进业务前用 zod 等做运行时结构校验。
7. 多线程共享可变变量必须加锁（读写都在 `with lock:` 内）。
8. 流水线各阶段入口验证上一阶段输出（库存在、关键表非空、记录数合理），无效即抛。
9. API Key/凭据禁止入 git；`.env` 必在 .gitignore。

**(B) v2 专属约束**

- 枚举/类别/阶段/参与方式映射**只能**改 `enums_mapping.json`，禁止在任何 .py/.ts 里硬编码枚举。
- 改 schema 必须**同步 s01b→s02a→s03 三处** + 提供迁移脚本，且**先在 .db 副本验证、动主库前 .bak 备份**。
- `canonical_id` 是稳定主键，**禁止重排**；"老项目在前"用 `display_order` 实现。
- 数值字段一律 REAL + 保留 `*_raw` 原文列；禁止用 TEXT 存数值。
- 每个 session 结束同步更新 `docs/status` 与 `docs/changes`。
- v2 范围外的事（多 agent/后端/多领域/自动运行/访问统计）一律不碰，需要时停下确认。

---

## 五、执行流程

1. **你审本文件** → 改 scope / session 顺序 / 迁移细节。
2. 批准后：建 v2 worktree（用原生 EnterWorktree），写入更新版 CLAUDE.md，跑基线。
3. 按 S1→S5 逐 session 执行；每个 session 开工时先生成该 session 的逐步 TDD 细化计划于 `docs/rules_and_plans/sessions/`。
4. 每 session 完成 → 验证 → 更新 status/changes → 你确认后进下一个。

---

## 附：v2.1 / v3 锚点（本次不做，仅留记录）

- **v2.1**：A.0 提取多 agent 化；A.1-A.5 分类规则补全（垃圾发电=生物质、数据中心须RE供电、年发电量反推容量、设备主/次细分）；金标准抽样评测集；E.2 未点名中企 `chinese_unnamed` 复核流。
  - **（从 S5 推迟而来）去重与同一性识别 + 地点归一化**：候选分组去 stage、地点/容量硬锚点、地点音译归一化模块（B 类）、是否对存量重去重；与 agent 重构一起设计（计划草稿见 `sessions/S5_dedup_location.md` Task 2-5，可作 v2.1 起点）。
- **v3**：F.1 多领域 domain 字段 + 领域插件式 pipeline；F.2 前后端分离 + FastAPI；定时自动运行（GitHub Actions cron）；访问量/IP 分析；E.3 外部信源换源（证券/官方公告）。
