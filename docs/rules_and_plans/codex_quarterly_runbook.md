# Codex 季度分拣管线 runbook

> 面向对象：三个月后已完全忘记细节的操作者。每步给可直接复制的命令。
> 代码位置：`codex_review/`（`export_queue.py` / `ingest_verdicts.py` / `apply_actions.py` / `schemas.py` / `ledger.py`）。
> 所有命令在**仓库根**（`E:\Projects\BRI_RE_Database_v2`，或本 worktree 根）执行。

---

## 步骤 0：前提

- 增量流水线（s01a → s01b → s02a → s02b）已跑完，主库是最新态。跑法见 `docs/status/pipeline.md`。
- 主库路径（唯一权威库，只读消费）：
  ```
  E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db
  ```
- 所有命令一律用 `uv run --no-sync python -m codex_review.xxx ...`。
  **为什么必须 `--no-sync`**：根 venv 里有一个未声明进 `pyproject.toml` 的 s01b editable 安装，`uv sync`/`uv add` 会尝试重新解析依赖并可能破坏它；因此本管线的所有调用一律禁止 `uv add`/`uv sync`，只用 `--no-sync`。
- 主库可能被其他进程（回填 / s02b）占用。若命令报 `database is locked`：等 30 秒重试一次；仍锁则本季度导出先停下，不要硬等或强杀进程。

---

## 步骤 1：导出三队列

三条命令分别导出 T1（漏合并候选对）/ T2（范围复核）/ T3（实体归属补录）。产物落在 `codex_review/runs/<quarter>/`：每个任务一个 `queue_T*.json`（喂给分拣者的数据）+ `guide_T*.md`（分拣规则说明，从 `codex_review/guides/` 复制而来）。

### T1：漏合并候选对

```bash
uv run --no-sync python -m codex_review.export_queue --task T1 --quarter 2026Q3
```

- 默认参数即可（`--db` 默认指向主库；`--threshold` 默认 85，即 token_sort_ratio 相似度 > 85 才入队）。
- 逻辑：按 `(country_iso, category)` 分组两两比对，排除台账已定案的对；按 `both_sides_visible`（近似前端展示口径）优先、相似度降序排序。

### T2：范围复核

```bash
uv run --no-sync python -m codex_review.export_queue --task T2 --quarter 2026Q3 --extra-ids ISR-HB-0001
```

- 三个触发来源合并去重（同一 project_id 多来源命中时按优先级 fixed_list > mf_boundary_scan > no_chinese_entity_scan 取展示 trigger，备注拼接）：
  1. **fixed_list**：读 `--audit-csv`（默认 `E:\Projects\BRI_RE_Database_v2\supporting_docs\numeric_audit_2026-07-04.csv`，该文件只在主工作区，worktree 无副本），取"附带备注"含"范围复核"的行。
  2. **no_chinese_entity_scan**：全部事件都没有具名中企实体的项目。
  3. **mf_boundary_scan**：project_name 或任一事件 project_summary 命中关键词 `锂矿`/`电解铝`/`蓝氨`。
- `--extra-ids`：逗号分隔的 project_id 列表，追加进固定清单（用于审计 CSV 里没写"范围复核"备注、但计划里点名要复核的个案）。**每季度导出前先查一次该项目是否仍 `excluded=0`**（见下方检查），已被排除（`excluded=1`）就不用再加。
  ```bash
  uv run --no-sync python -c "
  import sqlite3
  conn = sqlite3.connect(r'file:E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db?mode=ro', uri=True)
  print(conn.execute(\"SELECT excluded FROM canonical_projects WHERE id='ISR-HB-0001'\").fetchone())
  "
  ```
- 台账幂等：`is_settled(ledger, key)` 为真的条目不再入队；`ledger.json` 不存在时视为空台账（正常，见步骤 0 之后的"常见问题"）。

### T3：实体归属补录

```bash
uv run --no-sync python -m codex_review.export_queue --task T3 --quarter 2026Q3 --misses-csv supporting_docs/entity_reparse_misses_<最新时间戳>.csv
```

- **`--misses-csv` 必须显式指定**：默认值是代码里写死的一个历史文件名（可能已过期），每次导出前先找当前最新的一份：
  ```bash
  ls -t supporting_docs/entity_reparse_misses_*.csv | head -1
  ```
  worktree 里的 `supporting_docs/` 是独立副本，跟主工作区的不是同一批文件——按 worktree 自己目录里最新的来。
