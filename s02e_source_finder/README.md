# 外部参考来源核验与常态更新

## 常态生产入口

正常Pipeline默认在S02B之后、S03之前运行`recurring`。私有找源范围从2018年开始，网页仍以2022年为基线；当前参考池2654项目，缺发电容量池311项目，共3665事件。只读生成名单复用真实S03筛选，不排除已有链接或曾进入试点的项目。

```powershell
# 仅同步待处理队列/预览，不请求API、不写主库
uv run --project pipeline python -m s02e_source_finder.recurring
# 执行一批（默认25事件）；重复运行继续队列并共享累计预算
uv run --project pipeline python -m s02e_source_finder.recurring --execute
```

配置：`supporting_docs/source_finder_config.json`。2026-10-05用户取消本地累计费用、搜索次数和模型调用次数上限，授权继续处理全部剩余事件；持久账本仍记录调用和保守费用，不清零。正式配置当前为4路并发；首次尝试每事件最多2搜索/4正文。公开项目名/国家/参与方用于Google找源，相关目标身份和候选正文发送给既有DeepSeek核验，原始新闻整篇不用于搜索。

主库保存`external_reference_candidates`；队列、请求、全文、结果、日志和备份在`data/recurring/`。已有有效合格来源的任务跳过，其他候选失败不影响结束。无合格来源的事件第二遍先重试最多4个旧网页一次，仍无结果则最多两轮新搜索，每轮验证最多4个新网页；跨轮排除已尝试URL及同篇文章变体，任一合格即入库并结束，两轮均无合格结果则记录`no_reference_found`并终止本轮。旧版完成但无来源的事件也适用此一次性补搜。新版结果独立命名，`results/*-bounded-source-search-v1.progress.json`保存第二遍恢复位置；首次应用自动备份队列。批次`found_reference`和`exhausted`分别报告成功和耗尽，`processed`为两者合计。预算、认证、余额和限流暂停保留待续跑，未知结果调用不自动重发；事实变化或80天到期重验。没有合格中文来源就留空。缺容量只顺带检查同批正文，空数值且空raw、项目/单位/期次/事件证据完整才补空并审计，其他值保留。

生产配置的`source_review_enabled=true`启用GPT-6 Luna Low来源审核：未登记域名、原发布者待定及平台页面先抓取身份/出处证据，再逐页判断；通过即可继续项目审核。`source_review_min_urls=6`表示同完整hostname出现6个不同文章URL且机构归属核验通过后自动登记，重复Activity/URL不增次数，共享平台不整体放行，转载仍显示原出处。动态名单在`source-admission.sqlite/admitted_hosts`，手工51机构名单不覆盖，自动登记最多80天有效。Luna接收公开候选页面和来源信息，共用原调用账本。新规则优先重审旧来源拦截，复用旧URL、成功搜索和抽取；缺少页眉页脚字段的旧网页缓存会更新。其他项目/事件/容量门禁不变。

`external_references_config.json`优先读取已经生成的常态快照，未生成时保留旧样本快照。S03仍逐候选核验，正常完整更新可直接带到本地网页，单独找源命令只写主库/快照，不执行站点部署。

以下为保留的历史校准与单项审核入口；其中旧试点预算和生产写入门槛不替代上述常态入口。

当前提供中文公开参考核验及发电容量补空候选。双语正文可作私有容量证据，公开参考仅限中文；覆盖率不设硬门槛。DeepSeek 仍为默认，GPT-6 Luna Low 仅用于有预算的配对试点。所有旧样本均为开发材料，代理标签不是人工金标准。

## Luna Low 与容量补充试点

`enrichment_targets` 从当前 s03 展示基线生成参考名单，另生成仅放开发电容量条件的名单；原新闻中可追溯的中方参与和待审排除仍生效。`enrichment_trial` 共用冻结页面，分别运行 DeepSeek 与 OpenAI，正文、目标、模型、effort、prompt、schema 均进入请求身份。搜索和抽取共用持久账本，未知费用全额占用预留，不自动重试或切模型。新试点固定5个参考项目和5个缺容量项目，最多20次搜索、每项目4页、80次新抽取；旧13候选最多26次抽取，合计不超过5美元保守预算。

