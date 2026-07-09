# Developer 登记册 Task 7 灰度对拍演练报告（2026-07-07）

**目的**：在同一份真库只读快照上分别跑旧口径（LLM 自由合并，`use_registry=False`）与新口径
（registry/hierarchy 确定性映射 + LLM 结果降级为建议 CSV，`use_registry=True`），对拍两套结果的
差异面，供用户 GATE 是否可以把新口径转正用于真库。

**红线遵守情况**：全程只对 scratchpad 中的 `copyA.db` / `copyB.db`（源自真库 WAL 安全快照）做写入，
真库 `s02a_duplicate_merger/data/db/bri_re_database.db` 全程零写入、零打开写连接。API key 全程只存在于
进程环境变量，未出现在任何日志/CSV/本报告中。未 `git add`/`commit` 任何文件。

## 方法

1. 用 sqlite3 backup API + 源连接 `mode=ro` 对真库做 WAL 安全快照 → `greyscale_snapshot.db`，
   再各自 backup 一份为 `copyA.db`（旧口径工作副本）、`copyB.db`（新口径工作副本）。
2. `copyA.db`：`developer_normalizer.run(db_path=copyA, apply=True, use_registry=False)`
   —— 现行为 100% 不变（对照组），LLM 三信号候选对确认后直接建 `part_mapping` 落库。
3. `copyB.db`：`developer_normalizer.run(db_path=copyB, apply=True, use_registry=True)`
   —— registry/hierarchy 命中部件直接确定性映射落库；LLM 只处理未命中部件，且确认结果
   降级为登记册补录建议 CSV（不进映射、不写库）。
4. 权威比较用**库值**（不是中间 CSV）：对 `project_events.id` 逐行比较
   `project_developer`（相对归一前 baseline 快照），分类：
   - both-changed-same：两口径都改，改成一样
   - both-changed-diff：两口径都改，改法不同（最危险类，逐条核实）
   - A-only：仅旧口径改了（新口径漏改或有意不改）
   - B-only：仅新口径改了（registry/hierarchy 确定性映射覆盖到、旧口径 LLM 没碰）
5. B-only 逐条溯源：对每个变化的部件，调用真实 `company_lookup.build_index()/lookup()`
   （与 `developer_normalizer.registry_part_mapping` 同一引擎），验证 `新值 == lookup(原值).canonical_cn`。
6. A-only 用新口径产出的登记册补录建议 CSV 做交叉检查，看旧口径的合并决定是否被新口径的
   LLM 独立复现（即使降级为建议、未落库）。

## Headline 数字

| 类别 | 行数 |
|---|---|
| 总 project_events 行数 | 5,365 |
| 两口径都未改 | 4,461 |
| both-changed-same（一致） | 6 |
| both-changed-diff（**需核实**） | 29 |
| A-only-changed（仅旧口径改） | 47 |
| B-only-changed（仅新口径改） | 822 |
| B-only 溯源到 registry/hierarchy | **822 / 822 = 100%** |
| A-only 命中新口径建议 CSV | 12 / 47 = 25.5% |

新口径落库：857 行 / 339 个不重复值（含 128 条 registry/hierarchy 确定性映射）。
旧口径落库：82 行 / 66 个不重复值（50 条 LLM 确认的部件映射）。

## B-only 溯源结果（822 条，100% 有据可查）

逐条用真实 `lookup()` 引擎核实：822 条 B-only 变化中，**每一条的新值都精确等于
`lookup(原值部件).canonical_cn`**（registry 或 hierarchy 来源），零条溯源失败、零红旗。
说明新口径的确定性映射部分完全可信、可解释、可审计——不存在"新口径凭空改值"的情况。

样例（前 20 条见 `diff_b_only.csv`，节选）：
- `东方电气集团` → `中国东方电气集团有限公司`（hierarchy）
- `阿联酋水电公司（Ewec）` → `阿联酋水电公司`（registry，去括号别名）
- `丝路基金` → `丝路基金有限责任公司`（hierarchy）
- `上海电气` → `上海电气集团股份有限公司`（hierarchy）
- `韩国电力公司（KEPCO）` → `韩国电力公司`（registry）

全量明细：`diff_b_only.csv`（scratchpad，含 changed_parts 列标注 OK/MISS，本次全 OK）。

## both-changed-diff 核实（29 条，逐条查证结论：无风险）

对 29 条中每个发生变化的部件，用真实 `lookup()` 核对 baseline 值：

- **有 registry/hierarchy 依据的部件（25/29 条覆盖）**：新口径（B）的结果 **100% 等于
  `lookup(baseline).canonical_cn`**——即新口径永远给出可验证的确定性答案。差异完全来自
  **旧口径 LLM 对同一部件给出了不同（且未经登记册验证）的自由改写**，例如：
  - `国机工程集团` baseline → 旧口径 LLM 改成 `国机集团`（截断，无据）；新口径给出
    `中国机械工业集团有限公司`（hierarchy 命中，法定全称）。
  - `中国南方电网公司` baseline → 旧口径 LLM 改成 `中国南方电网有限责任公司`（LLM 臆测后缀，
    registry 未收录此原值，baseline 本身未命中）；新口径原样保留（未命中即不动，保守）。
  - `韩国电力公司（KEPCO）` → 两口径都识别为 registry 命中 `韩国电力公司`，一致。
