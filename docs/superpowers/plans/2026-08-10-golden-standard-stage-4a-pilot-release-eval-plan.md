# Golden Standard Studio 阶段 4A：Pilot Gold 发布与分层评估计划

## 1. 目标

把已经完成人工复核的十篇 Pilot 文章冻结为首个内部 Golden Standard 版本 `pilot-v0.1.0`，并建立同一份人工真值向 S01B Project Extractor、S02A Duplicate Merger、S02B Entity Normalizer 和端到端链路投影的最小评估闭环。

本阶段不扩大样本，不重新调用模型，不修改任何上游 SQLite 数据库，也不把当前数据库值自动当作人工真值。

## 2. 当前发布前审计

- Pilot 清单固定为现有十篇真实文章。
- 本地复核库目前有 9 篇处于 `confirmed`，文章 1533 仍是未确认草稿。
- 文章 9027 的确认早于分层字段加入，其已确认 payload 缺少 `normalized_location` 和 `normalized_entities`。
- 因此可以完成发布器、投影器、基线评估器和测试，但真实 `pilot-v0.1.0` 必须在 1533 完成确认、9027 重新打开并补齐分层字段后才能发布。
- 发布器必须 fail closed；不得替用户补写 review action、不得伪造确认、不得跳过缺失字段。

## 3. 发布合同

发布目录为：

`external-tools/gold-standard-studio/data/releases/pilot-v0.1.0/`

一次成功发布包含：

- `manifest.json`：版本、schema、文章清单、源数据 hash、候选 provenance 和所有产物 hash；
- `rich-gold.jsonl`：保留文章状态、项目、字段 envelope、证据、人工动作和来源 hash 的完整 Gold；
- `projections/s01b.jsonl`：S01B 原始事实层投影；
- `projections/s01b-eval.jsonl`：兼容现有 `eval/gold_loader.py` 的 S01B 投影；
- `projections/s02a.jsonl`：项目拆分、合并与文章内项目边界投影；
- `projections/s02b.jsonl`：地点与公司实体归一化投影；
- `projections/end-to-end.jsonl`：供完整链路回归使用的最终值投影；
- `baseline/stored-pipeline-baseline.json`：当前数据库及已存 Pipeline 结果相对 Gold 的分层基线报告。

发布规则：

1. 十篇文章必须全部存在并已确认。
2. 每篇文章必须重新通过当前严格确认校验，包括新增分层字段和证据校验。
3. 复核时使用的文章正文 hash 必须与当前只读原文一致。
4. 版本目录一旦存在，不允许覆盖或追加；修订必须使用新版本号。
5. 所有文件先写到同级临时目录，重新读取、校验并计算 hash 后再原子改名。
6. 任一检查失败时不生成半成品版本目录，并返回逐篇、逐字段阻塞原因。

## 4. 分层语义

### S01B Project Extractor

评估新闻中直接可抽取的文章状态、项目边界、项目名称、类别、能源、储能、容量、阶段、国家、原始地点、投资、融资、日期、原始公司名称和参与角色。母公司和规范名称不属于这一层的模型错误。

### S02A Duplicate Merger

评估项目是否应拆分、合并，以及数据库项目集合能否对应 Gold 项目集合。首版使用文章内项目名称匹配和项目级 precision/recall/F1，保留后续接入跨文章 canonical 聚类评估的空间。

### S02B Entity Normalizer

评估 `normalized_location` 与 `normalized_entities`，包括文中名称到 canonical entity、母公司和角色的映射。它与 S01B 的原始实体抽取分开计分。

### End-to-end

评估当前正式数据库快照相对人工最终值的项目覆盖和字段准确度，用于暴露上游抽取、合并或归一化任何一层造成的最终差异。

## 5. 当前 Pipeline 基线的定义

本阶段不重新运行模型。基线明确标记为 `stored_pipeline_snapshot`：

- S01B 使用现有 `projects.db` 中与文章关联的历史抽取结果；
- S02A/S02B/端到端使用当前只读 `bri_re_database.db` 中按来源链接关联的项目、事件和实体；
- 报告记录三个源 SQLite 文件的 SHA-256，保证结果可复现；
- 文章状态只能从已存结果判断“有效/非有效”，无法恢复当时 S01B 对 `irrelevant` 与 `no_chinese_participation` 的完整分类时，报告必须明确标记该指标的限制。

## 6. 最小实现

新增一个无网络、只读上游数据的发布模块，提供以下命令：

- `check`：输出真实 Pilot 发布就绪报告；
- `publish --version pilot-v0.1.0`：完成严格预检、生成投影、计算基线并原子发布；
- 发布接口接受可注入的数据加载器，便于用临时复核库做最小测试，而不修改真实数据库。

同步补充 Studio README、项目状态和当周修改日志。不会建设远程部署、权限、复杂迁移、200 篇抽样或新的模型执行框架。

## 7. 验证

最小测试覆盖：

1. 未确认或缺字段的文章阻止发布，且目标目录不存在；
2. 完整确认样本可以发布全部文件；
3. 同一版本二次发布被拒绝，原版本字节不变；
4. manifest 中的文件 hash 与磁盘一致；
5. `s01b-eval.jsonl` 可被现有 Gold loader 读取；
6. 基线报告可以对完全一致的测试样本给出满分；
7. 对真实复核库执行 `check`，准确报告 1533 与 9027 的当前阻塞；
8. 操作前后上游 raw、extraction 和 main 数据库 SHA-256 完全不变。

## 8. 完成条件

- 发布、投影和基线代码及针对性测试完成；
- 真实发布预检结果清楚、可操作；
- 若十篇全部满足合同，则生成 `pilot-v0.1.0`；若仍有人工真值缺口，则不生成伪版本，并把剩余操作精确到文章；
- 文档状态与修改日志同步完成；
- 不创建 Git 提交。
