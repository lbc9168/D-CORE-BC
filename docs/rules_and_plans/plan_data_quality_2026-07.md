# 数据质量工程总计划（2026-07，dcore_v2.2）

> 源起：2026-07-04 全链路质量评估 + grill-me 六项共识。目标：提高"新闻→结构化数据"的准确率、消灭大错。
> 状态标记：✅完成 · 🔶部分完成 · ⬜未开始 · 💤搁置/不做

## 已敲定的共识（全部 ✅，作为后续工作的规则）

1. 收录边界：**具名中企 + 可识别单体项目**为硬条件（招标/中标同规则）；批量招标计划/国家规划/企业远期目标不进 canonical。
2. TX/MF 与电站不分库不分表：**s03 导出层分流** + project_events 加 5 个稀疏专用列。
3. Codex(GPT-5.5) 季度分拣：**分级信任 + 抽检**（不改数据的自动收，改数据的头 2-3 季度抽 10-20%）。
4. canonical 分裂不加流水线机制，只做 **campaign 附属 merge 脚本**。
5. 金标准由 Claude 起草 + 用户复核 Markdown 复核包。
6. 备份**里程碑保留制**；campaign 备份 WAL 安全 + 内容去重。
7. 实体处理**来源保真与实体解析分两步**（设计已认可，实施在阶段 4）。
8. 拟开发项目保留入库不展示（有意设计，不改）；外国业主出现在 developer 字段不算错误。

## 阶段总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| 评估 | 全链路评估 + 六项共识 | ✅ |
| 阶段 0 | 备份修复/清理 + eval 基线准备 | ✅ |
| 阶段 1 | 主库数值大错回填 | ✅（网页待 run_export 刷新） |
| 插入A | 工作区迁移到 worktree dcore_v2.2 | ✅ |
| 插入B | 金标准重建（复核包→gold→基线） | ✅ |
| 插入C | company_hierarchy 扩充核实 | ✅（数据资产就绪，未接线） |
| 阶段 2 | Codex 季度分拣管线 + 试点 | ⬜ |
| 阶段 3 | TX/MF 展示前专项 | ⬜ |
| 阶段 4 | s01b prompt 迭代（基线回归驱动） | ⬜（基线已就绪） |

## 已完成明细

### 评估与共识（2026-07-04）
- ✅ 全链路评估：展示口径 1485/4529；四类数值错误模式；漏合并 179 对（双边可见 46 对）；TX/MF 三待办；eval 弱项定位。
- ✅ grill-me 六问共识（见上）。

### 阶段 0（2026-07-04/05）
- ✅ 备份机制修复：`s02b/db_backup.py`（SQLite 原生 backup=WAL 安全 + sha1 去重），en_backfill/developer_normalizer 接入，18 测试绿。
- ✅ 存量备份清理：删 86 个 .bak 释放 899MB，保留 10 个里程碑。
- ✅ 金标准丢失确认（gitignore+worktree 删除）→ 改道重建（见插入B）。

### 阶段 1（2026-07-05，主库已落库）
- ✅ 按审计清单 `supporting_docs/numeric_audit_2026-07-04.csv`（124 行）回填 113 项：TX/MF capacity 全量置空 74、数值修正 27（千倍错/币种错译/丢"亿万"字）、SAU-ES 储能语义归位、荒谬值置 NULL 9、巴西特高压投资取总投资 3300M$。备份 `.bak.20260705_093224`，验证全过。
- 🔶 网页 JSON 未刷新——待下次 `run_export`（用户 gated；数据在主工作区）。

### 插入A：工作区迁移（2026-07-05）
- ✅ 全部文件改动 stash→worktree `.claude/worktrees/dcore_v2.2`（分支 `dcore_v2.2`，基于 c14428e）；main 恢复干净供本次发布用；会话已切入 worktree。
- ✅ 修复 Excel 锁导致 draft_review.csv 未迁移的问题。