```powershell
# 已安装模块环境；默认预览不请求模型
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_targets --help
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_trial discover
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_trial evaluate --split new --registry s02e_source_finder/data/luna-capacity-pilot-20261005/new-publisher-registry.json --labels s02e_source_finder/data/luna-capacity-pilot-20261005/new-labels.json
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_trial budget
# 离线生成逐项审核与配对统计（不请求模型）
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_report capacity --input s02e_source_finder/data/luna-capacity-pilot-20261005 --main-db s02a_duplicate_merger/data/db/bri_re_database.db --report-dir s02e_source_finder/data/luna-capacity-pilot-20261005/review/provenance-corrected
& s02e_source_finder/.venv/Scripts/python.exe -m s02e_source_finder.enrichment_report metrics --input s02e_source_finder/data/luna-capacity-pilot-20261005
```

授权预算内的实际请求另加 `--execute`。OpenAI 使用 `OPENAI_API_KEY` 或既有 `GPT_API_KEY`，可选 `OPENAI_BASE_URL`，默认 `gpt-6-luna` / `reasoning_effort=low`；不修改 `.env`。发送项目身份、参与方、量值和候选正文，不发送独立标签。试点文件位于私有 `data/luna-capacity-pilot-20261005/`，不得复制完整正文或英文证据到 public。

本次新样本使用 `new-publisher-registry.json`，旧样本使用 `old-publisher-registry.json`。已生成且内容不同的审核文件不覆盖，修改规则后的离线审核须指定新的 `--report-dir`；本次繁体来源修复后的审核在 `review/provenance-corrected/`，冻结模型比较保持原样。可读总报告为 `review/report.html`。

容量流程使用 `capacity_apply {prepare,rehearse,apply}`（各命令 `--help` 查看参数）。候选须明确数值、原单位、设施/期次和既有事件；只补原数值和 raw 均为空的发电容量。批准文件绑定完整候选 manifest hash 和具体 candidate ID。`developer_rehearsal` 仅可写隔离副本；生产必须是用户逐条确认的 `human` 批准、精确 plan hash 与 `--allow-production`。锁内重新校验、验证备份、单事务审计、整批漂移拒绝和幂等复用均为必经步骤。合并模块保留本流程已确认容量，并将新冲突写入私有复核表。

普通 s03、离线重建和流水线通过 `supporting_docs/external_references_config.json` 使用绑定主库的参考快照；缺失或无效快照明确失败。隔离数据库不会隐式读取生产指针，需显式提供快照。未请求线上发布。

## 既有 Google 搜索开发入口与历史样例

过程与下一步见[实施计划](../docs/superpowers/plans/2026-09-06-s02e-google-search-to-reference-plan.md)，本地逐条结果见`data/google-source-loop-v1/report.md`。机构身份与文章对应关系分别判断：未知网站、未知原发布者、平台托管、内部署名、外部转载分别记录；页头来源控件与正文分开保留。日期、实际动作和指定子设施须在相同动作分句得到支持。旧Page与公开参考格式兼容。

```powershell
# 默认只预览，零API调用；执行前核对版本和预算，不能把改代码后重跑视为免费续跑
uv run --project s02e_source_finder python -m s02e_source_finder.source_loop s02e_source_finder/data/google-source-loop-v1/inputs-v1.json s02e_source_finder/data/google-source-loop-v1/publisher-registry-v2.json
# 从已经完成的原始记录重建当前报告，不发请求
uv run --project s02e_source_finder python s02e_source_finder/data/google-source-loop-v1/build_report.py
# 展示本轮克罗地亚真实详情样例
uv run --project s02e_source_finder python -m s02e_source_finder.preview s02e_source_finder/data/samples/google-source-loop-hrv-v1/report.json
```

