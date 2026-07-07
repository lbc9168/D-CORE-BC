# Apply 报告：T3 / 2026Q3

运行模式：--apply（已执行）


## 统计
- done: 24
- dry_run_only: 0

## 明细
| id | 结果 | 备注 |
| --- | --- | --- |
| 石油工程建设公司 | done |  |
| 中集安瑞科控股有限公司 | done |  |
| 江联重工公司 | done |  |
| 国家电投上海电力 | done |  |
| 上海电气输配电工程成套有限公司 | done |  |
| 赛维集团 | done |  |
| 湖南云箭集团 | done |  |
| 中国通信服务集团沙特分公司 | done |  |
| 中国建筑一局（集团）有限公司 | done |  |
| 江西电建公司 | done |  |
| 天赐材料 | done |  |
| 天津泰达环保有限公司 | done |  |
| 中国路桥 | done |  |
| 重庆三峰环境集团股份有限公司 | done |  |
| 湖南建工集团有限公司 | done |  |
| 中远海运特运 | done |  |
| 中电国际技术股份有限公司 | done |  |
| 平高电气集团 | done |  |
| 中国葛洲坝电力公司 | done |  |
| 尚德电力 | done |  |
| 中国能源建设集团山西电力勘测设计院有限公司 | done |  |
| 中国三峡集团 | done |  |
| Shandong Hi-Speed Group | done |  |
| 旗滨集团 | done |  |

## 幂等自检
外键+前缀一致性：通过 ✓
重放检查（pending=0）：通过 ✓

## T3 人工三步提示
1. 把 `registry_additions_2026Q3.json` 合并进 `company_hierarchy_draft_*.json`（用户在编辑，不代劳）
2. 重跑 `uv run supporting_docs/build_hierarchy_v2.py`
3. 在 s01b 跑 `entity_reparse_maindb --apply`（主库 gated）