### 插入B：金标准重建（2026-07-05）
- ✅ 分层抽样 55 篇（硬负例/招标/TX/MF/单位换算/一文多项目/普通/纯负例）→ 6 个 sonnet 代理标注 → `review_pack.md` + `draft_review.csv`（133 处分歧预填）。
- ✅ 用户复核（含 a21 华电股权投资方等修正）→ `eval/gold_set.jsonl`（55 篇/63 项目）→ **提交 git `7a9a028`**，.gitignore 已放行 gold。
- ✅ **eval 基线跑通**（report `eval/reports/20260705_195301.json`，混合配置 Gemini+DeepSeek，55 篇/39 计分/0 失败）：

| 指标 | 基线 | 对应阶段4改动 |
|---|---|---|
| weighted_total | **0.698** | （压力集，勿与旧97篇对比） |
| category | 0.953 | 强项 |
| status | 0.764 | ①收录边界 + ④few-shot |
| capacity_mw | 0.744 | ②换算移出 LLM |
| entity.name_f1 | **0.523** | ⑨解析分两步 + hierarchy |
| entity.role | 0.609 | ⑤S5 扩 involvement_type |
| project_set.f1 | 0.652 | ④一文多项目 few-shot |

### 插入C：company_hierarchy 扩充（2026-07-05）
- ✅ 主库提频次≥5 实体 276 → 剔 62 外国实体 → 214+7(补漏) 中国实体 9 批联网核实（每条附来源）→ 用户复核 CSV → 定稿 `company_hierarchy_draft_2026-07-05.json`：**新组 97（含华为/杭州锦江/特变电工）+ 挂现有 14**，冲突 0。
- ✅ 「中国能建国际集团」1800 事件归位能建；**「中国能源海外投资有限公司」121 次=电建海投名称讹变**（库内证据定案）。
- ✅ 覆盖率实测 **66.0%**；未命中高频几乎全是外国实体（正确透传），长尾 1.3 次/名——"透传+频次补录"策略验证成立。
- ⚠️ **刻意未接线**：现行 S4/entity_normalize 仍读旧表，草稿待阶段 4 ⑨ 启用。复核 CSV 220 行中 7 行 ★补漏 待用户扫一眼。

## 未完成明细

### 阶段 4：s01b prompt 迭代（⬜，下一 session 主战场——用户已指示执行 ②→⑨）

**执行纪律**：一次改一个变量 → 跑基线回归 → 记分数 → 保留或回滚。prompt 原则"有加有减"（②⑨是大减法，④是唯一明显加法）。
**目标线（够用就停）**：capacity≥0.90、status≥0.85、name_f1≥0.75、weighted≥0.80（压力集口径）；⑥⑦⑧看边际收益。
**回归命令**（worktree 根）：`PYTHONPATH=<worktree根> uv --project s01b_project_extractor run python -m eval.run_eval --gold eval/gold_set.jsonl --raw-news-db E:/Projects/BRI_RE_Database_v2/s01a_news_crawler/data/raw_news.db`——prompt 源码变更自动使缓存失效，报告自动与上一份对比。基线=`eval/reports/20260705_195301.json`（weighted 0.698/status 0.764/capacity 0.744/name_f1 0.523/role 0.609/set.f1 0.652）。改动后同时跑 s01b 测试套件。

