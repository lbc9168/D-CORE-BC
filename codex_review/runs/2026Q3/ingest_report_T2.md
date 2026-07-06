# Ingest 报告：T2 / 2026Q3

运行模式：--apply


## 统计
| 项目 | 数量 |
| --- | --- |
| 总条数 | 51 |
| 有效条数 | 51 |
| 拒收条数 | 0 |
| 自动收条数 | 6 |
| 改数据（gated）条数 | 45 |
| uncertain 跳过条数 | 0 |
| 已定案跳过条数（幂等）| 0 |

## 自动收明细
| id | 结论 |
| --- | --- |
| ISR-HB-0001 | action=keep，confidence=n/a |
| IDN-TH-0016 | action=keep，confidence=n/a |
| IDN-TH-0038 | action=keep，confidence=n/a |
| ZWE-TH-0003 | action=keep，confidence=n/a |
| AFG-HY-0001 | action=keep，confidence=n/a |
| AUS-ES-0001 | action=keep，confidence=n/a |

## 改数据（gated）明细
| id | 动作 |
| --- | --- |
| AUS-HB-0015 | exclude |
| COG-HY-0005 | exclude |
| KAZ-HB-0016 | exclude |
| MAR-HB-0006 | exclude |
| MRT-HB-0003 | exclude |
| PHL-HB-0002 | downgrade_non_project |
| SAU-HB-0003 | downgrade_non_project |
| SAU-HB-0021 | exclude |
| SAU-PV-0059 | downgrade_non_project |
| USA-NC-0001 | exclude |
| VNM-TH-0006 | downgrade_non_project |
| VNM-WD-0030 | downgrade_non_project |
| AGO-MF-0001 | exclude |
| ARE-MF-0004 | exclude |
| IDN-MF-0028 | exclude |
| KAZ-MF-0005 | exclude |
| SAU-MF-0001 | exclude |
| AFG-PV-0002 | exclude |
| AFG-PV-0001 | exclude |
| AFG-PV-0003 | exclude |
| AFG-TX-0001 | exclude |
| AFG-TX-0002 | exclude |
| AGO-HB-0001 | exclude |
| AGO-HB-0002 | exclude |
| AGO-PV-0001 | exclude |
| AGO-PV-0002 | exclude |
| AGO-PV-0003 | exclude |
| ALB-PV-0003 | exclude |
| ARE-HB-0003 | exclude |
| ARE-HB-0012 | exclude |
| ARE-PV-0012 | exclude |
| ARE-TH-0004 | exclude |
| ARE-WD-0005 | exclude |
| ARG-PV-0003 | exclude |
| ARM-PV-0001 | exclude |
| ARM-PV-0002 | exclude |
| AUS-ES-0006 | exclude |
| AUS-ES-0007 | exclude |
| AUS-ES-0008 | exclude |
| AUS-ES-0013 | exclude |
| AUS-ES-0015 | exclude |
| AUS-ES-0017 | exclude |
| AUS-ES-0020 | exclude |
| AUS-HB-0004 | exclude |
| AUS-HB-0010 | exclude |

## 抽检清单
- apply_plan 文件：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\apply_plan_T2.json`
- manual_check CSV：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\manual_check_T2.csv`（45 条 gated 中抽检部分）

## 下一步
- apply 清单已生成（apply_plan_<task>.json）。
- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。
- 请先复核 `manual_check_T2.csv`，再跑 apply_actions。
