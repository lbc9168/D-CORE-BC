# pipeline · 流水线编排与批量采集

> 最后更新：2026-10-05（现有Workflow完成本地增量运行）

正常范围更新使用原`run_pipeline(PipelineConfig(...))`；显式raw_ids只读映射URL并传同一范围给S02A，cached_results_path复用来源指纹匹配结果，经原writer/guard，不迁移隔离ID。

本次2026-09-22—09-30含首尾正常增量已沿既有Workflow运行到生产入库及本地导出。新增54项目、60事件、112关系，生产canonical/event/关系4811/5680/6917。1身份协调任务阻断2源行，另16条pending_review未入正式业务，最终status=partial。现有成功及13篇结果缓存复用，最终文章级技术失败0；重复原入口请求0且四库SHA256不变。详见`output/incremental-20260922-20260930-20261004/report.md`。

本批独立SDK请求账本累计444（提取320/翻译46/去重78），5请求失败，可见usage 1,772,569 token；旧274/40账本未变。用户后续明确要求直接运行，按现有重试续跑；一次SSL中断事务回滚后复用响应完成。原可选全库地点/业主/残余翻译LLM回填关闭以限定范围，确定性回填保留；S03禁止省份模型调用。

当前本地metadata.last_updated=2026-09-30（只表示最新已入库事件日期）；projects_flat3745、TX474、MF161、事件CSV3689行。默认发电/储能1600、输变电/制造业492。S03离线省份缓存导出完成无警告，npm build及本地地图/表格检查通过；预览http://127.0.0.1:5178/，未推送或远程部署。已发现药厂/LAB两条普通工业项目误归MF并进入网页/CSV，以及伊塞克湖垃圾发电疑似漏并；保留检查证据，尚未另起纠错。备份、ID/URL映射、原始响应和剩余待办见`output/incremental-20260922-20260930-20261004/report.md`。

---

## 职责

`pipeline/` 用一条命令把各阶段子项目串成完整流水线（纯 Python 顺序编排，无 crewAI）。
阶段之间靠 SQLite 文件接力（不依赖内存状态），可按需跳过任意阶段。s01b 内部实体处理采用“S4 逐字抄原文名 → 写库前 entity_normalize 确定性查表解析（company_hierarchy_v3.json）”两步，只负责规范身份补充；模型支持但名录未命中的参与关系保留原文名及空ID；s02b 不再二次模糊改写该结果。参与资格由S4/S34显式语义证据判断，不由查表命中决定。

2026-09-17 企业身份修复及受控名录召回已接入根目录 main 工作区。S1 名称线索只用于继续提取，S4/S34 仍核验目标项目参与。普通增量按已处理URL去重，不会自动重算历史无中企/待核实记录；补录刷新与历史定向试跑见 [s01b.md](s01b.md)。本地13个历史误排除项目已恢复并重导出，试跑发现的其他待核实结果仍保持隔离。

企业修复验证入口 `scripts/oneoff/entity_recovery_validation.py` 只在新建output目录执行固定回放/最多10篇真实调用及S01B写库→S02A合并，保存源库不变性和重复全行不变证据。扩大运行以批次可完成、技术失败可恢复、错误不冒充排除及写库可核验为门槛；个别语义待复核不阻塞其他记录。历史更正须有备份和明确旧新ID映射；不得删除旧review判断或清空import标志绕过去重。该验证只涉及历史隔离结果；当前生产与本地导出以本文件上方最新批次摘要为准。验证结果见 `output/entity-recovery-20260923/`。

---

## 当前功能

### 顺序编排（`src/bri_pipeline/flow.py`）

核心是 `run_pipeline(cfg: PipelineConfig)`，按下列顺序执行：

