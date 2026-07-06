# Ingest 报告：T1 / 2026Q3

运行模式：--apply


## 统计
| 项目 | 数量 |
| --- | --- |
| 总条数 | 40 |
| 有效条数 | 40 |
| 拒收条数 | 0 |
| 自动收条数 | 38 |
| 改数据（gated）条数 | 2 |
| uncertain 跳过条数 | 0 |
| 已定案跳过条数（幂等）| 0 |

## 自动收明细
| id | 结论 |
| --- | --- |
| c48270a1d37354757f31c7806ae84cf09c578121 | same_project=False，confidence=medium |
| 79c9af9acbd9277bf23d6e033cb8460f973fa1bf | same_project=False，confidence=medium |
| e0249cadc50d54f869a198083169d917a7b186e2 | same_project=False，confidence=medium |
| f911b03f0d7400d4e98da963e80299fdf19c766d | same_project=False，confidence=medium |
| 6a60c529b7e146211fc9b1b771a0b701f87be001 | same_project=False，confidence=high |
| 890896e6dde236e51df513351e37b302c096c286 | same_project=False，confidence=medium |
| cdf3c4f91fdc70e1f6089929440fb8a1db21e8e8 | same_project=False，confidence=high |
| 7dbbef6f935d4c0c17f5584274f5fc0599062bd9 | same_project=False，confidence=high |
| 9d8ff001202895144a33bfd5e2cf5b40c3033d9b | same_project=False，confidence=medium |
| 69802a562fc89cd4c8ffbba5f5649a4884504caa | same_project=False，confidence=high |
| f32f26552327745c31f6dd96063021413cd6c336 | same_project=False，confidence=high |
| 5280ba7598b28ed5761a60acc847aa03745643d8 | same_project=False，confidence=high |
| b1ace08f9cef58413961c0269a62c84b1ca10d4b | same_project=False，confidence=high |
| 902c440f4817355ff91bd455d1c7ac43ac157cce | same_project=False，confidence=high |
| 138566684e3edab4b4e018cc72fef8c54c5d7508 | same_project=False，confidence=medium |
| 46a46f27e9a04f206b3c8c44868cdc27e08e40d3 | same_project=False，confidence=high |
| 4c4177a1304160837fd6f1a71261ca7a42a41f37 | same_project=False，confidence=high |
| 0e0bfe0cbbd39a6934f183016813b700f5aae489 | same_project=False，confidence=high |
| 48aafc989c435ab797712adb7a8b3bc85dd13d7f | same_project=False，confidence=high |
| f62c51177512dd547425f4514e68e8589b4ac8ad | same_project=False，confidence=high |
| 497e7d43dce3aaa1179a2b8877daa192fb342dcf | same_project=False，confidence=high |
| 94e69cec773cbf5ad648d3a4f59415db70114ee0 | same_project=False，confidence=high |
| 9bec465b4b255e5b74452d03ca37cb9adc0c85f3 | same_project=False，confidence=high |
| 6ce4e2e90a907ca36daac2f662c198b0fae8961d | same_project=False，confidence=high |
| 4da3538bab3a3bcb7899210772c1f8a55747835a | same_project=False，confidence=high |
| 33b64b38c2683b76e618baab558aba06e2cbf291 | same_project=False，confidence=high |
| 6be8de45ae28878e0d1ef596ed96cf3223b44114 | same_project=False，confidence=high |
| 83c1bbf52f90b77ad7619ecae05099e06d7bf8c7 | same_project=False，confidence=high |
| b3eeeb801adab39d2aa78bd3e4ac6c4fd6bd5cbb | same_project=False，confidence=high |
| 3743ad934909bd03fa81d0086d24add7a4269af5 | same_project=False，confidence=high |
| 61de90cc3cbf7f01ebe3302e2fdb7cbd9c598a1b | same_project=False，confidence=high |
| 72f447db33d5200f5b487dbdb180f0d582580932 | same_project=False，confidence=high |
| 635a44d457353e723404eee2a5eb81bdd7d751ee | same_project=False，confidence=high |
| 01d444dbd2c9f5d41eb593684dfae3bed22576bb | same_project=False，confidence=high |
| 4bba988d5cc911899c196cfb3ab8b13664a6f4ad | same_project=False，confidence=high |
| c656a5ab18857e7f5ebf724965a04a5f7eca9c3f | same_project=False，confidence=high |
| ff2971d21c136f3b40aada9c3de4601fcf6ae74b | same_project=False，confidence=high |
| fc775c80d2fcbafd0364be41eb2d52babc02f16b | same_project=False，confidence=high |

## 改数据（gated）明细
| id | 动作 |
| --- | --- |
| a3455edfe2af893c3e43fd295dcc78dbd1076a73 | merge→survivor=SUR-HB-0005 |
| c65c76c1c04ae038292092d1c3a438395a157863 | merge→survivor=EGY-PV-0009 |

## 抽检清单
- apply_plan 文件：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\apply_plan_T1.json`
- manual_check CSV：`E:\Projects\BRI_RE_Database_v2\.claude\worktrees\dcore_v2.2\codex_review\runs\2026Q3\manual_check_T1.csv`（2 条 gated 中抽检部分）

## 下一步
- apply 清单已生成（apply_plan_<task>.json）。
- DB/registry 写入由 apply_actions 执行（用户确认抽检后）。
- 请先复核 `manual_check_T1.csv`，再跑 apply_actions。