开发执行器最多5事件、10次Google请求、24次正文校验，每事件4条新增候选；--execute才联网。完整请求参数绑定缓存，同目标/正文的已有抽取可跨判定规则重用；缓存失败不会自动重调。

## 运行

在仓库根目录执行（已安装环境可直接使用模块 `.venv` 的 Python）：

```powershell
uv sync --project s02e_source_finder
# 离线预览：零搜索、零模型调用、零验证结果写入
uv run --project s02e_source_finder source-finder-sample --project-id EGY-ES-0001 --event-id EGY-ES-0001-01
# 明确执行：旧候选 + 可选手动搜索候选，单事件且调用有上限
uv run --project s02e_source_finder source-finder-sample --project-id EGY-ES-0001 --event-id EGY-ES-0001-01 --execute --provider deepseek --max-model-calls 4 --candidate-url http://www.intl.ceec.net.cn/art/2026/2/25/art_55165_2514769.html --candidate-url https://www.stcn.com/article/detail/3648606.html
```

使用根目录 `.env` 中现有 `GEMINI_API_KEY` 或 `DS_API_KEY` / `DEEPSEEK_API_KEY`，不输出凭据。执行会向指定模型提供目标项目/事件、相关原新闻和候选正文。每次运行目录独立，保留候选、正文哈希、引文、判定、版本、实际 token 与源库哈希。模型服务失败后停止该轮后续模型请求，不自动换模型或扩大预算。Gemini与DeepSeek当前均可调用；第一阶段样例使用DeepSeek，第二阶段保留两种模型相同开发样本的结果。

默认使用系统 DNS。仅在可信本地 Fake-IP 代理环境中显式追加 `--dns-mode https`，通过 Google HTTPS DNS 核实公共解析；主机名会发送至该 DNS 服务。私网/本机地址仍拒绝，不关闭 TLS 校验。官方样例的 HTTPS 暂不可访问，HTTP 正文可读；两者作为显式候选分别记录，不自动降级。

## 本地网页验收

```powershell
uv run --project s02e_source_finder python -m s02e_source_finder.preview s02e_source_finder/data/samples/20260906T044651Z-6c70b7e0/report.json
cd s04_web_visualization
npm run dev -- --host 127.0.0.1 --port 5179 --strictPort
```

打开 `http://127.0.0.1:5179/reference-preview.html`。左边显示候选接受/拒绝/待确认理由，右边复用真实项目详情组件；可切换中英文和无合格链接状态。展示样式借用已有公开项目行；找源目标与判定使用本轮主库事实。生成器仅写入被 Git 忽略的 `src/dev/reference-sample.json`，不覆盖生产 JSON。预览 HTML 不属于生产构建入口，私有正文不进入页面或构建。

## 本次验收与后续门槛

- 项目 `EGY-ES-0001`，事件 `EGY-ES-0001-01`：4 候选，2 事件参考、1 拒绝、1 待确认。
- 官方正文支持本班 Nefertiti 1000MWh 项目中标；证券时报报道注明人民财讯来源，并转述中国能建消息，不视为独立印证。
- 旧电力网文章为 1GW 光伏＋600MWh 储能开工，明确错配。新浪转载虽涉及目标，但模型引文删掉原句括号内容，未通过定位；该转载原始发布者的采编身份也未独立确认，不作为合格参考。
- 原型保留 90 天有效期；有效期排除已在前端实现，定期重验尚待增量阶段。
- 16 项定向测试覆盖只读保护、精确项目/事件关联、匹配及引文约束、私网拒绝、DNS 模式、403/404 和正文抽取。类型检查、生产构建及实际浏览器成功/空状态检查另有验收记录。

第一阶段于2026-09-06获用户验收。现有公开链接字段尚未清理，本阶段不构成存量核验完成或上线许可。

## 30事件校准入口

从本轮主库生成目标时复用s03扁平构建及网页基线，s03只写临时数据库快照。当前总体为2016项目/2496事件；固定主表20、TX5、MF5，按项目、原新闻及候选URL连通组隔离开发/留出集。原文标签由独立代理在调用前复核，`human_confirmed=false`，用户尚未逐条确认，不能作为正式人工金标准。

