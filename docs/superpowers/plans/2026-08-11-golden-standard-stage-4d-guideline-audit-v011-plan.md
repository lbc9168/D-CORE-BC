# Golden Standard Studio 阶段 4D：标注口径审计与 `pilot-v0.1.1` 发布计划

## 1. 目标

把用户于 2026-08-11 明确确认的五条 Gold 标注口径固化为正式合同，定向审计现有 Pilot 中受影响的字段，并在不覆盖 `pilot-v0.1.0` 的前提下发布修订版 `pilot-v0.1.1`。随后使用完全相同的冻结 Terra/Sol 候选和类型化评估器重跑实验，区分 Gold 修订与模型变化。

本阶段不修改 production Prompt、模型、taxonomy policy、实体登记册、地点规则、候选 artifact 或上游数据库，也不调用模型。

## 2. 新增标注口径

1. 股权收购方不自动等于项目开发商；没有项目开发的明确证据时，`project_developer` 标为未提及。
2. 公司或区域层面的综合能力不能自动下放为每个项目的实体角色；每个项目的角色必须由该项目的参建、EPC、设备供应等具体证据支持。
3. 候选地点不得进入项目名称；`location` 只保留原文可确认的地点层级。具体场址只是候选时，可以保留更高层级的确定地点。
4. 股权收购不等于股权融资；`financing_mode` 必须有明确融资行为证据。
5. “参与建设/参建”足以支持 `project_stage=在建`，但如果原文同时表明已完工或投产，应采用更晚阶段。

## 3. 审计范围

优先核对冻结实验 `frozen-terra-sol-v1` 中 Reviewer 新增的 10 个 mismatch，重点文章为 `1533` 和 `1586`。每项必须读取固定文章原文、当前 Gold、Terra 与 Sol envelope；不能只凭 Reviewer note 自动改 Gold。

只有能由上述五条规则直接裁决的字段才修改。证据不足或仍有歧义的项目保持原确认值，并在审计报告中列为未修改项。

## 4. 草稿与发布语义

- 修改本地 `data/review.db` 前记录原 revision 和字段值；
- 使用 Studio 自身草稿/确认合同更新，保留字段 evidence、review action 和 revision；
- 修订后的文章必须重新通过严格确认校验；
- `pilot-v0.1.0` 保持字节不变；
- 发布新目录 `data/releases/pilot-v0.1.1`，原子生成且拒绝覆盖；
- 新 release manifest、rich Gold 和全部投影重新计算 SHA-256。

## 5. 实验重跑

使用相同的 20 个冻结候选、相同模型标识和同一类型化比较语义，生成：

`data/experiments/pilot-v0.1.1/frozen-terra-sol-v1/`

报告必须记录 Gold 版本变化，候选 hash 与 `pilot-v0.1.0` 实验一致；任何指标变化只能归因于 Gold 修订，而不是新模型采样。

## 6. 验证

- 五条规则进入长期维护的标注规则文档；
- 审计前后差异只包含已批准字段；
- `pilot-v0.1.0` 所有 artifact hash 不变；
- `pilot-v0.1.1` 发布校验、artifact hash 与 legacy loader 兼容通过；
- v0.1.1 冻结实验可重读且同 experiment id 拒绝覆盖；
- 20 个候选、Prompt provenance 和三份上游数据库 hash 不变；
- 只运行受影响的 Studio 定向测试，不运行全量 pytest。

## 7. 完成条件

- 用户确认的口径已成为可执行标注合同；
- 受影响 Gold 已按原文完成定向修订并发布 `pilot-v0.1.1`；
- 新旧实验差异可解释；
- docs/status 与 docs/changes 已同步；
- 不创建提交。
