# Energy Taxonomy v2.1 / TX-MF Contract Migration Report

执行日期：2026-08-14（Asia/Shanghai）  
工作树：`database-refactor-expansion-aa6198`  
规则版本：`energy_taxonomy_v2.1`

## Dry-run

- 主库初始 SHA-256：`7E60276F449A120A86A9955747BC4E64A3B81714087CA1AB1359C8AEC44FA33B`
- TX/MF schema：已有 5 个 normalized 列，待新增 4 个 raw 列。
- canonical 非受控技术标签：30 条，均为水光、风水、火储等 v2.1 之外的扩展标签。
- shadow：809 条，全部待从 `energy_taxonomy_v2` 更新为 `energy_taxonomy_v2.1`；其中同 30 条扩展标签待清空。
- 合法四标签父类错误：0。

## Apply

1. `scripts/migrate_add_txmf_columns.py --apply`
   - SQLite backup：`s02a_duplicate_merger/data/db/bri_re_database.db.bak.20260814_083547`
   - 新增：`voltage_kv_raw`、`line_length_km_raw`、`transmission_capacity_mw_raw`、`production_capacity_raw`。
   - normalized 五列保持不变。
2. `scripts/migrate_energy_taxonomy_v21.py --apply`
   - 迁移前（已含 raw schema）SHA-256：`40EEC123C43CA8F9BF75D8335CAD573C5DFAE5A3C541130B06BFE52BFC2CFDB4`
   - SQLite backup：`s02a_duplicate_merger/data/db/bri_re_database.db.bak.energy-taxonomy-v21.20260814_083547`
   - canonical 标签变更：30；shadow 标签变更：30；shadow 版本变更：809。
   - 最终主库 SHA-256：`264474EAEACEBA2099574AE73FAAFC68CB912C7DAEA2B150ECFEE934DCEDA40E`

> SQLite backup API 会生成逻辑等价但页面布局可能不同的文件，因此备份文件 SHA-256 不要求等于源库操作前 SHA-256；行数、ID、Category、外键和读回值是恢复门禁。

## Post-apply gates

| 门禁 | 结果 |
|---|---:|
| canonical_projects | 4529 |
| project_events | 5365 |
| chinese_entities | 6572 |
| canonical/event/entity ID 集合 | 与 schema 迁移前备份完全一致 |
| canonical Category | 与 schema 迁移前备份完全一致 |
| foreign_key_check | 0 errors |
| 历史 raw 非 NULL | 0 / 0 / 0 / 0 |
| shadow v2.1 | 809 / 809 |
| 非受控或父类错误标签 | 0 |
| 合法标签 | 光储 172；风储 11；风光 27；风光储 26 |
| schema migration rerun | 0 columns pending |
| taxonomy migration rerun | 0 tag/version changes pending |

两条历史 TX 事件 `KEN-TX-0001-01`、`UZB-TX-0004-01` 的 `capacity_mw_raw='0' / capacity_mw=0.0` 按已批准的“历史 TX/MF 数值不重算”边界保留。所有新 S01B→S02A TX/MF 数据会强制通用装机为空。

## Export and verification

- 离线导出：projects 3541、TX 440、MF 162、events 3541。
- Tests：S01B 621 passed；S02A 59 passed；S03 29 passed；root 299 passed + 1 skipped。
- Frontend：TypeScript typecheck 通过；Vite production build 通过；本地浏览器切换 `domain=txmf` 后显示 461 条记录和 TX kV/km 专用规格，console 0 error/warning。
- 未调用模型、未部署、未提交、未推送。
- Golden Standard 暂停快照、pilot、40 篇草稿/候选及 Terra/Sol 历史决策文件未改写。
