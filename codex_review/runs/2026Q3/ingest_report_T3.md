# Ingest 报告：T3 / 2026Q3

运行模式：--apply


## 统计
| 项目 | 数量 |
| --- | --- |
| 总条数 | 50 |
| 有效条数 | 50 |
| 拒收条数 | 0 |
| 自动收条数 | 8 |
| 改数据（gated）条数 | 38 |
| uncertain 跳过条数 | 4 |
| 已定案跳过条数（幂等）| 0 |

## 自动收明细
| id | 结论 |
| --- | --- |
| Recurrent Energy | classification=not_chinese，confidence=high |
| e-STORAGE | classification=not_chinese，confidence=medium |
| 长江基建集团有限公司 | classification=not_chinese，confidence=high |
| 星展银行（中国）有限公司 | classification=not_chinese，confidence=high |
| 亚洲基础设施投资银行 | classification=not_company，confidence=high |
| 渣打集团 | classification=not_chinese，confidence=high |
| 长江实业集团有限公司 | classification=not_chinese，confidence=high |
| 电能实业有限公司 | classification=not_chinese，confidence=high |

## 改数据（gated）明细
| id | 动作 |
| --- | --- |
| 通威 | new_group |
| 惠生工程 | alias_of_existing |
| 中国水电建设集团 | alias_of_existing |
| 特变电工新疆新能源股份有限公司 | subsidiary_of_existing |
| 川投国际 | new_group |
| 河南二建集团公司 | new_group |
| 华晟新能源 | new_group |
| 中交第一航务工程局有限公司 | subsidiary_of_existing |
| 中水对外 | alias_of_existing |
| 远景储能 | alias_of_existing |
| 中钢设备有限公司 | subsidiary_of_existing |
| 双良集团有限公司 | new_group |
| 中国大唐集团海外投资公司 | alias_of_existing |
| Universal Energy Co. Ltd. | alias_of_existing |
| 许继电气 | subsidiary_of_existing |
| 中国水电（马来西亚）有限公司 | alias_of_existing |
| 西电国际 | alias_of_existing |
| 中远海运工程物流有限公司 | alias_of_existing |
| 中国电建阿根廷分公司 | alias_of_existing |
| Recurrent Energy, LLC | subsidiary_of_existing |
| 紫金龙净清洁能源有限公司 | subsidiary_of_existing |
| 惠生（南通）重工有限公司 | alias_of_existing |
| 国网宁波供电公司 | alias_of_existing |
| 中国交建尼日利亚公司 | alias_of_existing |
| 中天科技 | new_group |
| 中国石化石油工程建设公司 | alias_of_existing |
| 凯盛科技 | subsidiary_of_existing |
| Europe TianYing BVBA | subsidiary_of_existing |
| 贵州工程公司 | alias_of_existing |
| 双登集团股份有限公司 | new_group |
| 湖北工程公司工程建设公司 | alias_of_existing |
| 中国建筑国际集团有限公司 | alias_of_existing |
| 中建安装 | alias_of_existing |
| 中国电建 | alias_of_existing |
| 深圳远信储能技术股份有限公司 | new_group |
| 山东泰开电力建设工程有限公司 | new_group |
| 北京中科清风科技有限公司 | new_group |
| 海油工程 | alias_of_existing |

## uncertain 跳过（下季度自然重新入队）
- 中国能源
- 瑞科科技
- 石油工程建设公司
- GREENISM

## 抽检清单
- apply_plan 文件：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\apply_plan_T3.json`
- manual_check CSV：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\manual_check_T3.csv`（38 条 gated 中抽检部分）

## 下一步
- apply 清单已生成（apply_plan_<task>.json）。
- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。
- 请先复核 `manual_check_T3.csv`，再跑 apply_actions。
