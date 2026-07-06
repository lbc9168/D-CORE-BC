# Apply 报告：T3 / 2026Q3

运行模式：--apply（已执行）


## 统计
- done: 38
- dry_run_only: 0

## 明细
| id | 结果 | 备注 |
| --- | --- | --- |
| 通威 | done |  |
| 惠生工程 | done |  |
| 中国水电建设集团 | done |  |
| 特变电工新疆新能源股份有限公司 | done |  |
| 川投国际 | done |  |
| 河南二建集团公司 | done |  |
| 华晟新能源 | done |  |
| 中交第一航务工程局有限公司 | done |  |
| 中水对外 | done |  |
| 远景储能 | done |  |
| 中钢设备有限公司 | done |  |
| 双良集团有限公司 | done |  |
| 中国大唐集团海外投资公司 | done |  |
| Universal Energy Co. Ltd. | done |  |
| 许继电气 | done |  |
| 中国水电（马来西亚）有限公司 | done |  |
| 西电国际 | done |  |
| 中远海运工程物流有限公司 | done |  |
| 中国电建阿根廷分公司 | done |  |
| Recurrent Energy, LLC | done |  |
| 紫金龙净清洁能源有限公司 | done |  |
| 惠生（南通）重工有限公司 | done |  |
| 国网宁波供电公司 | done |  |
| 中国交建尼日利亚公司 | done |  |
| 中天科技 | done |  |
| 中国石化石油工程建设公司 | done |  |
| 凯盛科技 | done |  |
| Europe TianYing BVBA | done |  |
| 贵州工程公司 | done |  |
| 双登集团股份有限公司 | done |  |
| 湖北工程公司工程建设公司 | done |  |
| 中国建筑国际集团有限公司 | done |  |
| 中建安装 | done |  |
| 中国电建 | done |  |
| 深圳远信储能技术股份有限公司 | done |  |
| 山东泰开电力建设工程有限公司 | done |  |
| 北京中科清风科技有限公司 | done |  |
| 海油工程 | done |  |

## 幂等自检
外键+前缀一致性：通过 ✓
重放检查（pending=0）：通过 ✓

## T3 人工三步提示
1. 把 `registry_additions_{quarter}.json` 合并进 `company_hierarchy_draft_*.json`（用户在编辑，不代劳）
2. 重跑 `uv run supporting_docs/build_hierarchy_v2.py`
3. 在 s01b 跑 `entity_reparse_maindb --apply`（主库 gated）