执行顺序与每项规格：
- ⬜ **② capacity 换算移出 LLM**（第一刀，治 0.744）：S2 prompt 删整段换算规则，capacity 输出改"逐字抄录原文数字短语"；新增确定性 Python 解析器（万千瓦×10/千瓦÷1000/GW×1000/兆瓦=MW；产能类 GWh、GW/年、万吨/年与输送容量→null+留 details；解析失败→null+日志）。TX/MF 一律 null（与主库口径一致）。
- ⬜ **③ s02a 合并 prompt 补 Phase I/II 规则**（一期/二期≠同一项目）。注意：**s02a 不在 eval 覆盖内**，验证靠 s02a 测试套件+dry-run 抽查，非基线回归。
- ⬜ **④ S2 补 few-shot 硬例 3-5 个**（治 status/set.f1）：候选案例=缅甸批量招标→不拆条、一期二期→分条、"700万千瓦"→逐字抄、蓝氨/无中企→负例、**能建vs电建实体名照原文抄（勿混）**。素材直接取自 review_pack 难例。唯一加长项，回归看是否值回票价。
- ⬜ **⑤ S5 纠偏扩到 involvement_type**（治 role 0.609）：`apply_s5` 的 valid/field_map 加第三字段即可。
- ⬜ **⑥ S3 保留投资额原文字符串**（真原文进 raw 列，审计线索）。
- ⬜ **⑦ format_checks 失败打 needs_review 标记**（extracted_projects 若无该列则 ALTER TABLE 加，已知无害边界）。
- ⬜ **⑧ semantic_checks 双模型接入**（可选，做完②-⑦看剩余病灶再定）。
- ⬜ **⑨ 实体解析分两步**（压轴重构，治 name_f1 0.523）：(a) S4 prompt 删 17 家层级表注入，实体名逐字抄进 implementing_entity、subsidiary 留空；(b) 生成正式 `company_hierarchy_v2.json`=旧 19 组（转 v2 schema）+草稿 97+14；(c) `entity_normalize.py` 重写为纯查表解析器（精确名→别名→去尾缀归一化三层；**未命中透传+未命中日志，删除包含匹配**）；(d) `is_central_soe` 改由登记册推导；(e) **全库重解析回填 CLI**（不变量：subsidiary 非空⇒它是原文名；空⇒implementing 即原文名。dry-run→.bak→apply，主库 chinese_entities 存量归属含 1800 能建国际+121 电建海投一并修正）；(f) 回归看 name_f1 + s01b/s02b 测试。
- ❓ **① 收录边界规则**（治 status 0.764，grill-me 共识项）：用户口头范围"②到⑨"未点名①——**新 session 开工时先确认是否纳入**（推荐纳入，与④配合治 status）。

### 阶段 2：Codex 季度分拣管线（⬜）
- ⬜ 导出器（review 队列 JSON + 固定操作指南）
- ⬜ ingest CLI（schema 校验 + 证据引句必填 + dry-run/--apply + .bak + 抽检输出）
- ⬜ 试点任务一：179 对高相似 canonical 分拣（46 对双边可见优先）+ 一次性 merge 脚本
- ⬜ 试点任务二：范围复核队列（特拉维夫轻轨、Svevind/AREH 等无中企保留行、项目群记录 ~10 条）
- ⬜ 季度 runbook 固化

### 阶段 3：TX/MF 展示前专项（⬜，展示形式等用户决定）
- ⬜ project_events 加 5 稀疏列（TX：voltage_kv/line_length_km/transmission_capacity_mw；MF：production_capacity_value/unit）
- ⬜ 无中企 sweep（蓝氨类漏检）+ record_type 双轨归一（57 条 non_project MF vs canonical 178 条）
- ⬜ 从 capacity_details/summary 确定性回填专用列（阶段1置空时换算值已留备注）
- ⬜ s03 输出 tx_flat/mf_flat 独立 JSON；前端 tab 待用户定展示形式

### 日常收尾（🔶）
- ⬜ dcore_v2.2 未提交改动（备份修复代码、hierarchy 草稿、docs、审计 CSV 等——目前只有 gold 那笔 `7a9a028` 已提交）
- ⬜ 下次 run_export + 部署（带上阶段1修正；用户 gated）
- ⬜ campaign 循环调用产生备份的调用方（一次运行备份一次）已由内容去重兜底，无需再改

## 💤 搁置/明确不做
- 物理分库/分表（TX/MF）——已否决，走导出层分流
- 流水线 canonical 自动愈合——已否决，走 Codex campaign
- en-backfill miss 长尾二轮 campaign——暂缓
- 旧 backlog P4——不做
- hierarchy 表外企业的自动"找妈妈"步骤——由 hierarchy 扩充 + 阶段4 解析器替代，季度增量补

## 推荐的下一步顺序
1. 阶段 4 轻改动第一刀：**capacity 换算移出 LLM** → 跑基线回归
2. 视分数再排：收录边界 / few-shot / S5 扩展（每项一回归）
3. 攒足轻改动收益后，啃 **实体解析分两步** 重构（含 hierarchy 接线 + 全库重解析）
4. 阶段 2 Codex 管线（可与阶段 4 并行，互不依赖）
5. 阶段 3 等你定 TX/MF 展示形式后排期
