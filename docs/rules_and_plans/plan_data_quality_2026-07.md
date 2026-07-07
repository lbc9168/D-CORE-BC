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
| 阶段 2 | Codex 季度分拣管线 + 试点 | ✅（2026-07-07，见 docs/changes/2026-W28a.md） |
| 阶段 3 | TX/MF 展示前专项 | ✅（2026-07-07 数据端+导出端完成；拍板②迁移口径待用户读双轨报告后定，见 docs/changes/2026-W28b.md） |
| 阶段 4 | s01b prompt 迭代（基线回归驱动） | ✅（2026-07-06：weighted 0.698→0.811） |

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

### 阶段 4：s01b prompt 迭代（✅ 2026-07-06 完成，详见 docs/changes/2026-W27k.md）
- ✅ ①收录边界 / ②换算移出 LLM（新 `capacity_parser.py`）/ ③Phase I/II 规则 / ⑤S5 扩 involvement_type / ⑥S3 留原文 / ⑦needs_review 标记 / ⑨实体解析分两步（`company_hierarchy_v2.json` 116 组接线 + `entity_reparse_maindb` 主库回填，能建国际 1697 行归位、soe 终值 3801）
- ❌ ④few-shot：试后回归变差，**回滚**（压缩规则已够）；💤 ⑧semantic_checks：有据跳过（病灶已消）
- **成绩**：weighted 0.698→**0.811**、capacity 0.744→0.878、name_f1 0.523→**0.88**、status/role 达线
- **遗留输入**：实体解析未命中长尾 `supporting_docs/entity_reparse_misses_20260706_023835.csv`（Top50 待补录）→ 移交阶段 2 T3

## 未完成明细

### 阶段 2：Codex 季度分拣管线（✅ 2026-07-07 完成并跑完 2026Q3 整个正式季度闭环；工具在 codex_review/，运行手册见 docs/rules_and_plans/codex_quarterly_runbook.md（quarterly 一步一命令），交付与终局明细见 docs/changes/2026-W28a.md。拍板落定：首季 2026Q3 / loser 保留+merged_into / 抽检 15%。验收全过且超额：Codex 正式交卷 1,732 verdict 0 拒收；T2 系统性错率 ~7% 经三轮对抗复核 668 全覆盖处置（救回 51/移出 617）；主库终态 excluded 988/未排除 3,541，registry 151 组，台账 1,785 条。季度循环遗留（非本阶段）：T3 uncertain 22 下轮重入队、T1 链式冲突 2 组人工、guide_T2 下季写入三条新判定知识、51 条救回项目实体回填专项）

**目的**：建立"系统导出复核队列 → Codex(GPT-5.5) 分拣 → 校验落库"的季度闭环，消化人工顾不过来的长尾复核。共识约束：分级信任+抽检（不改数据的 verdict 自动收；改数据的头 2-3 季度抽 10-20% 人工复核）；数据契约不绑供应商（队列 JSON 同样可喂 Claude/人工）。

**2.1 目录与数据契约**：新建根目录 `codex_review/`（非流水线独立工具，状态记 external-tools.md）。每季度一个工作目录 `codex_review/runs/<YYYYQn>/`，内含 `queue_<task>.json`（待分拣项，**自带全部判断上下文**——事件字段/摘要/信源，Codex 不需要访问数据库）、`guide_<task>.md`（固定指南：判定规则+verdict schema+证据引句必填）、`verdicts_<task>.json`（Codex 写回）、`ingest_report_<task>.md`。判定台账 `codex_review/ledger.json`（内容哈希 pair_key，幂等：判过的不重复入队，同 dedup_cache 思路）。

**2.2 三类任务（首期）**：
- **T1 merge_pairs 漏合并分拣**：导出器**现算**同国同类名称相似>0.85 的未排除 canonical 对（勿用 2026-07-04 静态 179 对——数据已变），双边可见的优先。verdict：`{pair_id, same_project, confidence, evidence_quote, survivor_id, reason}`。
- **T2 scope_review 范围复核**：固定清单起步——numeric_audit 保留行的 ~10 条范围复核标记（特拉维夫轻轨 ISR-HB-0001、Svevind、沙特阿美目标、菲拍卖、越南省规划群等）+ 无中企扫描（canonical 未排除但实体空/摘要明示无中企，含 MF 边界案：锂矿/电解铝/蓝氨）。verdict：`{project_id, action: keep|exclude|downgrade_non_project, reason, evidence_quote}`。**阶段 3 的"边界清理"由本任务承接，不另建轮子。**
- **T3 entity_misses 实体归属补录**：输入现成=阶段 4 遗留 `supporting_docs/entity_reparse_misses_20260706_023835.csv` Top50。verdict schema 复用 hierarchy verify_guide 六分类（new_group/alias/subsidiary/…/not_chinese/uncertain）+来源链接。

