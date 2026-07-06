# Ingest 报告：T3 / 2026Q3

运行模式：--apply


## 统计
| 项目 | 数量 |
| --- | --- |
| 总条数 | 1 |
| 有效条数 | 1 |
| 拒收条数 | 0 |
| 自动收条数 | 0 |
| 改数据（gated）条数 | 0 |
| uncertain 跳过条数 | 1 |
| 已定案跳过条数（幂等）| 0 |

## uncertain 跳过（下季度自然重新入队）
- 未知公司X

## 下一步
- apply 清单已生成（apply_plan_<task>.json）。
- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。
- 请先复核 `manual_check_T3.csv`，再跑 apply_actions。
