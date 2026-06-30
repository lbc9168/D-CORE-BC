# s02e_source_finder 设计方案（中文外链 + 交叉验证）

> 日期：2026-06-25
> 状态：设计已定（经 grill-me 逐条确认；含 baseline 简化修订），待写实施计划
> 关联：取代 `s02c_cross_verification`（保留弃用）；复用 `external-tools/external-link-adder/fill_links.py` 的搜索/判定原语

---

## 一、我的诉求（用户计划）

1. 现在新闻来自国富咨询（goalfore，s01a 爬虫）。我要给数据库**补上外部参考链接（external links）**，且**只用中文信源**（外文的 GDELT 路线不要了）。
2. 找源方式：用 Serper（Google 搜索 API）按新闻内容上网搜，Gemini 从结果里挑合适的中文源，把链接挂到数据库。
3. 分两步走：
   - **P1（当前方案）**：权衡之下**只存链接**。
   - **P2（未来方案）**：引入 `verification_status` + 置信度 + 人工复核队列（取代 s02c 的交叉验证职责）。
4. 交叉验证我接受**宽松口径**：在我的场景里，中文站之间互相转载、内容一致，就视为已被交叉印证——内容完全一致的转载反而是更有力的印证。
5. 原则：**对现有程序改动尽量小、不引入新 bug**；后置步骤若需大改要谨慎。能跑完我所有事件，异常多跑要能被拦截。

> 已知并接受的约束：Serper 只返回标题+摘要（快照），不抓网页全文；能抓全文的 API 成本高，**暂不实施**。这是 P2 验证深度的天花板（见难点）。

---

## 二、总体架构与关键决策（P1/P2 共用）

| # | 决策 | 结论 |
|---|------|------|
| 模块 | 形态 | 新建独立可选子项目 **`s02e_source_finder`**，**取代 s02c**（s02c 保留不删、弃用，可对照）。**P1 = 该模块 v1；P2 = v2（在 v1 的"搜索+判定"核心外加验证层）**，是同一模块两个成熟度。 |
| 复用 | 原语 | 复用 `fill_links.py` 已验证的 `serper_search` / `gemini_classify` / `SerperRateLimiter`(≤2/s) / 指数退避重试 / 断点续跑；外面换成"读写主库 + 按事件 + 验证层"。 |
| 单位 | 搜索粒度 | **按 `project_event` 搜**（不是按项目）——目标是"项目下每个事件都能追溯到自己的参考源"。 |
| 反塌缩 | 防"所有事件搜出同一条" | ① **按事件构造查询**：查询带该事件的**阶段词 + 年份**（签约/中标·开工/动工·竣工/投产/并网…，年份取 `publish_date`/计划日期）；② **告诉 Gemini 目标里程碑**，只挑阶段+时间对得上该事件的结果，对不上返回 null，不许拿项目通用文章凑；③ **项目内跨事件 URL 去重**，优先给不同事件配不同源。 |
| 兜底 | 事件搜不到专属源 | 取**方案 (a)**：P1 退一步挂项目级通用链接并**标记"非事件专属"**；P2 里这种算 medium（项目印证、里程碑待确认）。 |
| 范围 | 对哪些事件花钱搜 | 只对**"通过 s02e 宽松 gate（见下行）+ 当前无链接"的事件**。宽松 gate 保证覆盖所有网站可见项目，宁可多搜不漏搜。 |
| gate（范围门槛） | s02e 自带"宽松超集" | **不抽取、不共用 s03 的 `passes_baseline`，s03 可见性逻辑原封不动**。s02e 用自己一个**故意放宽的 gate**——只卡稳的、无歧义条件（国家 ≠ 中港澳台、`excluded=0`、非隐藏类别、+ 宽松年份：任意日期字段 ≥2022），**故意不复刻脆弱派生量**（精确"最后事件年"、"主要中方实体"）。少卡条件 → 只多放进、不漏 → **构造上保证是 s03 可见集的超集**（"网站可见的一定被 s02e 搜到"）。代价＝多搜一小撮 s03 不显示的边界项目（链接写库但不导出，无害浪费，量小）。 |
| 顺序 | 流水线位置 | `s01a → s01b → s02a → s02b → **s02e** → s03 → s04`（s02e 卡在 s02b 与 s03 之间；s02c 被取代）。**无环**：可见性由项目自身属性决定、与"有没有链接"无关，链接不反向影响可见性。 |
| 中文 | 信源语言保证 | ① Serper 加 `gl=cn` + `hl=zh-cn`；② 查询词本就是中文；③ **Gemini 硬规定"必须中文网站/中文报道，非中文一律 null"**（这道是真正保证）。**不做域名白名单**（太脆）。 |
| 运行 | 模式 | 独立可选模块 `uv run s02e_source_finder`，**原地读写主库 `project_events`**，带 `--dry-run` / `--limit` / 断点续跑；**不进默认流水线**，真实全量跑 GATED 人值守。 |
| 增量 | 后续更新 | 靠**幂等 `should_skip`** 实现自动增量：已处理过的事件直接跳过，每次只处理新增事件。无需单独"增量模式"。 |
| 熔断 | 批量上限 | `--max-events` 默认 **~3500**（略大于当前事件数 ~2600，不过大）。开跑前算 pending：`≤上限`→全跑完；`>上限`→**直接中止 + 大声报警 + 要求显式 `--max-events` 放行**（不静默截断、不闷头烧钱）。 |