```powershell
# 只读生成新一批离线目标，已有冻结文件不能覆盖
uv run --project s02e_source_finder python -m s02e_source_finder.targets --output-dir s02e_source_finder/data/pilot-v2
# 以下预览不发请求、不创建验证结果
uv run --project s02e_source_finder python -m s02e_source_finder.pilot prepare s02e_source_finder/data/pilot-v1/selected_events.json --per-event 2
uv run --project s02e_source_finder python -m s02e_source_finder.search s02e_source_finder/data/pilot-v1/selected_events.json --round 1 --max-queries 7
uv run --project s02e_source_finder python -m s02e_source_finder.pilot evaluate s02e_source_finder/data/pilot-v1/corpus-calibration-v1.json --provider deepseek --max-calls 30 --split development
# 复算本轮报告，不发外部请求
uv run --project s02e_source_finder python -m s02e_source_finder.pilot_report s02e_source_finder/data/pilot-v1/corpus-calibration-v1.json
```

执行抓取、搜索或模型需显式`--execute`。`prepare`缓存旧链正文；`search`最多3轮、每轮前5条，`--event-id`与可选`--query`限定单个补搜对象；`augment`把明确选择的新候选固化为新语料。`evaluate`要求同名`.labels.json`已冻结且与语料全集/哈希一致；标签不会进入模型输入。开发门槛未过，不应执行`--split holdout`。当前入口执行开发/留出选择，不自动决定验收或发布。

`pilot.sqlite`是试点调用账本，付费请求前先在事务中预留；相同规则/语料/模型/阶段续跑不会重调已完成或中断不明请求，预算不会自动扩大。更改规则会产生新批次并重新消耗调用，不能把重复启动当成免费重跑。搜索按事件/轮次预留，改查询文本也不能重放已预留轮次。生产级验证、TTL重验和全量预算调度尚未实现。

此前提示词校准产物在`data/pilot-v1/`：`selected_events.json`、冻结正文及标签、`rules-v1/v2.json`、`evaluation-*.json`、`pilot.sqlite`与`calibration-report.md`。36个候选、26个可读页面；16次Serper搜索；开发集两模型各18请求×两版规则=72次模型请求。初版Gemini有1次结构错误缺失用量，保留未知项。该历史报告不覆盖下述结构化复验，不得据此启用全量自动通过。

## 结构化复验入口

实现与结果记录在 [本轮计划](../docs/superpowers/plans/2026-09-06-s02e-structured-verification-calibration.md)。目标卡独立从原新闻提取，模型只接收目标身份/量值和候选段落编号；阶段、日期、子设施、容量和来源身份由程序检查。卡片及标签均为未获人工确认的草稿。

```powershell
# 开发集预览：零请求；不要为查看旧结果追加 --execute
uv run --project s02e_source_finder python -m s02e_source_finder.structured_run s02e_source_finder/data/pilot-v1/corpus-calibration-v1.json s02e_source_finder/data/pilot-v1/structured-target-cards-v2.json --provider deepseek --max-calls 18
# 从已有调用和离线重判生成当前报告：零外部请求
uv run --project s02e_source_finder python -m s02e_source_finder.structured_report s02e_source_finder/data/pilot-v1/corpus-calibration-v1.json
```

`structured_recheck`接收语料、目标卡、原`structured-evaluation-*.json`三参数，验证原prompt/标签/正文/规则契约后离线重判，输出独立文件，不改旧账本。相同重判文件存在时拒绝覆盖。`structured_run --split holdout --max-calls 8 --development-evaluation <合格开发结果文件>`还要求同模型、同语料/标签、完整开发判定、至少一个安全事件参考、零错误放行及相同确定性规则；仅加`--execute`才调用。

