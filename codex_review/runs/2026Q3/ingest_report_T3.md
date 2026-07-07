# Ingest 报告：T3 / 2026Q3

运行模式：--apply


## 统计
| 项目 | 数量 |
| --- | --- |
| 总条数 | 42 |
| 有效条数 | 42 |
| 拒收条数 | 0 |
| 自动收条数 | 0 |
| 改数据（gated）条数 | 24 |
| uncertain 跳过条数 | 18 |
| 已定案跳过条数（幂等）| 0 |

## 改数据（gated）明细
| id | 动作 |
| --- | --- |
| 石油工程建设公司 | subsidiary_of_existing |
| 中集安瑞科控股有限公司 | new_group |
| 江联重工公司 | new_group |
| 国家电投上海电力 | subsidiary_of_existing |
| 上海电气输配电工程成套有限公司 | subsidiary_of_existing |
| 赛维集团 | new_group |
| 湖南云箭集团 | new_group |
| 中国通信服务集团沙特分公司 | new_group |
| 中国建筑一局（集团）有限公司 | subsidiary_of_existing |
| 江西电建公司 | subsidiary_of_existing |
| 天赐材料 | new_group |
| 天津泰达环保有限公司 | new_group |
| 中国路桥 | subsidiary_of_existing |
| 重庆三峰环境集团股份有限公司 | new_group |
| 湖南建工集团有限公司 | alias_of_existing |
| 中远海运特运 | subsidiary_of_existing |
| 中电国际技术股份有限公司 | subsidiary_of_existing |
| 平高电气集团 | subsidiary_of_existing |
| 中国葛洲坝电力公司 | subsidiary_of_existing |
| 尚德电力 | new_group |
| 中国能源建设集团山西电力勘测设计院有限公司 | subsidiary_of_existing |
| 中国三峡集团 | alias_of_existing |
| Shandong Hi-Speed Group | alias_of_existing |
| 旗滨集团 | new_group |

## uncertain 跳过（下季度自然重新入队）
- 中国能源
- 瑞科科技
- GREENISM
- 三一微电网
- 江西中煤东非区域公司
- 莱鼎能源
- 环球矿业
- 特隆美储能
- 中储科技公司
- 云能国际香港公司
- 易助能源
- 中通文博
- 湖南郴电国际水电投资有限责任公司
- 丽天智能
- 一道新能源科技股份有限公司
- 杭州江河水电
- 中国永恒实业投资公司
- 新世纪电气

## 抽检清单
- apply_plan 文件：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\apply_plan_T3.json`
- manual_check CSV：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\manual_check_T3.csv`（24 条 gated 中抽检部分）

## 下一步
- apply 清单已生成（apply_plan_<task>.json）。
- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。
- 请先复核 `manual_check_T3.csv`，再跑 apply_actions。