- `--registry` 默认 `supporting_docs/company_hierarchy_v2.json`（集团层级库），用于过滤已收录的名字（registry 覆盖的名字不再入队）。
- 台账幂等同 T1/T2。

### 产物

每个任务导出后在 `codex_review/runs/<quarter>/` 下生成：
- `queue_T{1,2,3}.json` —— 交给 Codex/分拣者的实际输入（含全部上下文，无需另查库）。
- `guide_T{1,2,3}.md` —— 分拣规则+输出格式说明（随代码库版本变化，每次导出都会覆盖复制最新版）。

---

## 步骤 2：分拣（用户侧，不在本机自动跑）

把 `queue_T*.json` + `guide_T*.md` 交给分拣者（Codex / Claude / 人工均可——**数据契约不绑定供应商**，guide 里的字段说明和 schemas.py 是唯一权威）。分拣者需读 guide 里的规则，逐条判断，把结果写成 `verdicts_T*.json`（信封结构：`task`/`quarter`/`verdicts` 列表，字段见 `codex_review/schemas.py` 对应 `T{1,2,3}Verdict`）。

`codex exec` 一行示例（T1 为例，其余任务同理换 T2/T3）：

```bash
codex exec "读 codex_review/runs/2026Q3/guide_T1.md 里的判定规则，逐条处理 codex_review/runs/2026Q3/queue_T1.json 里的每个 item，把判定结果按 guide 里的信封格式写到 codex_review/runs/2026Q3/verdicts_T1.json"
```

- **T3 需要联网核实来源**：`classification` 属于 `new_group`/`alias_of_existing`/`subsidiary_of_existing`/`subsidiary_of_new` 时，`source_urls` 必须至少 1 条真实可达的 http(s) 链接（ingest 阶段会校验协议头，无法验证是否真实存在，分拣者需自行核实后再填）。
- verdicts 文件放回 `codex_review/runs/<quarter>/verdicts_T*.json`（ingest_verdicts 默认从这里读）。

---

## 步骤 3：回收校验（ingest_verdicts）

先 dry-run 看报告，确认统计数字合理后再 `--apply`。

```bash
# dry-run（不写台账，不生成 apply_plan）
uv run --no-sync python -m codex_review.ingest_verdicts --task T1 --quarter 2026Q3

# 确认无误后 --apply（写台账 + 生成 apply_plan_T1.json + manual_check_T1.csv）
uv run --no-sync python -m codex_review.ingest_verdicts --task T1 --quarter 2026Q3 --apply
```

T2/T3 同理，把 `--task T1` 换成 `--task T2` / `--task T3`。

- 报告：`codex_review/runs/<quarter>/ingest_report_T{1,2,3}.md`（覆盖式，dry-run 和 apply 都会生成）。看这份报告确认：拒收条数是否异常、自动收/改数据(gated)条数是否符合预期。
- **分级信任**（不用人工看的自动收 vs 要落库改动的 gated）：
  - T1：`same_project=False` → 自动收；`True` → gated（要 merge）。
  - T2：`action=keep` → 自动收；`exclude`/`downgrade_non_project` → gated。
  - T3：`classification` 为 `not_chinese`/`not_company` → 自动收；`uncertain` → 跳过（下季度自然重新入队，不占台账）；其余（认定为集团/别名/子公司）→ gated。
- `--apply` 后自动：
  - 台账写入自动收 + gated 各条目（`accepted_no_change` 或等 apply_actions 落库后置 `applied`）。
  - 生成 `apply_plan_T{task}.json`（gated 条目全文，供 apply_actions 消费）。
  - 生成 `manual_check_T{task}.csv`：从 gated 条目里按 `--sample-rate`（默认 0.15，即 15%）确定性抽样，供人工抽检复核。**头 2-3 个季度按 15% 抽检；分拣质量稳定后可考虑调低比例**（用 `--sample-rate 0.1` 等，需人工判断，不建议擅自降）。

---

## 步骤 4：落库（apply_actions）

**先人工复核 `manual_check_T{task}.csv`**（抽检 15% 的 gated 条目是否判得对），确认无误后再跑：

```bash
# dry-run（不写库，只算待执行集合）
uv run --no-sync python -m codex_review.apply_actions --task T1 --quarter 2026Q3

# 确认统计无误后 --apply（备份 + 单事务写库 + 幂等自检 + 重放检查）
uv run --no-sync python -m codex_review.apply_actions --task T1 --quarter 2026Q3 --apply
```

T2 同理换 `--task T2`（T2/T1 都会动库）。