```
阶段一A 新闻采集    ← cfg.skip_s01a=True 时跳过（→ s01a.run_single）
   ↓
阶段一B AI 提取     ← 始终执行（→ s01b.run_bulk_extract，透传raw/projects/review及可选raw_ids）
   ↓
阶段一B-2 企业待补录清单 ← cfg.run_entity_intake=True（默认）；只读导出、不自动更新名录
   ↓
阶段二A 去重合并入库 ← 始终执行（→ 直调 DuplicateMergerTool()._run(input_db, db_path)，绝对路径）
   ↓
阶段二B 实体别名候选（只读） ← cfg.run_entity_suggestions=False 时跳过（默认跳过）
   ↓
阶段二B-2 地点归一化 ← cfg.run_location_normalize=False 时跳过（写 location_norm）
   ↓
阶段二B-3 业主归一化 ← cfg.run_developer_normalize=False 时跳过（登记册查表前置归一 project_developer；LLM 降级为补录建议 CSV）
   ↓
阶段二B-4 英文名回填 ← cfg.run_en_backfill=False 时跳过（registry canonical_en > 缓存 en_abbr > en 三档口径重算 *_en 列，miss 只报告）
   ↓
阶段二B-5 TX/MF 专用列回填 ← cfg.run_txmf_backfill=False 时跳过（txmf_spec_parser 确定性解析 details/summary，fill-if-null 回填 5 稀疏列；流水线模式只打印 misses 计数，CSV 由手跑 CLI 带 --out 产出）
   ↓
阶段二B-6 剩余名称翻译回填 ← cfg.run_residual_translate=False 时跳过（2B-4 miss 的长尾中文名：registry>主缓存>残余缓存>LLM 批翻，写 residual_translation_cache.json，fill-if-null 只填 *_en 空/含中文行）
   ↓
阶段二B-7 能源事实/储能容量/光伏形态回填 ← cfg.run_storage_subcat_backfill=False 时跳过（严格上下文，停止新增“综合能源/光储”）
   ↓
阶段二B-8 能源分类影子汇总 ← cfg.run_energy_taxonomy_shadow=False 时跳过（默认写 shadow 表并生成内部 CSV/JSON；正式类别/子类/ID 不变）
   ↓
阶段二C 交叉验证    ← cfg.run_cross_verification=True 时启用（默认关闭）
   ↓
阶段二E 外部参考发现 ← cfg.run_source_finder=True（默认）；持久队列处理新增/变更及到期事件，累计授权预算内调用，数据库保存参考与合格容量补空
   ↓
阶段三   导出       ← cfg.run_export=True 时启用（默认关闭）
```

> 「串某几段」= 构造对应的 `PipelineConfig` 再调 `run_pipeline(cfg)`；「单独跑某段」进对应子项目 `uv run sNN_xxx`。

### 批次结果与恢复

- S01B支持`cached_results_path`（`s01b-recorded-results-v1`），先跳过成功URL，缓存命中严格比较URL/标题/原发布日期/正文SHA256，来源不符为技术失败且不回退模型；未命中保持原提取。返回`reused_cached_articles`及结构化`completed/partial`、文章ID/URL、失败清单、输出路径及唯一JSON报告；技术失败不写业务排除表。成功URL保持去重，重跑只补失败文章；`raw_ids`可精确限定范围，`max_extract_workers`控制文章并发，跨批次串行写共享库。
- 上游库/关键表与路径在阶段边界校验，不能用错误路径或空建库冒充产物。备份或写库失败立即停止，已有工作可保留续跑。合法无命中/零可发布记录正常退出。
- S02A明确完成结果与计数；去重确认或冲突仲裁调用失败不当作“不同项目”，不继续标记已导入。默认按代码/模型版本保存批内确认缓存，续跑复用已成功判断。`partial_overlap`等未决身份保存在提取库协调队列，实际跳过业务写入，源行保持未导入且正常重跑不再挑选；同批正常项目继续。主库写入、协调任务及源导入标记使用同一双库事务。S02A业务partial不会中断S02B/S03，但pipeline总体保持partial；协调定向恢复见[s02a.md](s02a.md)。
- 可选摘要失败使用结构化事实降级，不重做核心提取。字段警告、技术失败和业务排除分开；省份/翻译/Excel等非关键步骤失败可继续，但总体为partial并保存诊断。
- 配置`run_export=True`时S03使用本轮`db_path`。非默认主库必须明确`export_dir`及`export_web_dir`，省份缓存可指定`export_cache_path`；`export_allow_model_calls=False`允许离线导出。`export_external_references`（显式元数据快照）与`export_reference_config`（绑定本轮源库的指针）互斥并透传S03。正式源库省略这两项时自动使用`supporting_docs/external_references_config.json`；隔离源库不会自动绑定正式快照。快照文件/配置失效会在S03重建前失败；单条过期、身份变化或非中文证据被隐藏，导出阶段报告含明确原因计数。`run_export_only`同样继承S03正式源库指针；不触发找源API。旧S02C入口尚未参数化，自定义主库开启它会明确拒绝。
- 增量日期默认读取`metadata.last_updated`次日，截止默认今天；日志区分请求区间与尚未证实的覆盖范围，不用“程序返回”冒充逐日完整抓取。

### 定向历史处理

先用`run_bulk_extract(raw_ids=[...], raw_news_db_path=..., projects_db_path=隔离新库, review_db_path=隔离复核库)`重提取，再在同一隔离路径运行S02A；失败后使用相同目录/ID范围续跑，成功URL不重复调用。正式更正由`python -m bri_pipeline.historical_reprocess plan/apply`处理，详见[s02a.md](s02a.md)。不能把隔离新ID直接覆盖生产ID。

### 支持路径

```
路径一（完整）：    s01a → s01b → s02a → s02b
路径二（跳过爬虫）： s01b → s02a → s02b（使用已有 raw_news.db）
```