---

## 三、P1 —— 当前方案（只存链接）

**目标**：给每个 baseline 可见、当前无链接的 `project_event` 找一个中文参考源并入库，前端按事件展示。

P1 拆成两个可分开实施的子部分：

### P1-a：s02e 找源 + 按事件写主库
- 读主库：对每个目标 `project_event`，取其所在 canonical 项目的 `project_name` + 主要中方实体（复用 s03 `primary_entity` 取法）+ `country`，加该事件的 `project_stage`、年份、`source_link` 作种子/上下文。
- 搜索+判定：按"总体架构"的反塌缩三件套跑 Serper + Gemini；中文三道闸。
- 写库：每个事件**最多存 1 个官方 + 1 个非官方**（**取消 fill_links"有官方就丢非官方"的规则**，两类都留——非官方那条正是 P2 验证要复用的），**追加去重**进该事件的 `project_events.official_website_links` / `non_official_website_links`（JSON 数组），不覆盖 s01b 提取已有的链接。

### P1-b：s03 加 `event_links` + s04 逐事件展示（破坏性最小）
- **s03 `flat_builder`（纯新增 ~10 行）**：在现有 `for ev in proj_events` 循环里多攒一个数组，给 row dict 加一个字段 `event_links`：
  ```
  event_links: [{ stage_en, year, official, non_official }, ...]   # 只收有链接的事件
  ```
  **不碰** `stage_events`（趋势图不受影响）、**不碰**项目级聚合的 `official_website_links`/`non_official_website_links`（CSV/其它消费方照旧）。
- **s04 `ProjectDetail.tsx`（只改 1 个 section + 加 1 个类型字段）**：把现在"外部链接"那段（项目级拼平列表）换成**逐事件**渲染——每个事件一行：英文阶段标签（如 `Signed · 2023`）+ 链接，**官方优先、没有官方才显示非官方**（`official ?? non_official`）。其它（timeline_notes、stage_events、数字条、实体区）一概不动。

### P1 难点
- **按事件搜的塌缩**：弱区分度的事件可能仍搜不出专属源 → 落兜底(a) 挂项目级链接。属可接受退化，但比例需在小跑时观察。
- **主要中方实体的选取**：要和 s03 `primary_entity` 口径一致，避免用错实体导致搜偏。

### P1 潜在问题
- **中文保证非 100%**：`gl/hl` 只是偏向，真正靠 Gemini 那道闸；若 Gemini 放过一条英文源，会漏网 → 抽查兜住。
- **链接质量**：纯参考链接，错配是低风险（不改任何业务字段），但仍应抽查。

---

## 四、P2 —— 未来方案（验证 + 置信度 + 复核队列）

**目标**：在 P1 基础上判定每个事件是否被中文源**交叉印证**，写 `verification_status` + 置信度，模糊的进人工复核队列。**取代 s02c 的职责**。

### 与 P1 的关系（P2 = P1 + 薄层）
- 验证源 = P1 找到的那个中文源（存在 `verification_sources`）。
- **复用 P1 产出**：若 P1 已为该事件存了链接，**P2 直接对该链接做"是否印证"的再判定（仅 Gemini，无需再搜 Serper）**；只有 P1 没找到链接的事件才新搜。→ P2 大部分是 Gemini-only，很便宜。
- ⚠️ **skip 谓词不同**：P1 跳过"已有链接"的事件；**P2 必须跳过"已验证(`verification_status != unverified`)"的事件**，否则 P1 填了链接的事件会被 P2 误当"已处理"而漏验。

### 宽松口径（用户拍板 C）
- 只要找到**可信中文源描述了同一事件**就视为印证，**转载也算**（内容一致即印证，符合用户场景）。**砍掉"独立性判断"**（我之前评估里唯一的真难点，被此决定消除）。最低要求：源域名 ≠ 该事件 `source_link` 域名。
- **置信度 = 匹配强度**（是不是**这个项目 + 这个里程碑**），不再衡量独立性。

### 置信度 → 状态 → 队列 映射
| 情况 | confidence | verification_status | needs_manual_review |
|------|-----------|---------------------|---------------------|
| 明确同项目（名/主体/国家/容量或地点一致）且对得上里程碑（阶段+年份） | high | **verified** | 0 |
| 同项目，但里程碑对不准（项目级/非事件专属，即兜底 (a)） | medium | **verified** | 1（`milestone_confirm`） |
| 不确定是不是同名另一个项目 | low | **pending_review** | 1（`same_project_check`） |
| 搜了但无可信中文源 | — | **unmatched** | 0（不进队列） |
| 未轮到 / 默认 | — | unverified | 0 |

- **medium 算 verified**（用户立场：项目被印证就够）。
- **进复核队列 = `needs_manual_review=1` 的两类**；high 不打扰人，unmatched 不进队列。

