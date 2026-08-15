# 储能倒查 2026-W30 —— 光伏/储能/综合能源 1,687 项目全量细读（只查不改）

**日期**：2026-07-26 · **方法**：主库事件 source_links → s01a raw_news.db 原文全文（覆盖 100%），
Haiku 42 批逐项目细读 → Sonnet 对 317 个拟改动行逐行对抗复核（复核覆盖 100%）。
**未动库、未改 prompt、未动前端。** 所有清单仅供复看，落库方式待拍板。

## 结论速览

| 类别 | 查了 | 主要发现 |
|---|---|---|
| 光伏 995 | 62 个候选 | **27 个确认实为光储**（拟改类 综合能源/光储），11 个 unclear 待定，24 个被复核否决 |
| 综合能源 480 | 子类全盘点 | 光储 175→**203**（+30 新识别，−2 降级）；其他可辨子类共 ~70：风光 27、微网混合 16、风光储 14、风储 8、水光 2 |
| 储能 192 | 数值补缺 | 可补 storage_power_mw **33** 个、storage_energy_mwh **21** 个；数值冲突 9 个 |
| 综合能源数值 | 同上 | 可补 storage_mwh 25、storage_mw 7、pv_mw 6；pv_mw 冲突 13（多为 DC/AC 或多事件不一致） |
| 新能源制造业 20 | 误填核查 | **20/20 确认误填**：storage_energy_mwh 存的其实是电池厂产能（GWh/年），Sonnet 全数 confirm |

## 文件说明

| 文件 | 内容 | 行数 |
|---|---|---|
| `pv_solar_storage_candidates.csv` | 光伏疑似光储候选：初审/复核/生效判定 + 数值 + 证据引文。**生效判定=yes 且复核=confirm/amend 的 27 行是拟改类清单** | 62 |
| `hb_subtype_inventory.csv` | 综合能源子类改判清单（判定≠库内标注的行）；全量分布见上表 | 100 |
| `es_hb_fill_candidates.csv` | 储能/综合能源可补数值长表（一行=项目×字段，status=fill/conflict） | 143 |
| `mf_mwh_check.csv` | 制造业 20 个 storage_energy_mwh 误填核查 | 20 |

## 质量注记

- Sonnet 复核否决率不低（PV 62 个候选杀掉 24），主要拦下：年发电量误当储能量、"计划配储/招标含储能选项"当已配储、组件出口误判为光储。已按复核结果过滤。
- 3 行 Haiku 漏标未进复核（CSV 复核状态="无需复核"但判定≠库内），其中 `BGD-HB-0003` 子类判定值无效（"光光"，按证据应为"其他"），复看时注意。
- pv_mw 的 13 个 conflict 多为 DC/AC 口径差或多事件数值不一致，需逐个人工裁。

## 待拍板（回填阶段）

1. PV→综合能源/光储 27 个：category+subcategory 同改，编号不动；**capacity_mw 语义随之变为全厂总装机**，是否把原光伏容量同步写入 pv_capacity_mw 需定规则。
2. unclear 11 个：搁置 or 并入下轮人工复看。
3. ES/HB 数值回填：走 storage_subcat_backfill 的 fill-if-null 惯例；conflict 行人工裁。
4. MF 20 个：storage_energy_mwh 清空，数值是否迁 production_capacity_value/unit（GWh/年）待定。
5. s01b 抽取 prompt 修改方向可基于本次误判模式设计（下一步另议）。