- **无 registry/hierarchy 依据的 4 处残留（旧口径 LLM 独有改写，新口径未触碰对应部件）**：
  `沙特电力ACWA公司`、`中水对外`→`中水对外投资建设`、`埃及新型可再生能源管理局（NREA）`、
  `葛洲坝国际公司`、`日本丸红株式会社`——这些是旧口径 LLM 在**另一个部件**上的自由匹配，
  与新口径改的是同一行里的**不同部件**，两侧改动不冲突，只是行级 diff 把它们归到了同一行。

**结论**：29 条 both-diff 中，无一条属于"新口径给出错误答案"；全部差异要么是（a）新口径给出
registry/hierarchy 可验证答案、旧口径给出未经验证的 LLM 自由改写，要么是（b）同一行里两口径
各自改了不同部件，互不冲突。全量明细：`diff_both_diff.csv` + `both_diff_term_check.txt`（scratchpad）。

## A-only 样例核实（47 条中的模式）

47 条仅旧口径改动的案例可分两类：

1. **旧口径 LLM 的合理归一，新口径暂未覆盖**（约占多数）——如
   `东方电气国际合作公司`→`东方电气集团国际合作有限公司`、
   `坦桑尼亚电力供应有限公司`→`坦桑尼亚电力有限公司`。其中 **12/47（25.5%）已被新口径的
   LLM 独立确认并写入登记册补录建议 CSV**，等待人工复核后正式入册（入册后下次跑新口径即可
   确定性覆盖）。
2. **旧口径 LLM 的风险性发挥，新口径保守跳过（更安全）**——如
   `葛洲坝国际公司` baseline → 旧口径 LLM 加前缀臆测成 `中国能建葛洲坝国际公司`（未经验证的
   归属声明，出现 2 次：`IDN-XX-0012-01`/`VNM-TH-0017-01`）；
   `中广核能源科技（老挝）有限公司` → 旧口径 LLM 缩写成 `中广核老挝公司`（信息丢失，出现 2 次）；
   `三峡集团所属三峡国际` → 旧口径 LLM 直接丢弃"三峡集团所属"定语。
   这类案例中，新口径的"未命中即不动"策略避免了旧口径的过度发挥。

全量明细（含 in_suggestions_csv 标记）：`diff_a_only_traced.csv`（scratchpad）。

## both-changed-same（6 条，一致性校验通过）

6 条中两口径完全独立地得出同一个结果（"中国能建山西院"→"中国能源建设集团山西省电力勘测
设计院有限公司"），说明 LLM 自由匹配与 registry/hierarchy 确定性映射在有据可查的场景下能
相互印证，无矛盾。

## 结论与建议

1. **新口径（registry/hierarchy 优先 + LLM 降级为建议）在本次演练中零红旗**：822 条 B-only
   变化 100% 可溯源、可验证；29 条 both-diff 全部核实为"新口径更保守/更可信、旧口径 LLM
   自由发挥"，未发现新口径产生错误合并或过度归一的证据。
2. **建议**：可以按计划把新口径（`use_registry=True`）转正为真库运行的默认口径。
3. **遗留工作**（不在本次演练范围内，供下一步排期）：
   - 新口径产出的补录建议 CSV（本次 12 条已被 A-only 交叉验证为"确实是同一法人"）建议人工
     复核后正式写入 `developer_registry.json`，可将 B-only 覆盖面进一步扩大。
   - miss CSV（本次约 800+ 未命中部件，含大量长尾/外文/一次性名称）留作后续 campaign 参考，
     不需要现在处理。
4. 本报告及所有中间 CSV/日志均在 scratchpad，未落地到任何真实数据文件；`copyA.db`/`copyB.db`
   仅为一次性演练产物，可随时清理。

## 附件（均在 scratchpad，未提交 git）

- `copyA.db` / `copyB.db` — 归一后的两份工作副本
- `greyscale_snapshot.db` — 归一前 baseline 快照
- `runA_old_regime.log` / `runB_new_regime.log` — 两次 run() 的完整日志
- `diff_summary.json` — headline 数字机器可读版
- `diff_both_diff.csv` / `diff_both_same.csv` / `diff_a_only.csv` / `diff_a_only_traced.csv` / `diff_b_only.csv`
- `both_diff_term_check.txt` — both-diff 逐部件 lookup 核实明细
- `developer_registry_suggestions_20260707_231935.csv` — 新口径产出的登记册补录建议
- `developer_norm_misses_20260707_231935.csv` — 新口径 miss 明细