来源机构表当前仅为开发试点覆盖，未知站点/转载署名保留待确认。Gemini原生Schema只约束服务器结构，完整长度与数值约束仍在本地验证。任何规则或代码变动会生成新批次并重新消耗调用；不得把再次执行当作免费查看。首个请求连接失败可显式`--retry-of <原批次key>`建立新尝试，旧失败与未知用量保留。

当前结果见`data/pilot-v1/structured-report.md`：新增65次请求尝试、62次返回用量、3次无用量，无新搜索；两次离线重判不收费调用。DeepSeek留出为0接受/10待确认/3拒绝，冻结草稿中的6条可接受候选全部隐藏，不能称为通过。下一版应独立补齐机构身份核实并使用新未见样本验收；本次留出已用作诊断，不再当作未见集。

## Google闭环与新10事件验收

`source_loop`用于最多5个开发事件；`acceptance`保留旧命令名，处理1至10个不同项目并允许已有搜索/抽取结果。标签manifest仅保留来源和哈希，不设正例数量或覆盖率门槛。

```powershell
# 默认仅预览，不请求网页或模型，不创建调用账本
uv run --project s02e_source_finder python -m s02e_source_finder.source_loop s02e_source_finder/data/google-source-loop-v1/inputs-v1.json s02e_source_finder/data/google-source-loop-v1/publisher-registry-v3.json
uv run --project s02e_source_finder python -m s02e_source_finder.acceptance s02e_source_finder/data/acceptance-v1/inputs-v1.json s02e_source_finder/data/acceptance-v1/publisher-registry-v1.json
```

只有在授权预算内追加`--execute`才联网。Google最多4路并发，DeepSeek串行，预算上限仍为20搜索/60抽取。单条请求、网页或格式失败独立记录，成功引用继续处理；认证、余额和限流只停止新增对应请求。`complete`表示已选候选有明确状态（成功/排除/待确认/失败），不是全成功。原记录不覆盖，失败不自动重试。

量值证据允许多个不相邻段落分别完整支持同一数量，但每组须有完整数值和单位；不允许跨缺失段落拼接。项目身份、动作和日期仍要求连续原文。复合署名只解析明确的整理/编译/来源角色，未知署名保持待确认。详情投影失败只降级该候选并保留理由。

历史输出保留在`data/acceptance-v1/`，旧complete=false和失败资料不修改。当前零API开发验证见`data/development-validation-v1/`：混合批次经过实际执行器、判定和详情投影，并核对源库/生产文件哈希。MF年制造产能与发电装机维度分开；项目参考不推断目标事件日期。待核实来源、旧格式失败缺失响应、项目范围歧义继续单独隐藏。本批13候选不再是独立盲测，不构成生产发布授权。

## 已有结果接入正常详情页（零API）

使用已复核候选生成元数据交接文件，再调用正式JSON导出器；不需要重建事实表或调用省份API。下面路径从仓库根目录运行，Python需能导入s02e及s03的src包（例如设置PYTHONPATH包含s03_data_exporter/src）。

```powershell
python -m s02e_source_finder.export_references --inputs s02e_source_finder/data/acceptance-v1/inputs-v1.json --reviewed s02e_source_finder/data/development-validation-v1/seen-13-rejudgement.json --output s02e_source_finder/data/export-development-validation-v1/external-references.json
python -m s03_data_exporter.json_exporter --db s03_data_exporter/data/output/bri_re_export.db --source-db s02a_duplicate_merger/data/db/bri_re_database.db --output s02e_source_finder/data/export-development-validation-v1/public/data --external-references s02e_source_finder/data/export-development-validation-v1/external-references.json --reference-audit s02e_source_finder/data/export-development-validation-v1/reference-audit.json
```

交接文件不可覆盖为不同内容；后续复核使用新路径。s03三表均输出external_references，没有合格记录就是空数组；不把核验正文或审计报告复制到公开目录。完整run_export及离线重建脚本也支持--external-references，但会走各自原有重建/同步流程，本次没有执行。正常/#/table的成功与空状态证据见data/export-development-validation-v1/，并非reference-preview替代验收。