- `--apply` 前自动对主库做 WAL-safe 备份（`<db>.bak.<时间戳>`），备份后做三重断言（文件存在/非 0 字节/`canonical_projects` 行数一致），任一失败直接抛异常终止。
- `--apply` 后自动跑两项自检，任一失败 CLI 以非零码退出（`sys.exit(1)`），需人工介入：
  1. **外键+前缀一致性自检**（`chinese_entities.event_id` 都在 `project_events`；`project_events.canonical_id` 都在 `canonical_projects`；事件 id 前缀去掉 `-NN` 后等于 canonical_id）。
  2. **重放检查**：对同一 `apply_plan` 再算一遍待执行集，断言 `done==0`（全部命中幂等/冲突跳过），证明本次 apply 是完整、幂等的。
- 报告：`codex_review/runs/<quarter>/apply_report_T{1,2,3}.md`（覆盖式）。

### T3 特殊：不动库，产出 `registry_additions_<quarter>.json`

T3 的 apply 不直接写数据库/registry，只是把 gated verdicts 按 `new_group`/`additions_to_existing_groups` 两类整理进 `codex_review/runs/<quarter>/registry_additions_<quarter>.json`。之后需要人工三步（**不代劳，用户自己做**）：

```bash
uv run --no-sync python -m codex_review.apply_actions --task T3 --quarter 2026Q3 --apply
```

1. 把 `registry_additions_2026Q3.json` 里的内容合并进 `supporting_docs/company_hierarchy_draft_*.json`（人工编辑合并，判断是否真要收录）。
2. 重跑 `uv run supporting_docs/build_hierarchy_v2.py`（把 draft 编译成正式 `company_hierarchy_v2.json`）。
3. 在 s01b 跑 `entity_reparse_maindb --apply`（主库 gated 操作，让新收录的集团/别名在主库实体名上生效）。

---

## 步骤 5：收尾

1. **一定**在 `docs/changes/2026-Www.md`（本周对应文件）追加一条：涉及模块 `codex_review`，做了什么（跑了哪个季度、T1/T2/T3 各多少条入队/自动收/gated）、为什么、影响了哪些文件/表。
2. 备份清理：`*.bak.<时间戳>` 按里程碑保留制清理（不是每次都删，重大里程碑前后的备份留着，中间高频的可以清）。判断标准与其他模块的备份清理一致，无需为 codex_review 单独定义新规则。
3. **若本季度改动（T1 merge / T2 exclude / T3 registry 补录）会影响网页展示**，走硬顺序，不可颠倒：
   ```
   en_backfill → uv run run_export（s03） → 部署（gh-pages）
   ```
   只做 T3 人工三步而未落库改动时，此步骤视具体情况判断是否需要（T3 影响的是 entity_reparse，若已跑 `entity_reparse_maindb --apply` 改了主库，仍需走 en_backfill → run_export → 部署）。

---

## 常见问题

**Q：主库被并行进程锁住（`database is locked`）怎么办？**
A：等 30 秒重试一次。仍然锁，说明有人在跑回填/迁移，当季度导出/落库先停下，不要硬等、不要强杀对方进程。

**Q：`ledger.json` 冲突或损坏怎么办？有备份策略吗？**
A：没有专门的备份策略——`ledger.py` 的 `save_ledger()` 用"写临时文件再 `os.replace`"的原子写法防止写入中途中断导致半截损坏，但不做历史快照。若文件整个损坏（如手工误改导致 JSON 解析失败），`load_ledger()` 会直接抛 `ValueError`（"台账文件损坏，无法解析 JSON"）。恢复方式：**从 git 历史恢复**（`codex_review/ledger.json` 是随每次 `--apply` 提交进版本库的，`git log -- codex_review/ledger.json` 找上一个可用版本 `git checkout <commit> -- codex_review/ledger.json`）。若多人并发跑导致内容冲突（同一 key 两次不同写入），以最后一次 `--apply` 为准，冲突条目人工核对 `decided_at`/`verdict_digest` 后决定是否需要重新判定。

**Q：为什么每次都要显式传 `--misses-csv`/检查 `--extra-ids`，而不能全靠默认值？**
A：`--misses-csv` 的默认值是代码里写死的历史文件名（当前季度产出的新文件不会自动被捡到）；`--extra-ids` 里点名的个案（如 ISR-HB-0001）可能在某季度已被 T2 处理并排除（`excluded=1`），这时不需要再加，加了也无害（`export_t2` 会正常跳过已排除的），但每次显式检查更清楚、避免不必要的队列条目。
