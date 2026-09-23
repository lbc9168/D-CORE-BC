"""Produce the reviewable handoff from the frozen, verified isolated delta."""
import json
from contextlib import closing
from recover_corrections_20260923 import OUT, WORK, audit, protected


def run():
    plan=audit.read(OUT/'production-correction-delta.json')
    historical=audit.read(OUT/'historical-audit.json')
    verification=audit.read(OUT/'verification.json')
    rehearsal=audit.read(OUT/'rehearsal.json')
    changes=plan['changes']
    summary={db:{table:{'insert':sum(x['before'] is None for x in rows),
        'update':sum(x['before'] is not None for x in rows)} for table,rows in tables.items() if table!='sqlite_sequence'} for db,tables in changes.items()}
    mappings=[]
    with closing(audit.ro(WORK/'main.db')) as c:
        for table in ('canonical_projects','project_events','chinese_entities','non_project_records'):
            for change in changes['main'].get(table,[]):
                after=change['after'];before=change['before']
                mappings.append({'table':table,'id':after['id'],'action':'insert' if before is None else 'update',
                    'canonical_id':after.get('canonical_id'),'event_id':after.get('event_id'),
                    'source_links':after.get('source_links',after.get('source_link')),
                    'changed_fields':list(after) if before is None else [k for k,v in after.items() if before.get(k)!=v]})
    audit.dump(OUT/'id-mapping.json',{'focus':{key:audit.read(OUT/f'{key}-result.json') for key in ('kenya','sarimay','oman')},
        'historical_sources':audit.read(OUT/'historical-id-map.json')['records'],
        'business_changes':mappings,'note':'Full exact old/new values are authoritative in production-correction-delta.json; diagnostic reused25 IDs must never be migrated wholesale.'})
    audit.dump(OUT/'final-summary.json',{'historical_counts':historical['counts'],'table_changes':summary,
        'main_before':{k:v['count'] for k,v in plan['baseline']['main'].items()},
        'main_after':{k:v['count'] for k,v in plan['candidate']['main'].items()},
        'delta_sha256':audit.digest(OUT/'production-correction-delta.json'),**protected()})
    rows=['|库/表|新增|更新|','|---|---:|---:|']
    for db,tables in summary.items():
        for table,counts in tables.items():rows.append(f"|{db}.{table}|{counts['insert']}|{counts['update']}|")
    table='\n'.join(rows)
    before=plan['baseline']['main'];after=plan['candidate']['main']
    counts=' / '.join(f"{t}: {before[t]['count']} → {after[t]['count']}" for t in ('canonical_projects','project_events','chinese_entities','non_project_records'))
    export=audit.read(OUT/'export-delta.json')
    ed=export['same_code_baseline_vs_corrected'];pd=export['public_snapshot_vs_corrected']
    report=f'''# 流水线纠错恢复：已实施的隔离结果

生产一致性副本已完成实际纠错；生产本轮未写入，仍4756 canonical / 5616 event / 6787关系。此前8篇受控生产写入是既成事实，不再沿用“生产从未修改”。外部调用0，原274与40请求账本逐文件校验不变；公开导出、缓存未替换，metadata.last_updated仍2026-09-15。原工作树改动保留。

## 原因、修正及结果

|问题|错误原因|实际隔离修正|
|---|---|---|
|肯尼亚英特利|S1误把有明确地热用途的电源订单当无关，混淆设备与基地|raw28080→新source11852→non_project1580，MW空；review214 accepted且canonical_id映射为源11852；discard4790及原S1响应不改。S1/S2和确定性兜底修复设备边界。|
|茂物|旧名录门槛使新合格事件不能公开|复用IDN-BM-0015，-02及Unknown6877保留，163百万美元一次；旧-01作为巴厘岛文中背景隐藏，6745仍Unknown，不提升为已确认参与；源11691实质排除。|
|Sarimay|历史候选排除原因被误当永久禁入|Voltalia、花拉子模、100MWac、面积、并网时间与世行担保共同支持复用UZB-PV-0013；新增-02及6878/6879，11850/51 import1，旧任务resolved；旧-01不借新参与，0024档案保留排除、不做强并。|
|阿曼JBB ALI|旧developer把接网公司误当业主，造成假冲突|复用OMN-WD-0002新增-02及6880–6882；raw28088、源11853–11855；旧449–451及-01假业主清空。旧120MW、新123MW各留其报道口径，未创建OMN-WD-0005。|
|历史旧门槛|名录未命中、句式/角色未知被当排除；漏看non_project导致虚假重提取待办|逐原文核验203源/146URL，实际应用确定性修复、身份裁决及非项目记录复用；容量、金额、业主、单位、组合包边界随实质证据纠正。|
|柬埔寨四项目|两文分别署四川/山东电建，不能凭同集团猜测当两家参与|9源保有效参与，每项目一条关系，原名冲突明确保留，法人/集团ID空；原具体子公司关系隐藏但原ID保留。仅名称标准化待证，不阻断项目。|

历史203源最终互斥分类：`{json.dumps(historical['counts'],ensure_ascii=False)}`。a与a_nonproject均已实际隔离实施；d保留实质排除；already_restored_13_skip不重复恢复，already_valid_skip不重复入库。b真实重提取为0。详细逐条原文、失败原因、裁决和ID见[历史清单](historical-audit.md)、[机器清单](historical-audit.json)、[映射](id-mapping.json)。新增配套PV从旧酒店文中另作审定源，不把酒店恢复成新能源制造业，也不重复计入203分类。

136条项目源对应104个既有canonical的恢复/身份修正，不应称为136个新项目。

身份与地点仍需补证的源11749/11750：Maluku/Djili与原文刚果（布）/总统署名冲突，缺原始合同地点或发文方勘误；已保存唯一协调任务，import0、pending_review及旧事件隐藏，重复运行不释放。具体法人署名、0024历史档案合并属于单独未决，不阻断已证实的新参与。

隔离主表总量：{counts}。旧ID均保留；历史误分类/重复canonical归档排除，Dhaka旧事件ID保留但定向重挂正确canonical。所有旧新值在[精确delta](production-correction-delta.json)。

## 已有25篇成果

在独立诊断副本实际纠正11篇、40有序patch操作/37最终行，详见[25篇结果](reused25/reused25-report.md)。投资日期误用投产日、供应商父子重复、背景项目借用、业主丢失、错误英文身份已修；core20关系27→23，holdout5关系仍8，目标项目集合不变。原始成果四库及响应不变；这些诊断库ID不进入生产delta。

## 验证与展示

定向测试223（S01B）+33（S02A/pipeline）+18（S03）通过；属于构造回归、原响应回放和本地审定，非新模型验证。真实隔离跨模块重复通过，四库业务行/标记不变；全部ID、外键、TEXT源值/协调快照、13个旧恢复项目、阻断事件与关系筛选直接核验。四库精确delta注入失败后DDL/行/sequence全回滚；实际重放精确匹配候选，重复already_applied。见[验证](verification.json)、[事务演练](rehearsal.json)、[最终独立审查](independent-review.md)。

同一导出代码下，基线事件CSV {ed['before_count']}→{ed['after_count']}行，逐行增加{len(ed['added'])}、移除{len(ed['removed'])}（修改行表现为一增一减）。相对目前公开{pd['before_count']}行的差分还包含此前8篇入库、空容量放行和省份差异，不能当作本次纯修正。完整[展示差分](export-delta.json)与preview-data仅在output。设备订单不伪装为物理电站；可选英文/容量/金额空值不成为资格门槛。

## 待一次批准的内容

只请求批准[生产精确修正方案](production-correction-plan.md)对应冻结delta；不含公开导出、缓存替换或部署。无需请求真实模型调用：文章范围为空，底层请求/重试/单篇累计上限均0，不启用provider/model或并发、不生成新增token账本，全部复用本地证据。缺合同地点无法通过重新提取同一文章解决。

清理三个本轮pytest临时目录被自动审批拒绝（blocked by policy），目录保留、未绕过；失败记录见validation-notes.json。必要原始响应、失败与审计文件均保留。
'''
    (OUT/'report.md').write_text(report,encoding='utf-8')
    production=f'''# 可批准的生产精确修正方案（尚未执行）

批准对象仅为production-correction-delta.json，SHA256 `{audit.digest(OUT/'production-correction-delta.json')}`。不是把candidate数据库搬回生产。该JSON逐行包含database/table/key/before/after，baseline/candidate含所有表schema、行数、完整内容hash与sequence；id-mapping.json是检索入口，不能代替delta。

{table}

另新增主库project_events与chinese_entities的nullable publication_status CHECK列；原有13列及2协调表已经生产应用，本次不重建。NULL兼容旧数据，显式pending_review/excluded被S03过滤；核心旧ID与全部原响应保留，无删除操作。

执行前必须：暂停其他写者；复核代码及delta哈希、independent-review.md、verification.json和rehearsal.json；生产四库完整逻辑快照必须与baseline一致，任何漂移即停止重新审阅，不能跳过旧值检查。按URL确认review214指向extracted11852而非物理canonical，数值以实际TEXT/REAL类型核验。

备份：新建不可覆盖的时间戳commit目录，用SQLite backup API备份main/raw/extracted/review及导出库，记录每库SHA256/完整性/外键/快照；缓存与public文件记录hash并保存必要文件备份。现有baseline与原8篇commit备份继续保留。

事务：使用rehearse_correction_delta_20260923.py已演练的精确应用算法，在明确批准后增加受控生产入口，不解除隔离限制后随意执行。离线维护窗口转四库DELETE journal、synchronous FULL、同一连接ATTACH四库，BEGIN IMMEDIATE后重新核验baseline；先canonical、后event、后关系及源/任务/复核，以主键写精确旧新行。DDL、业务数据、源import、协调结果同事务；校验candidate完整快照和四库integrity/foreign keys后才COMMIT，再恢复journal模式。

幂等：当前完整快照等于candidate则already_applied，不重插任何项目/事件/关系/任务；等于baseline才执行，其他状态一律拒绝。已在隔离四库证明第一次精确应用、第二次零写入。

回滚：commit前任一失败整体ROLLBACK，已注入失败验证schema/行/sequence全部恢复。commit后若需回退，先停止写者并核验仍等于candidate，再基于本次before/after生成精确逆向事务并验证；若出现后续写入，不盲目恢复整库，先协调后续变更。只有核实无后续写入的受控灾难恢复才使用新鲜备份。禁止覆盖当前生产为旧隔离库。

提交后回读：四库candidate快照、URL与ID映射、源import与实际业务一致、协调幂等、旧13项目不变、无错误OMN0005、茂物163只一次、肯尼亚无MW及物理项目；生产公开文件/缓存/两账本哈希仍必须不变。另生成commit报告、备份manifest和正式回执。

展示影响：本次批准只改生产四库，公开页面仍旧快照；隔离export-delta.json已展示同代码纯修正差分及相对公开快照的完整差分。未来公开更新另行批准，必须明确包含之前空容量策略与8篇生产入库等差异，不在此方案自动执行。
'''
    (OUT/'production-correction-plan.md').write_text(production,encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':run()
