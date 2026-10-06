# GPT-6 Luna Low 与 Gemini 2.5 Flash-Lite：Pilot-10 对照

日期：2026-10-05。只替换 S1/S2，S34/S5 保留 DeepSeek；development 诊断，未切换生产默认。

固定样本为 pilot-v0.1.1：10 篇、14 个项目。所有原文 hash 匹配；发布文件按 LF 还原后匹配旧 manifest，原 Gold 未修改。

**结论：Luna Low 有值得继续验证的质量改善，当前保留为 challenger，不直接切换生产默认。** 它在 S1 提前拦下两个无中企负例，补回一个同时开工的储能站，避免一处光伏功率误写成储能功率，并更准确识别三站的签约阶段。但旧 Gold 与当前口径存在显著漂移，项目名阈值也放大了分差；共同项目的主要旧字段没有普遍提升，速度未获改善。

| 指标 | Gemini 基线 | Luna Low |
|---|---:|---:|
| 文章状态准确率 | 0.7000 | 0.9000 |
| 项目 Precision | 0.1667 | 0.5000 |
| 项目 Recall | 0.1429 | 0.3571 |
| 项目 F1 | 0.1538 | 0.4167 |
| 项目匹配/预测/Gold | 2/12/14 | 5/10/14 |
| 底层请求 | 45 | 40 |
| 传输失败请求 | 0 | 2 |
| SDK 请求耗时（秒，含失败） | 411.4 | 620.3 |
| 按已知 usage 的保守费用估算（美元） | 0.13653 | 0.11813 |
| 含未知 usage 请求预留的预算核算（美元） | 0.13653 | 0.15650 |

项目集合指标计入所有文章，包括负例误进提取和技术失败；对象是未过滤 publication_status 的 S01B 原始候选，不代表主库或网页误收。旧 evaluator 的分母与 status 门控结果另保留在 summary.json。

| 文章 | Gold 状态/项目数 | 基线状态/项目数 | Luna 状态/项目数 |
|---|---|---|---|
| 9027 | valid/2 | valid/1 | valid/2 |
| 1533 | valid/8 | valid/1 | valid/1 |
| 1586 | valid/1 | valid/1 | valid/1 |
| 14595 | valid/1 | valid/1 | valid/1 |
| 18491 | no_chinese/0 | valid/2 | no_chinese/0 |
| 27082 | no_chinese/0 | valid/1 | no_chinese/0 |
| 10069 | no_chinese/0 | no_chinese/0 | no_chinese/0 |
| 13649 | valid/1 | valid/1 | valid/1 |
| 27309 | irrelevant/0 | valid/1 | valid/1 |
| 17413 | valid/1 | valid/3 | valid/3 |

源文复核的核心发现：

- Luna 补回了 9027 同日报道、同日开工的奥兹储能站；这是实际项目覆盖改善。
- 1533 两组均保留罗马尼亚 95MWp 光伏与 218MWh 储能；基线另外把光伏 95MWp 错填为储能功率 95MW，Luna 未作此推断。
- 1586 两组均找到苏格兰制造基地；基线项目名未过旧对齐阈值，Luna 的名称与投资方角色更贴近 Gold。
- 13649 两组均找到 Vetrozelena 300MW 风电项目，但都未过旧中文项目名阈值；17413 两组均按当前物理边界拆成三站，不能称为三项虚构项目。
- 17413 已是签约事件，Luna 三站均为签约，基线三站均为中标；源文明确签署日 2021-12-10。

以下只比较五个两组均识别、源文确认属于同一工程的第一项目（9027/1533/1586/14595/13649）；容量排除 14595 的组合/单体口径，投资排除 1586 的未标准化币种。均保留旧字段标签，融资仅作 null=Unknown 等义处理。

| 共同项目字段 | 基线正确/可比 | Luna正确/可比 |
|---|---:|---:|
| project_stage | 4/5 | 4/5 |
| capacity_mw | 4/4 | 4/4 |
| financing_model | 4/5 | 4/5 |
| total_investment_usd | 4/4 | 4/4 |

解释边界：

- 1533 的 8→1 在两组共同发生：当前规则只提取本次收购，旧 Gold 同时标注七个背景项目；不能把这七项全称为模型召回退化。
- 14595 的 26 岛组合记录在两组均触发物理边界 pending_review；当前规则不把组合 8MW 分配到单个岛站，因此容量与旧 Gold 不一致。
- 27309 锂矿项目在旧 Gold 中为 irrelevant；当前 docs/status/s01b.md 明确保留锂资源/锂盐，两组共同收录不能直接称为模型误收。
- 17413 旧 Gold 为一个合同项目，当前两组均拆三站。该条与 1533/14595 的口径变化及项目名阈值共同压低 F1；其提升不是总体准确率提升 26 个百分点的证据。
- 基线的 18491 为 excluded/pending_review，27082 为 pending_review，未显示最终允许入库；Luna 在 S1 拦下的是不必要的后续提取。
- null 融资与 Unknown 的等义诊断另外保留；1586 的英镑/人民币原始投资、17413 的合同金额结构不宜当作规范 USD 总投资对比。
- 名字阈值与实体逐字匹配可能漏配同一工程或短名/全称；保留旧分数与逐项源文诊断，不修改阈值来提高分数。
- GPT-6 Luna 确实返回 gpt-6-luna；DeepSeek 旧请求名实际返回 deepseek-flash，官方解释为 V4.1 Flash。
- 测试包含两次 Luna S2 90 秒超时，以及一次设备知识资源漏带后补齐的隔离准备失败；先前响应和请求预算均保留。
- 本次先前中断后复用两个成功 SDK 响应。一次同轮异常 DeepSeek JSON 被重复回放后再取得成功新响应，最终结果完整；资源统计按真实 SDK 请求去重。
- 全部结果完成后缓存重跑新增请求为 0，整个 SDK 账本逐文件 hash 不变。重跑期间发现报告目录混入逐篇列表会影响旧 evaluator 的“上次报告”读取，已在实验 runner 使用独立 canonical 子目录解决，未改生产 evaluator。
- 各供应商的成功原始响应、解析/校验错误、配置、输入和输出 hash 均在本目录。费用是保守估算，不是账单实付。

价格依据：[OpenAI](https://developers.openai.com/api/docs/pricing)、[Gemini](https://ai.google.dev/gemini-api/docs/pricing)、[DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/)。

数据：comparison.json、summary.json、verification.json、ledger/calls.json、ledger/reuses.json、articles/、benchmark/、snapshot/。
未执行生产写库、默认模型替换、全 Pipeline 入库/导出、推送或部署；Python 审计 hook 将实验写入与 SQLite 访问限制在独立目录。另一授权对话正在更新生产，因此没有声称生产库前后 hash 不变。

完整本地证据目录：`output/luna-low-pilot10-20261005/`。
