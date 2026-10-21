# Codex 季度分拣管线 runbook

> 面向对象：三个月后已完全忘记细节的操作者。每步给可直接复制的命令。
> 代码位置：`codex_review/`（`quarterly.py` 薄编排器 / `export_queue.py` / `ingest_verdicts.py` / `apply_actions.py` / `schemas.py` / `ledger.py`）。
> 日常操作走 `quarterly.py` 三条子命令（export/ingest/apply）；需要单独调参/排障某个任务时，见文末"附录：单任务粒度命令"。
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

一条命令依次导出 T1（漏合并候选对）/ T2（范围复核）/ T3（实体归属补录），产物落在 `codex_review/runs/<quarter>/`：每个任务一个 `queue_T*.json`（喂给分拣者的数据）+ `guide_T*.md`（分拣规则说明），收尾自动生成 `CODEX_README.md`（交给 Codex 的唯一入口，见步骤 2）。

```bash
uv run --no-sync python -m codex_review.quarterly export --quarter 2026Q3
```

- 默认参数即可覆盖 T1/T2 常规场景（`--db` 默认指向主库；T1 相似度阈值固定 85）。
- **T2 `--t2-extra-ids`**（可选）：逗号分隔的 project_id 列表，追加进固定清单（用于审计 CSV 里没写"范围复核"备注、但计划里点名要复核的个案），例如 `--t2-extra-ids ISR-HB-0001,EGY-PV-0002`。**每季度导出前先查一次该项目是否仍 `excluded=0`**（已被排除就不用再加，加了也无害，见附录检查命令）。
- **T3 misses CSV 已自动选最新**：`quarterly export` 会在 `supporting_docs/` 下 glob `entity_reparse_misses_*.csv` 按 mtime 自动取最新一份并在日志里记录用了哪份，无需手工找、无需传参。
- 某任务本轮队列为 0 条时正常继续（不算错），`CODEX_README.md` 里会标注该任务"本轮无活，跳过"。
- 产物：`queue_T{1,2,3}.json` + `guide_T{1,2,3}.md` + `CODEX_README.md`，均在 `codex_review/runs/<quarter>/`。

---

## 步骤 2：分拣（交给 Codex，不在本机自动跑）

把整个 `codex_review/runs/<quarter>/` 文件夹交给 Codex，一句话即可——文件夹里的 `CODEX_README.md` 已经写清楚了任务、文件清单、硬规则、输出信封格式，Codex 只需要读它照做。

```bash
codex exec "读 codex_review/runs/2026Q3/CODEX_README.md 并照做"
```

- Codex 会依次读各任务的 `guide_T*.md`、逐条判断 `queue_T*.json` 里的 item，把结果写成 `verdicts_T*.json`（信封结构：`task`/`quarter`/`verdicts` 列表，字段以 guide 内 JSON 示例为准，**禁止任何额外字段**）。
- **T3 需要联网核实来源**：`classification` 属于 `new_group`/`alias_of_existing`/`subsidiary_of_existing`/`subsidiary_of_new` 时，`source_urls` 必须至少 1 条真实可达的 http(s) 链接（ingest 阶段会校验协议头，无法验证是否真实存在，分拣者需自行核实后再填）。
- verdicts 文件放回 `codex_review/runs/<quarter>/verdicts_T*.json`（quarterly ingest 默认从这里读）。
- 允许分批完成：verdicts 里只含已判条目即可，未判条目下季度自然重新入队。

---

## 步骤 3：回收校验（quarterly ingest）

对存在 `verdicts_<task>.json` 的任务依次校验+分级信任+抽检（缺文件的任务自动跳过并提示，不算错）。先 dry-run 看汇总，确认统计数字合理后再 `--apply`。

```bash
# dry-run（不写台账，不生成 apply_plan）
uv run --no-sync python -m codex_review.quarterly ingest --quarter 2026Q3

# 确认无误后 --apply（写台账 + 生成 apply_plan_T*.json + manual_check_T*.csv）
uv run --no-sync python -m codex_review.quarterly ingest --quarter 2026Q3 --apply
```