**2.3 导出器** `codex_review/export_queue.py --task T --quarter Q`：按任务类型生成 queue+guide；查台账去重；Pydantic 校验出口。

**2.4 ingest CLI** `codex_review/ingest_verdicts.py --task T --quarter Q [--apply]`：
- 校验：Pydantic schema、枚举合法、evidence_quote 非空、id 存在于队列；非法整条拒收进报告。
- 分级信任：不改数据的 verdict（not_same/keep）自动接受入台账；**改数据的**（merge/exclude/registry 补录）出 apply 清单 + 按 15% 随机抽样生成人工复核子清单（头 2-3 季度），用户确认后 `--apply`。
- apply 动作：**merge**=附属脚本（事件迁挂 survivor 并重编 `{canonical_id}-{NN}` 事件 id、chinese_entities.event_id 外键同步、loser 行保留但 `excluded=1, excluded_reason='merged_into:<survivor>'`）；**exclude**=置排除+reason；**registry 补录**=条目进 `build_hierarchy_v2.py` 输入 → 重生成 v2 JSON → 跑 `entity_reparse_maindb`（阶段 4 现成 CLI）。全部 dry-run→.bak（WAL 安全）→apply→幂等自检。
- ⚠️ 主库在**主工作区**，绝对路径访问；merge 脚本先在拷贝库上跑测试再碰真库。

**2.5 runbook** `docs/rules_and_plans/codex_quarterly_runbook.md`：季度五步=增量流水线跑完 → export 三任务 → 用户侧跑 Codex（`codex exec`，指南模板固化在 repo）→ ingest 校验+抽检 → apply+记 changes。

**验收**：三类任务各完成一轮真实试点；ingest 拒收非法输入有测试；merge 脚本在测试库验证外键一致性；台账幂等（重跑 export 不重复入队）。
**开工时用户拍板**：① 首季编号（建议 2026Q3）；② merge 后 loser 处理（推荐保留行+merged_into 标记，可追溯）；③ 抽检比例（推荐 15%）。

### 阶段 3：TX/MF 展示前专项（✅ 2026-07-07 数据端+导出端完成，subagent-driven 3.1→3.4 全过审。拍板落定：① MF 产能单位四档 GW/年、GWh/年、万吨/年、Unknown；③ 阶段 2 先行已兑现。交付：主库 project_events 5 稀疏列迁移+回填（未排除 TX 441 项目——电压 309/里程 99/输送容量 12；MF 162——产能值 66/单位 69 含 Unknown 3；misses 274 CSV 喂 T3/人工）、s01b txmf_spec_parser + txmf_backfill CLI、流水线常设 2B-5、s03 tx_flat/mf_flat 两表两 JSON（projects_flat 字节级零变化独立复证）、双轨对照报告 docs/analysis/mf_dualtrack_report_2026-07.md（重叠 8 对/未匹配 49/边界案 0 命中/推荐口径 B）。**遗留拍板②：record_type 迁移口径等用户读报告后定**；前端展示 tab 另行排期；真实 run_export+部署仍 gated（硬顺序 en_backfill→run_export→部署）。明细见 docs/changes/2026-W28b.md）

**目的**：让输变电（TX，463 项目/532 事件）与新能源制造业（MF，178/192）具备按正确单位展示的数据基础。共识：不分库不分表——主库加稀疏列 + s03 导出层分流；本阶段**只做数据端与导出端**，s04 前端 tab 等用户出展示设计后单独排期。

**3.1 稀疏专用列（schema，先行）**：`ALTER TABLE project_events` 加 5 列——TX：`voltage_kv REAL`、`line_length_km REAL`、`transmission_capacity_mw REAL`；MF：`production_capacity_value REAL`、`production_capacity_unit TEXT`（受控枚举进 enums_mapping.json 新 section：GW/年、GWh/年、万吨/年、Unknown…）。沿用 storage_power_mw 稀疏列先例；列已存在时静默跳过（已知无害边界，加注释）。
**s01b 不改**：增量数据的专用列由回填器（3.2）作为流水线常设小步骤补——S2 prompt 刚过阶段 4 回归，不再动；原文信息本就落在 capacity_mw_raw/details。

