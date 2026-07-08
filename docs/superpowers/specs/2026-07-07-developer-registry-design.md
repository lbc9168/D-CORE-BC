# Developer 名称登记册与归一化统一设计（spec）

日期：2026-07-07 · 状态：**已实施上线（2026-07-08，Task1-8 全过审，存量回填落库；实施明细见 docs/changes/2026-W28b.md）**
决策人：用户 · 起草：Claude（本 spec 是唯一权威口径，实施计划=docs/superpowers/plans/2026-07-07-developer-registry.md）

## 1. 背景与动机（真库实测 2026-07-07）

- `project_events.project_developer` 非空 4,795 事件；按分隔符 `[;；、，,/]` 拆出 **3,656 个 distinct 部件**（频次≥5 共 153 个、≥3 共 315 个）；28% 部件为纯英文（外企名直接坐在"中文"列里）。
- `project_developer_en` 已存在但 **2,930/4,795（61%）为空**——列有名无实。
- 现行 2B-3（`s02b_entity_normalizer/developer_normalizer.py`）机制 = 每轮"字符串相似候选对 → LLM 保守确认 → Union-Find → 保守重建"：**判定不沉淀**（同一变体每轮增量重新问 LLM），且**结构性做不了跨语言归一**——`Masdar`(38次) 与 `阿布扎比未来能源公司`(36次) 相似度为 0，永远成不了候选对。
- 已知高频讹变：`中国能源海外投资有限公司`(121次)=电建海投名称讹变，company_hierarchy_v2 已判定过，developer 侧应复用而非重判。

## 2. 拍板决策（2026-07-07，全部已定）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 写回策略 | **就地改写** `project_developer`（沿 2B-3 现行契约，.bak+would-change CSV 可审）；同步写 `project_developer_en`。**不加新列** |
| D2 | 母子公司口径 | **同一法人的写法变体才归一（含中英跨语言/讹变/简称）；母子公司永不合并**。登记册 `parent_cn` 仅标注，不参与归并 |
| D3 | 收录范围 | **中外企业都收**（外企是 developer 大头且跨语言变体只有登记册能治） |
| D4 | 首轮种子门槛 | **频次≥5，共 153 名**（长尾走季度 miss 循环） |
| D5 | 架构 | **方案一：登记册前置查表 + LLM 降级为补录建议器**（见 §3） |
| D6 | 与中企归一的关系 | **共享查表引擎、保持两步**：引擎按两类归一通用设计，各持登记册、各自备份报告；**本期只接 2B-3**——中企 2B 的 entity_normalize 已是确定性查表（同等能力），维持现状，将来迁移到共享引擎属独立重构。pipeline 步骤数与顺序不变 |

## 3. 架构

```
project_developer 原值
  └─ split_developer()（沿现行分隔符，JV 各部件独立处理）
       └─ 共享查表引擎 lookup(part)
            ① developer_registry.json：exact / alias 命中（含跨语言别名）
                 → (canonical_cn, canonical_en)  确定性重写
            ② 中文部件续查 company_hierarchy_v2.json 别名表（只读复用）
                 → 该别名对应的规范**子公司名**（绝不上卷母公司）；EN 走 ③ 缓存
            ③ 未命中 → 透传原值 + miss 记录
  └─ rebuild()（沿现行"部件有变才改"保守重建）→ 写回 CN 列；同映射拼 EN 列
未命中高频候选 → 现行 LLM 保守确认照跑，但结果不再直接写库，
  改产「登记册补录建议 CSV」→ 人工/campaign 复核 → 入册 → 下轮查表生效
```

- **共享查表引擎**：模块放 `s02b_entity_normalizer/company_lookup.py`（两个消费方 2B-3/2B-4 都在 s02b 包内，根级模块无法被其 console script 导入——计划期修正）；**数据文件在根目录** `developer_registry.json`（与 enums_mapping.json 同级，单一可信源），引擎经 `parents[3]` 定位（en_backfill `_ROOT` 同款先例）。纯函数查表：exact→alias→透传+miss 日志。中企 2B 现行 `entity_normalize` 不改动（将来如想迁移到共享引擎属独立重构，非本设计范围）。
- **hierarchy 只读复用**：引擎直接读 `supporting_docs/company_hierarchy_v2.json` 数据文件（别名→规范子公司名映射），**不 import s01b 代码、不改该文件**（它是 eval 版本哈希资产）。
- 2B-3 的 LLM 环节保留原 prompt 与保守口径，仅输出目的地从"写库"改为"补录建议 CSV"。

## 4. 登记册 `developer_registry.json`（根目录，与 enums_mapping.json 同级）