- 报告：`codex_review/runs/<quarter>/ingest_report_T{1,2,3}.md`（每个已处理任务各一份，覆盖式）；命令行结尾打三任务汇总表（有效/自动收/gated/uncertain）。看汇总确认：拒收条数是否异常、自动收/改数据(gated)条数是否符合预期。
- **分级信任**（不用人工看的自动收 vs 要落库改动的 gated）：
  - T1：`same_project=False` → 自动收；`True` → gated（要 merge）。
  - T2：`action=keep` → 自动收；`exclude`/`downgrade_non_project` → gated。
  - T3：`classification` 为 `not_chinese`/`not_company` → 自动收；`uncertain` → 跳过（下季度自然重新入队，不占台账）；其余（认定为集团/别名/子公司）→ gated。
- `--apply` 后自动：
  - 台账写入自动收 + gated 各条目（`accepted_no_change` 或等 apply_actions 落库后置 `applied`）。
  - 生成 `apply_plan_T{task}.json`（gated 条目全文，供 apply_actions 消费）。
  - 生成 `manual_check_T{task}.csv`：从 gated 条目里按 `--sample-rate`（默认 0.15，即 15%）确定性抽样，供人工抽检复核。**头 2-3 个季度按 15% 抽检；分拣质量稳定后可考虑调低比例**（用 `--sample-rate 0.1` 等，需人工判断，不建议擅自降）。
- 任一任务信封不符等致命错误 → 整体非零退出（其余任务仍会跑完，失败的在汇总里标 FAILED）。

---

## 步骤 4：落库（quarterly apply）

**先人工复核 `manual_check_T{task}.csv`**（抽检 15% 的 gated 条目是否判得对），确认无误后再跑。对存在 `apply_plan_<task>.json` 的任务依次执行（缺文件的任务自动跳过）：

```bash
# dry-run（不写库，只算待执行集合）
uv run --no-sync python -m codex_review.quarterly apply --quarter 2026Q3

# 确认统计无误后 --apply（备份 + 单事务写库 + 幂等自检 + 重放检查）
uv run --no-sync python -m codex_review.quarterly apply --quarter 2026Q3 --apply
```

- `--apply` 前自动对主库做 WAL-safe 备份（`<db>.bak.<时间戳>`），备份后做三重断言（文件存在/非 0 字节/`canonical_projects` 行数一致），任一失败直接抛异常终止。
- `--apply` 后自动跑两项自检，任一失败该任务在汇总里标 FAILED，需人工介入：
  1. **外键+前缀一致性自检**（`chinese_entities.event_id` 都在 `project_events`；`project_events.canonical_id` 都在 `canonical_projects`；事件 id 前缀去掉 `-NN` 后等于 canonical_id）。
  2. **重放检查**：对同一 `apply_plan` 再算一遍待执行集，断言 `done==0`（全部命中幂等/冲突跳过），证明本次 apply 是完整、幂等的。
- 报告：`codex_review/runs/<quarter>/apply_report_T{1,2,3}.md`（每个已处理任务各一份，覆盖式）；命令行结尾打三任务汇总表（done/skipped/conflict/error）。

### T3 特殊：不动库，产出 `registry_additions_<quarter>.json`

T3 的 apply 不直接写数据库/registry，只是把 gated verdicts 按 `new_group`/`additions_to_existing_groups` 两类整理进 `codex_review/runs/<quarter>/registry_additions_<quarter>.json`（`quarterly apply` 已包含 T3，无需单独跑）。之后需要人工三步（**不代劳，用户自己做**）：

1. 把 `registry_additions_2026Q3.json` 里的内容合并进 `supporting_docs/company_hierarchy_draft_*.json`（人工编辑合并，判断是否真要收录）。
2. 重跑 `uv run supporting_docs/build_hierarchy_v2.py` 生成 v2 基线；当前运行时是 `company_hierarchy_v3.json`，新增内容须按 `company_hierarchy_v3_changes.json` 的口径合并进 v3（或另起新版本），不要直接覆盖 v3。
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
A：`quarterly export` 已经自动处理了 misses CSV（glob 取最新，见步骤 1）；`--t2-extra-ids` 里点名的个案（如 ISR-HB-0001）可能在某季度已被 T2 处理并排除（`excluded=1`），这时不需要再加，加了也无害（`export_t2` 会正常跳过已排除的），但每次显式检查更清楚、避免不必要的队列条目。

