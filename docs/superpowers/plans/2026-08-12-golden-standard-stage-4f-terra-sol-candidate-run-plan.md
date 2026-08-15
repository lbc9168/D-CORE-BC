# Golden Standard Studio Stage 4F：40 篇 Terra → Sol 候选生成计划

日期：2026-08-12

## 目标

对 `stage-4e-40-v1` 的全部 40 篇执行与 Pilot 相同的内置 agent 流程：`gpt-5.6-terra` Provider 按 production S01B-S1→S2→逐项目 S34→逐项目 S5 生成候选，随后 `gpt-5.6-sol` Reviewer 读取相同固定原文和完整 Provider artifact 做独立逐字段审校。网页向 development 和 challenge 同时展示候选；候选只辅助人工复核，不是 Gold。

## 运行合同

- 运行范围只来自不可变 cohort manifest，不把 40 个 ID 重新硬编码进执行器。
- 每篇保存固定文章、完整 rendered Prompt、builder hash、阶段原始输出、Provider candidate、Reviewer Prompt、Reviewer candidate 和父候选关系。
- Provider/Reviewer 均不得读取数据库候选、Pipeline、人工草稿或 Gold；网页继续不调用模型。
- 每个候选校验 article/model/role/hash/status/project schema、逐字 evidence 和 Reviewer parent link；通过后才进入 `data/candidates`。
- 已通过校验的 artifact 断点续跑时跳过；非法或半完成文件不得被网页导入。
- development/challenge 都显示 Terra/Sol。challenge 仅表示未来回归中的固定困难集，不是人工无候选实验，也不是 blind。

## 人工 Gold 语义修正

- `not_mentioned/not_applicable/uncertain` 不要求原文证据；只有事实层 `present` 要求连续逐字证据。
- Provider 的 `explicit/extracted/derived_from_explicit` 统一按实际值映射为 present；`unknown` 映射为 uncertain；空值或字面 `not_mentioned` 映射为 not_mentioned。
- 新候选到达后，只更新 revision=1、尚无人工 field action 的自动初始草稿；任何已编辑或已确认草稿绝不覆盖。

## 验收

1. 40 个 Provider 和 40 个 Reviewer artifact 全部通过合同校验。
2. 队列显示每篇 `provider → reviewer`；development 和 challenge 页面都显示 Terra/Sol。
3. 未提及字段可在无 evidence 时确认；present 事实仍必须有原文证据。
4. 三份上游 SQLite、既有 Pilot candidate/release 和 Gold 均保持 hash 不变。
5. 更新 README、`docs/status/external-tools.md` 与 `docs/changes/2026-W33.md`，不创建提交。
