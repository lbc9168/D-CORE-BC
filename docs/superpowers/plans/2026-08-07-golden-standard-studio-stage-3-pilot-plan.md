# Golden Standard Studio 阶段 3 Pilot 实施计划

日期：2026-08-07  
工作区：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\database-refactor-expansion-aa6198`  
分支：`claude/database-refactor-expansion-aa6198`

## 1. 已确认边界

- 阶段 3 总计约 10 篇真实文章，包含阶段 2 已验收的 `raw_news.id=9027`。
- 实现固定复核队列、确定性风险/难度顺序、进度状态、多文章导航及完整复核闭环。
- 不记录人工复核耗时、停留时间、操作速度或行为分析。
- 用户直接使用 Pilot；发现交互卡点后再定向修改。
- 不做 200–300 篇抽样、Gold 发布、eval 投影、权限、远程部署或阶段 4 功能。
- 不修改任何上游数据库、正式分类、canonical/event ID 或正式导出。
- 不恢复或复制 Git 历史中已删除的 Studio 实现，不创建提交。

## 2. 固定 Pilot 样本

| 顺序语义 | raw_news.id | 类型 | 难度/风险 | 选择理由 |
|---|---:|---|---|---|
| 已验收基线 | 9027 | 储能双项目 | 中 | 两个独立 150MW/300MWh 项目，含跨来源数据库污染风险 |
| 高风险 | 1533 | 复杂多项目与历史项目 | 很高 | 主收购项目夹杂历史项目，检验拆分和过度提取 |
| 高风险 | 1586 | 新能源制造业 | 高 | 工厂年产能不能误作发电或储能容量 |
| 高风险负样本 | 18491 | 无中方参与火电 | 高 | 电力项目明确但正文参与方为日韩企业 |
| 高风险 | 14595 | 光柴储微网 | 高 | 26 岛打包项目、光伏与储能容量语义 |
| 高风险负样本 | 27082 | 无中方参与光伏融资 | 高 | 明确电力项目但不得误判 irrelevant |
| 中风险负样本 | 10069 | 无中方参与光伏融资 | 中 | 金融关闭事实丰富，但正文未出现中企 |
| 中风险 | 13649 | 英文风电投资 | 中 | 英文原文、投资角色和项目阶段 |
| 中风险负样本 | 27309 | 非能源矿业 | 中 | 有中企但主题是锂矿，必须 irrelevant |
| 低风险 | 17413 | 输变电 | 低 | 单项目、类别边界清晰 |

队列由代码中的固定 manifest 给出，不在 Pilot 内建设随机抽样器。风险排序是可解释的静态产品顺序，不是对人工速度的评分。

曾考虑 `raw_news.id=12972` 作为绿氨负样本，但真实正文同时回顾多个具名电力项目，不能提供干净的文章级负样本，因此不纳入本 Pilot。

## 3. 可复现 Terra → Sol 候选链

命名约定：S01B 是顶层 Project Extractor；下文的 S01B-S1、S01B-S2、S01B-S34、S01B-S5 都是 S01B 内部步骤，不是与 S02A/S02B 并列的顶层 pipeline 阶段。

每篇新增文章执行：

1. 从只读 `raw_news.db` 固定文章 `title/content/date/url/article_sha256`。
2. 代码直接调用当前 production builder 渲染 S01B-S1 与 S01B-S2，保存完整 Prompt、SHA-256 和 builder 源码 SHA-256。
3. Terra Provider 使用 `gpt-5.6-terra` 按 S01B-S1 → S01B-S2 执行；S01B-S1 为 `irrelevant/no_chinese_participation` 时按 production 行为短路。
4. 对有效文章，基于 Terra 的 S01B-S2 原始输出逐项目重新调用 production builder 渲染 S01B-S34；Terra 逐项目执行并保存原始输出。
5. 将同项目 S01B-S2+S01B-S34 结构化结果传给 production S01B-S5 builder，逐项目渲染 S01B-S5；Terra 执行并保存原始输出。
6. Provider artifact 保存全部阶段原始输出、最终规范化候选、文章 hash、模型、Prompt manifest 和候选 ID。
7. 代码将固定文章与完整 Provider artifact 渲染为独立 Reviewer Prompt，保存 Prompt/hash 和父候选 hash。
8. Sol Reviewer 使用 `gpt-5.6-sol` 逐字段核验，保存完整 reviewer artifact，并用 `parent_candidate_id` 指向 Terra 候选。

Subagent 不读取数据库候选、人工草稿或 Gold；Web 应用不调用模型，也不持有 API key。

## 4. 最小代码范围

- 固定 Pilot manifest 与通用只读文章加载。
- production Prompt pack 渲染器及 Studio-owned 运行 artifact。
- `/queue` 页面：风险顺序、候选就绪状态、草稿/确认进度。
- `/review/{article_id}` 泛化到固定 10 篇，保留阶段 2 双栏、草稿、刷新恢复、确认与重新打开语义。
- 上一篇/下一篇/返回队列导航。
- 只增加覆盖队列与多文章路径的定向测试。
- 同步 README、`docs/status/external-tools.md` 和 `docs/changes/2026-W32.md`。

## 5. 验收路径

1. 打开 `/queue`，看到 10 篇真实标题、类型、难度、候选链状态和本地进度。
2. 打开正常单项目、复杂多项目、`irrelevant` 与 `no_chinese` 各至少一篇。
3. 每篇正文、数据库值、Pipeline、Terra、Sol、policy 候选按实际可用数据呈现。
4. 从队列和上一篇/下一篇可稳定切换，文章之间草稿互不覆盖。
5. 任意字段编辑立即宽松保存；刷新恢复修改。
6. 负样本选择相应文章状态后可清空项目并确认。
7. 有效文章字段可独立确认，整篇确认才执行严格校验。
8. 确认后锁定；显式重新打开后可继续编辑和再次确认。
9. 保存失败明确说明原因且页面输入保留。
10. 用真实浏览器走完多文章路径；只运行定向测试。
11. 上游数据库 hash 不变；不创建提交；完成后停在阶段 3 等待用户验收。
