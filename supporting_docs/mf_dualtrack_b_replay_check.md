# 双轨方案B 执行报告：8对重叠补挂信源

生成时间：2026-07-07 10:28 UTC　运行模式：dry-run（拟执行）　真库：`E:\Projects\BRI_RE_Database_v2\s02a_duplicate_merger\data\db\bri_re_database.db`

## 统计

- 补挂/幂等跳过对数：8
- source_links 新增链接数：0
- source_links 已存在跳过数：0
- additional_remarks 追加行数：0
- npr 侧新标记 merged_into_canonical 行数：0
- npr 侧幂等跳过行数：8

## 逐对原文对照（人工/审查目验）

### npr[6] → EGY-MF-0001

- npr 项目名：`埃及博达新能5GW光伏制造基地`
- npr capacity_details：高效光伏电池2GW、光伏组件3GW
- canonical 项目名：`埃及博达新能5GW光伏制造基地`
- 目标事件：`EGY-MF-0001-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=EGY-MF-0001

### npr[31] → EGY-MF-0001

- npr 项目名：`埃及博达新能4GW光伏制造基地`
- npr capacity_details：年产能4GW
- canonical 项目名：`埃及博达新能5GW光伏制造基地`
- 目标事件：`EGY-MF-0001-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=EGY-MF-0001

### npr[126] → GBR-MF-0001

- npr 项目名：`英国明阳智能风电机组制造基地项目`
- canonical 项目名：`英国风电机组制造基地项目`
- 目标事件：`GBR-MF-0001-02`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=GBR-MF-0001

### npr[566] → SAU-MF-0006

- npr 项目名：`沙特20GW光伏晶体晶片生产项目`
- npr capacity_details：年产能20GW
- canonical 项目名：`沙特20吉瓦光伏晶体晶片生产工厂`
- 目标事件：`SAU-MF-0006-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=SAU-MF-0006

### npr[694] → MYS-MF-0016

- npr 项目名：`马来西亚吉打州圆柱形锂电池制造项目`
- canonical 项目名：`马来西亚圆柱锂电池制造项目`
- 目标事件：`MYS-MF-0016-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=MYS-MF-0016

### npr[756] → BRA-MF-0003

- npr 项目名：`巴西风电叶片制造基地项目`
- npr capacity_details：年产260套风电叶片
- canonical 项目名：`巴西年产260套风电叶片制造基地项目`
- 目标事件：`BRA-MF-0003-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=BRA-MF-0003

### npr[800] → MYS-MF-0011

- npr 项目名：`马来西亚双文丹组件工厂`
- canonical 项目名：`马来西亚双文丹组件工厂`
- 目标事件：`MYS-MF-0011-01`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=MYS-MF-0011

### npr[1009] → MYS-MF-0010

- npr 项目名：`马来西亚隆基2.8GW新建组件厂项目`
- npr capacity_details：2.8GW新建组件厂
- canonical 项目名：`马来西亚隆基新建组件项目`
- 目标事件：`MYS-MF-0010-02`
- 结果：skipped_idempotent
- 已标记：merged_into_canonical=MYS-MF-0010

## 行数一致性自检（本任务零行数变化）

| 表 | apply前 | apply后 |
|---|---|---|
| canonical_projects | 4529 | - |
| project_events | 5365 | - |
| chinese_entities | 6572 | - |