---

## 附录：单任务粒度命令

日常操作用步骤 1/3/4 的 `quarterly` 三条子命令即可。以下是底层被调用的单任务命令，用于**调参/排障**某个具体任务时单独重跑（例如只想重跑 T1、或要传步骤 1 默认值没覆盖到的冷门参数）。

### 导出（对应 `quarterly export` 内部依次调用的三条命令）

```bash
# T1：漏合并候选对（--db 默认主库；--threshold 默认 85，即 token_sort_ratio > 85 才入队）
uv run --no-sync python -m codex_review.export_queue --task T1 --quarter 2026Q3

# T2：范围复核（三个触发来源合并去重，优先级 fixed_list > mf_boundary_scan > no_chinese_entity_scan）
uv run --no-sync python -m codex_review.export_queue --task T2 --quarter 2026Q3 --extra-ids ISR-HB-0001

# T3：实体归属补录（--misses-csv 必须显式指定，quarterly export 会自动选最新，单独跑此命令需自己找）
uv run --no-sync python -m codex_review.export_queue --task T3 --quarter 2026Q3 --misses-csv supporting_docs/entity_reparse_misses_<最新时间戳>.csv
```

- T2 `--extra-ids` 追加前建议先查一次该项目是否仍 `excluded=0`：
  ```bash
  uv run --no-sync python -c "
  import sqlite3
  conn = sqlite3.connect(r'file:E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db?mode=ro', uri=True)
  print(conn.execute(\"SELECT excluded FROM canonical_projects WHERE id='ISR-HB-0001'\").fetchone())
  "
  ```
- T3 `--misses-csv` 手工找最新：`ls -t supporting_docs/entity_reparse_misses_*.csv | head -1`（worktree 里的 `supporting_docs/` 是独立副本，按 worktree 自己目录里最新的来）。
- T1/T2 的 `--db` 默认指向主库；T3 的 `--registry` 默认 `supporting_docs/company_hierarchy_v3.json`。
- 台账幂等（`is_settled(ledger, key)` 为真的条目不再入队）三任务通用。

### 回收校验（对应 `quarterly ingest` 内部依次调用）

```bash
uv run --no-sync python -m codex_review.ingest_verdicts --task T1 --quarter 2026Q3
uv run --no-sync python -m codex_review.ingest_verdicts --task T1 --quarter 2026Q3 --apply
```

把 `--task T1` 换成 `--task T2` / `--task T3` 即可单独跑某个任务。

### 落库（对应 `quarterly apply` 内部依次调用）

```bash
uv run --no-sync python -m codex_review.apply_actions --task T1 --quarter 2026Q3
uv run --no-sync python -m codex_review.apply_actions --task T1 --quarter 2026Q3 --apply
```

把 `--task T1` 换成 `--task T2` / `--task T3` 即可单独跑某个任务（T3 见步骤 4"T3 特殊"一节）。

## 附：developer 登记册季度维护（2026-07-08 起常设）

增量流水线跑完后，收集两类 CSV 喂人工/campaign 复核入册（`developer_registry.json`，入册后跑 `uv run --no-sync pytest tests/test_developer_registry.py -q` 契约自检）：
- `supporting_docs/developer_registry_suggestions_*.csv` —— 2B-3 的 LLM 降级合并建议（未写库）
- `supporting_docs/developer_norm_misses_*.csv` —— 查表未命中长尾（含 ASCII 外企；EN 缺口同源）
入册后下一轮增量 2B-3/2B-4 自动生效；如需存量立即生效，手动重跑 `normalize_developers --apply` + `backfill_entity_en --apply`（均幂等）。