### PipelineConfig 参数（`@dataclass`）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `skip_s01a` | bool | False | 跳过爬虫（用已有 raw_news.db，即路径二） |
| `run_entity_intake` | bool | True | S01B后读取input_db，按候选企业汇总原文证据与受影响行，输出同级reports/entity_intake_时间戳.json |
| `run_entity_suggestions` | bool | False | 是否只读导出中方实体疑似别名审核 CSV |
| `run_entity_normalize` | bool | False | 旧配置名兼容位；True 也只生成建议，不再写库 |
| `run_location_normalize` | bool | True | 是否执行地点归一化（写 location_norm） |
| `run_developer_normalize` | bool | True | 是否执行业主归一化（project_developer 部件级） |
| `run_en_backfill` | bool | True | 是否执行英文名回填（缓存口径同步 *_en 列） |
| `run_txmf_backfill` | bool | True | 是否执行 TX/MF 专用列回填（s01b txmf_backfill，fill-if-null） |
| `run_residual_translate` | bool | True | 是否执行剩余名称翻译回填（长尾名 LLM 翻译+残余缓存，fill-if-null） |
| `run_storage_subcat_backfill` | bool | True | 是否执行严格储能事实与光伏形态回填（不新增 HB/光储） |
| `run_energy_taxonomy_shadow` | bool | True | 是否执行版本化影子分类并生成内部差异报告 |
| `run_cross_verification` | bool | False | 是否执行 s02c 交叉验证（预留） |
| `run_source_finder` | bool | True | S02E常态找源；未授权/耗尽预算保留待处理并继续导出 |
| `source_finder_config` | str/None | None | 默认生产绑定supporting_docs/source_finder_config.json；隔离库须显式配置 |
| `run_export` | bool | False | 是否执行 s03 导出（预留） |
| `start_date` / `end_date` | str/None | metadata次日 / 本地今天 | 显式指定时保留；无新增日期不抓取，仍可继续待处理数据 |
| `raw_news_db_path` | str | s01a/data/raw_news.db | s01a 输出 / s01b 输入 |
| `input_db` | str/None | None | 旧配置别名；与projects_db_path必须一致，不能从另一库接力 |
| `db_path` | str | s02a/data/db/bri_re_database.db | s02a 输出（主库），同时作 s02b 入参 |

---

## 如何运行

```bash
cd pipeline
uv sync                   # 首次安装（自动以 editable 安装所有本地子项目）

uv run run_pipeline       # 路径一：完整流水线（s01a→s01b→s02a→s02b）
uv run run_from_s01b      # 路径二：跳过爬虫，用已有 raw_news.db
uv run run_export_only    # 仅 s03 导出（数据库已有时）
```

根目录快捷命令（等效）：

```bash
make pipeline        # = uv run run_pipeline
make from-s01b       # = uv run run_from_s01b
make export-only     # = uv run run_export_only
./bri.sh pipeline    # bash 脚本（Windows Git Bash / Linux）
```

### 批量采集 / 提取入口（历史补录用，可单独跑）

```bash
cd s01a_news_crawler   && uv run run_bulk_crawl     # 按月顺序爬取 + checkpoint 接力
cd s01b_project_extractor && uv run run_bulk_extract # 并发调用 Gemini 批量提取（默认 4 并发）
```

---

## 输入 / 输出

- **输入**：goalfore.cn 新闻页（s01a 联网抓取）或已有的 `s01a_news_crawler/data/raw_news.db`。
- **输出**：逐阶段 SQLite 文件接力，最终主库 `s02a_duplicate_merger/data/db/bri_re_database.db`；若启用 `run_export`，则导出到 `s03_data_exporter/data/output/` 并同步前端。

---

## 关键依赖与约束

1. 首次 `uv sync` 需要各子项目目录已存在（以 editable 模式安装）。
2. 根目录 `.env` 需包含 `GEMINI_API_KEY`。
3. 路径二（`run_from_s01b`）需要 `s01a/data/raw_news.db` 已存在。
4. s02a 段直调 tool 用**绝对路径**（不走 `s02a.run()`，因其写死相对路径会按 pipeline 的 CWD 写错文件）。
5. `implementing_entity` 正式归类只允许登记册确定性查找；旧式“模糊候选 → LLM → Union-Find → 全表 UPDATE”已禁用。未命中值透传并进入审核建议，不得在增量更新中重评历史实体。
6. **主库原地回填后的部署顺序（硬约束）**：凡对主库 `chinese_entities` 做过实体回填
   （如 `entity_reparse_maindb --apply`，其规则为「CN 变 → 对应 `*_en` 置 NULL」）后，
   部署必须按 **en_backfill → run_export(s03) → 部署（gh-pages）** 顺序执行，
   不得跳过 en_backfill 直接导出——回填后主库英文名大面积为空
   （2026-07-06 实测 `implementing_entity_en` 空 3660/6519 ≈ 56%、
   `subsidiary_name_en` 缺 666），先导出/部署会把空英文名发布到前端。
   en_backfill 单独跑：`cd s02b_entity_normalizer && uv run backfill_entity_en`
   （或走 pipeline `run_en_backfill=True` 段）。