```json
{
  "_meta": {"version": 1, "generated": "YYYY-MM-DD", "source": "campaign_2026-07"},
  "companies": [
    {
      "canonical_cn": "阿布扎比未来能源公司",
      "canonical_en": "Masdar",
      "aliases": ["Masdar", "马斯达尔", "Abu Dhabi Future Energy Company"],
      "is_chinese": false,
      "parent_cn": null,
      "note": "可选：证据/判定备注"
    }
  ]
}
```

规则：
- `canonical_cn` 必填；无常用中文名的外企直接用英文名充当（如 Recurrent Energy），保证 CN 列总有确定值。`canonical_en` 必填。
- alias 全册唯一（含 canonical 本身视作自身 alias）；载入时冲突断言（同 hierarchy v2 先例）。
- 中企条目原则上**不入本册**（走 hierarchy 复用），仅当 hierarchy 未收且 developer 语境需要时才收，且 `is_chinese: true` 并在 note 说明为何不进 hierarchy。
- Pydantic 载入校验（编程规则 #6）。

## 5. EN 写回优先级（防两工具乒乓，关键一致性规则）

**registry `canonical_en` > 翻译缓存（company_name_cache）> 留空+miss 报告**。
- **EN 单一写入方原则（计划期修正，优于原两处写入设计）**：2B-3 只写 CN 列；`project_developer_en` 的唯一写入方=2B-4 `en_backfill`（其 developer 档加 registry 最高优先档，含纯 ASCII 值查册命中直填——现行 ascii_skip 是 61% 空值的一大来源）。流水线顺序 2B-3→2B-4 天然衔接；单独跑 2B-3 后须跟跑 en_backfill（与既有部署硬顺序一致）。乒乓问题从结构上消失。

## 6. 落地顺序（每步独立可停、可回退）

1. **纯加法**：`developer_registry.json`（空壳+schema）+ 根级查表引擎 + Pydantic loader + 单测。不接线，零风险。
2. **种子 campaign**：频次≥5 的 153 名 → 联网核实（复用中企 hierarchy campaign 的 3×sonnet 模式+证据留档）→ 复核 CSV 交用户过目 → 入册。
3. **2B-3 改造**：查表前置 + LLM 降级为补录建议；**灰度对拍**——同一批真库数据，现行 LLM 口径 vs 新查表口径各跑 dry-run，diff 报告交用户看过才切换默认。配置旗标沿 PipelineConfig 布尔位先例。
4. **存量回填**：真库 4,795 事件 dry-run→would-change CSV 复核→apply（dry-run→.bak(WAL+三重断言)→单事务→幂等自检，busy_timeout=30000）。
5. **en 侧接线**：2B-4 en_backfill 加 registry 优先档（§5）。
6. **季度循环**：miss 报告喂 codex_review quarterly（与 T3 同机制），登记册滚动扩充。

## 7. 测试与验收锚点

- 引擎单测：exact/alias/跨语言命中、hierarchy 复用（讹变→规范子公司名，不上卷）、透传+miss、别名冲突断言。
- 金样例断言：`Masdar`+`阿布扎比未来能源公司`→同一 (cn,en)；`中国能源海外投资有限公司`(121次)→`中国电建集团海外投资有限公司`；ACWA Power 变体合一。
- 2B-3 改造对拍：查表口径 vs 现行 LLM 口径 diff **零意外合并**（查表只可能是 LLM 判定的子集+跨语言新增，出现册外合并即 bug）。
- 存量回填后：`developer_en` 空值率 61% 显著下降（153 名种子理论覆盖高频大头）；重跑幂等 0。
- JV/联合体串：各部件独立归一、分隔符与顺序不变（现行 rebuild 契约）。

## 8. 非目标（明确不做）

- 不动前端、s03 导出口径、s01b S2 prompt/提取逻辑
- 不合并母子公司、不重构 company_hierarchy_v2 及中企 2B 步骤
- 不物理合并 pipeline 步骤（2B/2B-3 保持两步）
- 不在本设计内处理 `Unknown`(359次) 占位值（维持现行 unify_junk 逻辑）

## 9. 风险与护栏

| 风险 | 护栏 |
|---|---|
| 登记册错条目造成批量错改 | 入册须过复核 CSV；apply 全走 dry-run→.bak→幂等；金样例断言 |
| 与 en_backfill 乒乓覆盖 | §5 优先级规则，两工具同源 |
| hierarchy 文件变动破坏引擎 | 引擎只读+载入校验；hierarchy 本身不改 |
| LLM 降级后归一能力短期下降 | 灰度对拍确认查表覆盖≥现行合并集后才切默认 |