### 缺里程碑 → 候选（不自动写库）
- 场景：源对得上项目、但里程碑库里没有。**不自动插入新 `project_event`**——只在复核队列加一条 `reason_type = "new_milestone_candidate"`，带 canonical_id + 推测阶段/年份 + 信源链接，**交人工定**。
- 理由（见难点"自动插事件"）：自动插稀疏事件会引爆下游隐性逻辑；后置写入又脆弱。队列做候选 = 零程序风险、价值不丢、职责干净。

### 复核队列机制
- 复用 v2.1 `review/` 写法（`queue.py` add/pending + `export_queue.py` 导 CSV），但 **s02e 用自己独立的队列存储**（专门表/库，不与提取复核 `review_queue` 混；同 recheck 当年单独 `recheck_queue.db` 的做法）。
- `reason_type`：`milestone_confirm` / `same_project_check` / `new_milestone_candidate`。导出 CSV 给人工。

### P2 难点
- **同名错配（唯一真风险）**：宽松口径下，独立性不查了，唯一会错的就是"源其实是同名另一个项目/对不上事件"。→ 用 confidence=匹配强度 + low 进 `same_project_check` 队列兜住；小跑时重点抽查。
- **快照天花板**：只有标题+摘要 → 印证证据偏弱 → 更多落 medium/人工。可接受（队列是安全阀），将来接全文抓取可提自动率。

### P2 潜在问题
- **队列体积**：medium/low/candidate 若精度低会淹没队列 → 小跑调 prompt 后再全量。
- **重建脆弱性**：见下"共同潜在问题"。

---

## 五、共同的难点与潜在问题（汇总）

1. **重建即丢（s02e 写入的脆弱性）**：s02e 后置写 `project_events`（链接 + 验证字段）。将来 s02a 一旦全量重建 canonical（如 B3 那种），会按 s01b 数据重铸 `project_events`，**冲掉 s02e 的写入**。s02c/recheck 当年有同样问题。**对策（实施计划里落地，不在 s02e 内解决）= 方案②"重建前导出、重建后重放"**：导出 s02e 结果，按**稳定信号 `canonical_id + 阶段 + source_link`**（而非会变的事件编号 `-NN`）重新对位灌回。
2. **自动插事件（已决定不做）**：若将来真要做，需三道保险——用 s02a 现成 `_gen_event_id`、给插入事件打 `event_source='s02e'` provenance、**先审计 s03 终态/容量聚合**确认稀疏事件不会把 `capacity_mw`/投资额拉空（终态事件无容量→项目容量变 null 是典型静默 bug）。列为 backlog 高风险项。
3. **成本**：几千次 Serper（与 fill_links 之前 6389 行同量级，已知不大）+ Gemini flash-lite（更便宜）；熔断器防 bug 暴涨。
4. **中文保证依赖 Gemini 判定**：非中文漏网风险靠抽查兜。

> 注：原"bri_baseline 抽取行为不变"那条风险**已消除**——改为 s02e 自带宽松超集 gate、**完全不动 s03 可见性逻辑**，无需抽取、无需一致性测试（见 §二「gate（范围门槛）」行）。

---

## 六、明确不做（YAGNI / 范围外）

- ❌ 全文抓取 API（成本高，暂缓）——P2 先吃快照，自动率天花板由此而来。
- ❌ 自动插入缺失里程碑事件（改为队列候选，人工定）。
- ❌ 独立性/转载识别（用户选宽松 C，转载即印证）。
- ❌ 域名白名单（太脆）。
- ❌ 删除 s02c（保留弃用即可）。
- ❌ 进默认流水线自动跑（GATED 人值守；增量自动跑留作后续可选挂载）。
- ❌ 抽取/统一 baseline 到共享模块（已撤销；s02e 自带宽松 gate、s03 可见性逻辑不动）。
- ⏭ 前端 `shouldExcludeByPolicy`（第三份可见性拷贝）统一到同一定义 → **backlog**（本次不动，不影响功能）。

---

## 七、数据/schema 落点（均为现有字段，零 schema 改动）

- **链接（P1）**：`project_events.official_website_links` / `non_official_website_links`（JSON 数组，已贯通 s03→s04）。
- **验证（P2）**：`project_events.verification_status`(unverified|verified|unmatched|pending_review) / `verification_confidence`(high|medium|low) / `verification_sources`(JSON URL 数组) / `needs_manual_review`(0|1)。
- **前端新增（P1-b）**：`projects_flat.event_links`（新字段）+ `Project.event_links?` 类型。
- **不新建共享 baseline 模块**：s02e 自带宽松 scope gate（独立判定，s03 可见性逻辑不动）。
- **新建队列存储**：s02e 专属复核队列表/库 + 导出 CSV。

---

## 八、验收与上线流程

1. `--limit 50~100` + `--dry-run` 小跑，导出 CSV/日志。
2. **人工抽查 ~30 条**：重点看"同名错配"与"事件阶段/年份对没对上"。
3. 据抽查调 Gemini prompt。
4. 通过后全量（受 `--max-events` 熔断保护），**GATED 人值守**。
5. P2 上线后，定期看复核队列 CSV，处理三类候选。
