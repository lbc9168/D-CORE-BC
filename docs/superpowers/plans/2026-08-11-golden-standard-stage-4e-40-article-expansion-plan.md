# Golden Standard Studio Stage 4E：40 篇分层扩展实施计划

日期：2026-08-11  
工作区：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\database-refactor-expansion-aa6198`  
分支：`claude/database-refactor-expansion-aa6198`

## 1. 目标

在不可变 `pilot-v0.1.1` 的 10 篇 development 样本之外，新增一个独立的 40 篇 expansion cohort，使 Studio 累计可复核规模达到 50 篇。Stage 4E 只完成分层选样、不可变清单、队列接入和可复现性验证；不替人工批量确认，不发布尚未复核的 Gold，也不开始最终 200–300 篇或 blind 系统建设。

## 2. 固定划分

- 新增 40 篇：development 24 篇、challenge 16 篇、blind 0 篇。
- 原 Pilot 10 篇继续作为 development 起点，不重复计入本 40 篇，不得转入 challenge/blind。
- challenge 是已知文章 ID 的压力测试集，不是 blind，也不得宣传为无泄漏最终排名。
- Stage 4E 完成后累计为 development 34、challenge 16、blind 0，共 50 篇。

## 3. 主分层配额

每篇只有一个 `primary_stratum`，同时可带多个层级标签；配额如下：

| 主分层 | 篇数 | development | challenge | 主要目的 |
|---|---:|---:|---:|---|
| `core_balanced` | 10 | 7 | 3 | 单项目、语言、类别、国家和阶段基本覆盖 |
| `s01b_complex` | 8 | 4 | 4 | 多项目、清单/回顾、组合与背景项目边界 |
| `s02a_family` | 8 | 4 | 4 | 四个两文章 canonical/近重复家族，建立跨文章裁定入口 |
| `s02b_s03_normalization` | 8 | 6 | 2 | 实体/地点标准化与 admin1 支持边界 |
| `boundary_gate` | 6 | 3 | 3 | 无中方、服务/设备/制造业和项目范围边界 |
| **合计** | **40** | **24** | **16** |  |

这些 strata 由历史数据库信号用于选样，不是人工真值。`historical_status_hint`、既有 category、canonical ID 和 location normalization 只能帮助排队与展示，人工确认时可全部推翻。

## 4. 固定样本

### core_balanced（10）

`23627, 25106, 16899, 20872, 192, 260, 281, 166, 353, 87`

### s01b_complex（8）

`14313, 9081, 4604, 3674, 13911, 12153, 13744, 16105`

### s02a_family（8）

- `UZB-PV-GIZHAK-4E`：`232, 26667`
- `IDN-HY-JATIGEDE-4E`：`4930, 4952`
- `GBR-ES-MENDI-4E`：`16817, 17408`
- `BIH-BOUNDARY-U-4E`：`526, 532`（近重复正文且历史 canonical 关联存在冲突，必须人工裁定）

### s02b_s03_normalization（8）

`32, 75, 137, 155, 432, 465, 483, 635`

### boundary_gate（6）

`1380, 3215, 12865, 5093, 2756, 27434`

## 5. 不可变 cohort 合同

输出目录：

`external-tools/gold-standard-studio/data/cohorts/stage-4e-40-v1/`

包含：

- `articles.jsonl`：40 篇的 article ID、title、url、publish date、正文 SHA-256、split、主分层、风险/难度、层级标签、family ID 与历史状态提示；
- `manifest.json`：schema/cohort version、配额统计、Pilot 排除集合、selection policy、三个只读源数据库 hash、`articles.jsonl` hash 和实现代码 hash。

生成规则：

1. 精确固定 40 个互异 ID，且与十篇 Pilot 完全不相交。
2. 从只读 `raw_news.db` 读取正文并计算 SHA-256；不存在、正文为空或 hash 漂移时 fail closed。
3. S02A family 必须各有且仅有两篇，并且一个 family 不得跨 development/challenge；本阶段为训练与压力测试可各自成族，未来 blind 则禁止 family 跨 split。
4. 统计必须严格等于 24/16、10/8/8/8/6 和 blind=0。
5. 先写同级临时目录，重新读取和校验后原子改名；已有同版本拒绝覆盖。
6. artifact 不写运行时间戳，使同一源数据库快照得到相同字节。

## 6. Studio 接入

- 保留 `pilot_manifest.py` 和既有 Pilot 发布语义，`pilot-v0.1.1` 字节不得改变。
- 新增 Stage 4E manifest 模块和通用 review-cohort 视图；`/queue` 展示累计 50 篇，并按 Pilot、Stage 4E development、Stage 4E challenge 分组统计。
- 复核页复用现有草稿宽松/确认严格、候选/数据库对照、reopen 和 navigation；Stage 4E 无候选时使用历史抽取作为可编辑起点，并醒目标为非 Gold。
- 没有历史项目的 boundary 样本只建立空草稿，不自动判成负样本；人工必须选择文章状态并填写理由或项目。
- 本阶段不调用模型，不生成 Terra/Sol 候选，不改变 production Prompt、taxonomy、registry、location rules 或上游数据库。

> 2026-08-12 补充：上述停止点只描述 cohort 建设阶段。后续 Stage 4F 已获授权，对固定 40 篇执行 Terra→Sol 候选生成，并由同一网页人工复核；development/challenge 都显示冻结候选，challenge 不再承担“首次判断隐藏候选”的实验职责。详见 `2026-08-12-golden-standard-stage-4f-terra-sol-candidate-run-plan.md`。

## 7. 验证

1. 单元测试校验样本唯一性、Pilot 排除、split/stratum 配额、family 完整性和 blind=0。
2. 在临时目录生成 cohort，校验原子发布、artifact hash、同版本不可覆盖和 JSONL 可重读。
3. 对真实数据生成 `stage-4e-40-v1`，逐篇核验正文 hash 与 title；40 篇均可由 `load_article()` 只读加载。
4. 队列显示总数 50、development 34、challenge 16，并可打开至少一篇无候选 boundary、一个 S02A family 和一个 normalization 样本。
5. 测试前后 raw、extraction、main SQLite SHA-256 不变；`pilot-v0.1.0`、`pilot-v0.1.1` 和冻结候选 hash 不变。
6. 只运行 Studio 定向测试和 `git diff --check`，不运行全仓库测试。

## 8. 完成条件与停止点

- Master Plan 与本计划均已保存；
- 40 篇清单已按不可变合同生成并接入复核队列；
- 新样本未被伪装为已确认 Gold，blind 仍为空；
- README、`docs/status/external-tools.md` 和 `docs/changes/2026-W33.md` 已同步；
- 完成后停在 40 篇待人工复核/候选生成状态，不继续构建 200–300 篇系统，不创建 Git 提交。