**3.2 存量+增量回填器** `txmf_spec_parser.py` + 回填 CLI：
- 从 `capacity_mw_raw + capacity_details`（必要时 summary）确定性解析：TX 的 `±?N千伏/kV→voltage_kv`、`N公里/km→line_length_km`、`输送/输电容量 N万千瓦|MW→transmission_capacity_mw`；MF 的 `N GW|GWh|万吨(/年)→value+unit`。**数字短语解析复用阶段 4 的 `capacity_parser.py`**（含 兆瓦时/吉瓦时 拦截），只加 TX/MF 语境层。
- 解析不了→null+misses CSV（喂阶段 2 T3 或人工）。CLI 老规矩：dry-run→.bak→apply→幂等自检。
- **现成测试用例**：阶段 1 审计 CSV 备注里的人工换算值（SAU-TX=7000MW、BRA-TX=5000MW、PAK-TX-0014=4000MW、TLS=3x40MVA 等）作为断言对拍。
- 接入增量流水线：`pipeline/flow.py` 在 s02b 之后加一步（阶段入口验证上一步输出，规则 #8）。

**3.3 边界与双轨（报告先行，不自动迁移）**：
- 无中企/非能源边界案（蓝氨 ARE-MF-0004、锂矿、电解铝等）→ **并入阶段 2 T2 队列**处理，本阶段不重复做；若阶段 3 先行，则用独立 sweep 报告替代（复用现有 sweep CLI 模式）。
- record_type 双轨归一：出对照报告——57 条 non_project_records(record_type=新能源制造业) vs canonical MF 178 条 vs 37 条同名重叠——给用户拍板迁移口径后再动（首期只报告）。
**3.4 s03 导出层分流**：flat_builder 增建 `tx_flat`、`mf_flat` 两表（基本信息+专用列+阶段/年份/实体，无 capacity_mw）；json_exporter 增出 `tx_flat.json`/`mf_flat.json`（双写 s03 output + s04 public/data）；**projects_flat 与现网行为零变化**（TX/MF 仍 hidden）。events_exporter 下载口径待前端定型再议。验收：s03 测试 + 行数对账（tx_flat=TX 未排除数等）。
- ⚠️ 部署硬顺序不变：en_backfill → run_export → 部署。

**开工时用户拍板**：① MF 产能单位枚举清单 ✅（2026-07-07 拍定四档：GW/年、GWh/年、万吨/年、Unknown）；② record_type 双轨迁移口径 ⬜（报告已出：docs/analysis/mf_dualtrack_report_2026-07.md，推荐口径 B，等用户拍板）；③ 与阶段 2 的先后 ✅（阶段 2 先行已兑现，T2 清掉了边界案）。

### 日常收尾（🔶）
- ⬜ **dcore_v2.2 共 34 commits 待用户定合并方式**（合并回 main 的时机与方式——本次发布后）
- ⬜ 下次 run_export + 部署（带上阶段 1 修正与阶段 4 主库回填；⚠️ 硬顺序 en_backfill → run_export → 部署；用户 gated）
- ⬜ entity_reparse misses Top50 长尾补录 → 阶段 2 T3 承接

## 💤 搁置/明确不做
- 物理分库/分表（TX/MF）——已否决，走导出层分流
- 流水线 canonical 自动愈合——已否决，走 Codex campaign
- en-backfill miss 长尾二轮 campaign——暂缓
- 旧 backlog P4——不做
- hierarchy 表外企业的自动"找妈妈"步骤——由 hierarchy 扩充 + 阶段4 解析器替代，季度增量补

## 推荐的下一步顺序（2026-07-06 更新）
1. **阶段 2 先行**（理由：T3 直接消化阶段 4 遗留的 misses Top50；T2 顺带清掉阶段 3 需要的边界案；建好的季度闭环长期复用）
2. **阶段 3 随后**（3.1 schema → 3.2 回填器 → 3.3 报告 → 3.4 导出分流；3.3 的范围判定引用阶段 2 T2 的 verdict）
3. 两阶段都完成后：run_export + 部署（硬顺序），dcore_v2.2 合并方式拍板
4. 前端 TX/MF 展示 tab：等用户出展示设计后单独排期
