"""Offline, evidence-reviewed recovery on production snapshots only.

The generated full-row delta is a review artifact, not production authorization.
This entry point deliberately refuses paths outside its isolated output folder.
"""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import sys
import socket
from unittest.mock import patch

import apply_production_pilot_20260923 as audit

ROOT = audit.ROOT
OUT = ROOT / 'output/correction-recovery-20260923'
BASE = OUT / 'baseline'
WORK = OUT / 'candidate'
for module in ('pipeline', 's01b_project_extractor', 's02a_duplicate_merger', 's03_data_exporter'):
    sys.path.insert(0, str(ROOT / module / 'src'))
sys.path.insert(0, str(ROOT))


def connect(label):
    path = (WORK / f'{label}.db').resolve()
    if not path.is_relative_to(OUT.resolve()) or not path.exists():
        raise ValueError('Only existing isolated copies may be modified')
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def update(conn, table, key, values):
    """Explicit ID updates; the full original rows remain in baseline and delta."""
    assert conn.execute(f'SELECT 1 FROM {audit.qi(table)} WHERE id=?', (key,)).fetchone()
    conn.execute(f'UPDATE {audit.qi(table)} SET '+','.join(f'{audit.qi(k)}=?' for k in values)+' WHERE id=?', [*values.values(), key])


def prepare():
    for label in audit.DBS:
        audit.backup(BASE / f'{label}.db', WORK / f'{label}.db')
    with closing(connect('main')) as c, c:
        c.execute("ALTER TABLE project_events ADD COLUMN publication_status TEXT CHECK(publication_status IN ('eligible','pending_review','excluded'))")
        c.execute("ALTER TABLE chinese_entities ADD COLUMN publication_status TEXT CHECK(publication_status IN ('eligible','pending_review','excluded'))")
        update(c, 'canonical_projects', 'IDN-BM-0015', {'excluded': 0, 'excluded_reason': None})
        update(c, 'project_events', 'IDN-BM-0015-01', {
            'publication_status': 'excluded',
            'additional_remarks': '2026-09-23 evidence adjudication: raw27720/97618 is the Bali visit; Bogor is background only, no independent construction milestone. Preserve event ID and Unknown relation6745 as audit; do not publish or borrow Bali participation.'})
        update(c, 'project_events', 'IDN-BM-0015-02', {
            'publication_status': 'eligible',
            'project_summary': '印尼茂物垃圾焚烧发电项目于2026年9月17日举行开工仪式；伟明环保参与，具体角色Unknown。总投资约1.63亿美元；1500吨/日为垃圾处理量，不是发电MW。',
            'total_investment_usd_raw': '投资金额约1.63亿美元'})
    with closing(connect('extracted')) as c, c:
        update(c, 'extracted_projects', 11691, {'publication_status': 'excluded', 'excluded': 1,
            'exclude_source': 'evidence_adjudication', 'exclude_reason': '原文97618茂物仅背景推进，无独立本次工程节点；不得借用巴厘岛阶段或参与关系。'})
        for rid in (11843,11849):
            row = dict(c.execute('SELECT * FROM extracted_projects WHERE id=?',(rid,)).fetchone())
            evidence=json.loads(row['entity_evidence_json'])
            evidence['correction_adjudication']={'source':'agent_source_review','original_summary':row['project_summary'],
                'reason':'Remove obsolete BOT/BOOT assertion after the previously accepted Unknown role ruling; original model responses untouched.'}
            update(c,'extracted_projects',rid,{'project_summary':'伟明环保参与印尼茂物垃圾焚烧发电项目；角色Unknown。双报道合并为同一开工事件，投资仅在事件记录一次。',
                'entity_evidence_json':json.dumps(evidence,ensure_ascii=False)})


def diff():
    result={}
    for label in audit.DBS:
        result[label]={}
        with closing(audit.ro(BASE/f'{label}.db')) as b, closing(audit.ro(WORK/f'{label}.db')) as c:
            b.row_factory=c.row_factory=sqlite3.Row
            for table in audit.names(c,'main'):
                columns=audit.info(c,'main',table)
                key=[r[1] for r in columns if r[5]] or (['name'] if table=='sqlite_sequence' else ['rowid'])
                def rows(db):
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():return {}
                    return {tuple(r[k] for k in key):dict(r) for r in db.execute('SELECT '+('rowid,*' if key==['rowid'] else '*')+' FROM '+audit.qi(table))}
                before,after=rows(b),rows(c)
                changes=[{'key':list(k),'before':before.get(k),'after':after.get(k)} for k in before.keys()|after.keys() if before.get(k)!=after.get(k)]
                # A nullable column addition does not make every legacy row a business change.
                meaningful=[d for d in changes if d['before'] is None or d['after'] is None or any(d['before'].get(k)!=v for k,v in d['after'].items())]
                if meaningful:result[label][table]=meaningful
    audit.dump(OUT/'production-correction-delta.json',{'production_applied':False,'changes':result,
        'schema_additions':[{'database':'main','table':table,'column':'publication_status',
                             'sql':f"ALTER TABLE {table} ADD COLUMN publication_status TEXT CHECK(publication_status IN ('eligible','pending_review','excluded'))"} for table in ('project_events','chinese_entities')],
        'baseline':audit.read(OUT/'production-before.json'),'candidate':audit.snapshot({k:WORK/f'{k}.db' for k in audit.DBS})})
    return {db:{t:len(rows) for t,rows in tables.items()} for db,tables in result.items()}


def kenya():
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger
    decision=audit.read(OUT/'kenya-adjudication.json')
    src=decision['source']; url=src['url']
    with closing(connect('extracted')) as c, c:
        existing=c.execute('SELECT id FROM extracted_projects WHERE source_link=?',(url,)).fetchone()
        if existing: raise ValueError('Already staged; use final delta replay to test idempotency')
        rid=c.execute('SELECT MAX(id)+1 FROM extracted_projects').fetchone()[0]
        record={'id':rid,'record_type':'设备订单','project_name':'肯尼亚地热制氢IGBT电源设备订单',
          'project_name_en':'Kenya geothermal hydrogen IGBT power supply order','country':'肯尼亚','country_iso':'KEN',
          'category':'地热及余热','project_stage':'中标','implementing_entity':'英特利','raw_entity_name':'英特利',
          'entity_name':'英特利','identity_status':'article_supported_chinese','involvement_type':'设备供应商',
          'capacity_details':'1000方级IGBT制氢电源；设备能力，不转换为MW','source_link':url,'publish_date':src['publish_date'],
          'publication_status':'eligible','excluded':0,'needs_review':0,'imported_to_s02a':0,
          'is_unnamed_placeholder':0,'batch_id':'offline-correction-20260923',
          'project_summary':decision['project']['project_summary'],'entity_evidence_json':json.dumps(decision,ensure_ascii=False),
          'financing_model':'Unknown','storage_status':'not_mentioned'}
        c.execute('INSERT INTO extracted_projects ('+','.join(audit.qi(k) for k in record)+') VALUES ('+','.join('?' for _ in record)+')',list(record.values()))
    # Use the existing S02A grouping + non-project writer, limited to this source.
    df,ids=merger._load_from_db(str(WORK/'extracted.db'))
    projects=merger._group_into_logical_projects(df.loc[df['id']==rid].copy())
    assert len(projects)==1 and projects[0]['record_type']=='设备订单'
    from s02a_duplicate_merger.tools.coordination import attached_transaction
    with attached_transaction(WORK/'extracted.db',WORK/'main.db') as c:
        assert merger._write_non_project_records(c,projects,commit=False)==1
        c.execute('UPDATE extracted.extracted_projects SET imported_to_s02a=1 WHERE id=?',(rid,))
        npid=c.execute('SELECT id FROM non_project_records WHERE source_link=?',(url,)).fetchone()[0]
    with closing(connect('review')) as c,c:
        update(c,'review_queue',214,{'canonical_id':str(rid),'state':'accepted','suggested_value':'设备订单 / eligible',
          'evidence':json.dumps({'adjudication':str(OUT/'kenya-adjudication.json'),'source_id':rid,'non_project_id':npid,
            'note':'Offline source adjudication; original S1 response and discarded4790 unchanged.'},ensure_ascii=False)})
    audit.dump(OUT/'kenya-result.json',{'raw_id':28080,'source_id':rid,'non_project_id':npid,'review_id':214,
        'canonical_id':None,'event_id':None,'original_discard_preserved':4790,'external_calls':0})


def sarimay():
    from bri_pipeline.historical_reprocess import build_coordination_plan, apply_coordination_plan
    with closing(connect('main')) as c,c:
        update(c,'canonical_projects','UZB-PV-0013',{'excluded':0,'excluded_reason':None})
        # Historical article contains no named Chinese participant. It is retained
        # internally, without borrowing the new event's Chinese participation.
        update(c,'project_events','UZB-PV-0013-01',{'publication_status':'excluded',
            'additional_remarks':'2026-09-23: original report lacks named Chinese participation; retain historical facts/ID, publish the separately evidenced new event only.'})
    plan=build_coordination_plan(WORK/'extracted.db',WORK/'main.db',{
      'task_id':'identity-26f4784652d44cecbf188fc1','decision':'existing','canonical_id':'UZB-PV-0013',
      'reviewer':'agent_local_source_adjudication','evidence_review_note':'identity-audit.md: same Voltalia/Khorezm/100MWac, 177~180ha, Nov2025 grid connection, World Bank USD3.5m payment guarantee. Old exclusion was absent named participant; new CMEC/Trina evidence qualifies. 0024 stays excluded; no merge.'})
    audit.dump(OUT/'sarimay-coordination-plan.json',plan)
    result=apply_coordination_plan(plan,OUT/'sarimay-backup')
    before=audit.snapshot({'main':WORK/'main.db','extracted':WORK/'extracted.db'})
    repeat=apply_coordination_plan(plan,OUT/'sarimay-backup')
    assert repeat['status']=='already_applied' and before==audit.snapshot({'main':WORK/'main.db','extracted':WORK/'extracted.db'})
    audit.dump(OUT/'sarimay-result.json',{'result':result,'repeat':repeat,'repeat_unchanged':True})


def oman():
    from s02a_duplicate_merger.tools.coordination import attached_transaction,save_hold
    from bri_pipeline.historical_reprocess import build_coordination_plan,apply_coordination_plan
    original=audit.read(ROOT/'output/pipeline-production-readiness-20260923-144217/pending-coordination.json')['tasks'][0]
    project=original['project']; sources=original['source_rows']
    url=project['source_links'][0]
    with closing(audit.ro(ROOT/'output/incremental-20-20260923/raw.db')) as c:
        c.row_factory=sqlite3.Row
        raw=dict(c.execute('SELECT * FROM raw_news WHERE url=?',(url,)).fetchone())
    with closing(connect('raw')) as c,c:
        assert not c.execute('SELECT 1 FROM raw_news WHERE url=?',(url,)).fetchone()
        raw['id']=c.execute('SELECT MAX(id)+1 FROM raw_news').fetchone()[0]
        c.execute('INSERT INTO raw_news ('+','.join(map(audit.qi,raw))+') VALUES ('+','.join('?' for _ in raw)+')',list(raw.values()))
    idmap={}
    with closing(connect('extracted')) as c,c:
        nextid=c.execute('SELECT MAX(id)+1 FROM extracted_projects').fetchone()[0]
        for index,row in enumerate(sources):
            idmap[row['id']]=nextid+index
            row=dict(row);row['id']=nextid+index
            row['project_developer']='Saba Jalan';row['project_developer_en']='Saba Jalan'
            row['project_developer_evidence']='业主EDF法国电力集团项目公司Saba Jalan'
            c.execute('INSERT INTO extracted_projects ('+','.join(map(audit.qi,row))+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        for rid in (449,450,451):update(c,'extracted_projects',rid,{'project_developer':'','project_developer_en':''})
    with closing(connect('main')) as c,c:
        update(c,'project_events','OMN-WD-0002-01',{'project_developer':'','project_developer_en':''})
    project['_source_record_ids']=list(idmap.values())
    project['project_developer']='Saba Jalan';project['project_developer_en']='Saba Jalan'
    with attached_transaction(WORK/'extracted.db',WORK/'main.db') as c:
        task=save_hold(c,project,{'source':'agent_local_source_adjudication','original_task':original,
          'reason':'Preserve original partial-overlap evidence; local source audit resolves grid connection company false developer conflict.'})
    plan=build_coordination_plan(WORK/'extracted.db',WORK/'main.db',{'task_id':task,'decision':'existing',
      'canonical_id':'OMN-WD-0002','reviewer':'agent_local_source_adjudication',
      'evidence_review_note':'identity-audit.md: exact JBB Ali/Jalan Bani Bu Ali, same three-company EPC consortium, 16 turbines and award-to-construction chronology. Historical grid company is connection operator, not developer. Preserve 120MW old /123MW new without arithmetic overwrites.'})
    audit.dump(OUT/'oman-coordination-plan.json',plan)
    result=apply_coordination_plan(plan,OUT/'oman-backup')
    repeat=apply_coordination_plan(plan,OUT/'oman-backup')
    assert repeat['status']=='already_applied'
    audit.dump(OUT/'oman-result.json',{'raw_id':raw['id'],'url':url,'source_id_map':idmap,'result':result,'repeat':repeat})


def historical(filename='historical-consolidated-patches.json'):
    from s02a_duplicate_merger.tools.coordination import attached_transaction
    if Path(filename).name != filename or not filename.endswith('.json'):raise ValueError('Expected local patch filename')
    files=[OUT/filename]
    patches=[p for file in files for p in audit.read(file)['patches']]
    applied=[]
    with attached_transaction(WORK/'extracted.db',WORK/'main.db') as c:
        for p in patches:
            schema={'source':'extracted','main':'main'}[p['database']]
            table=p['table'];pk=p['pk']
            cursor=c.execute(f'SELECT * FROM {audit.qi(schema)}.{audit.qi(table)} WHERE '+' AND '.join(audit.qi(k)+'=?' for k in pk),list(pk.values()))
            found=cursor.fetchone()
            if found is None and p['before'] is None:
                values={**p['after'],**pk}
                c.execute(f'INSERT INTO {audit.qi(schema)}.{audit.qi(table)} ('+','.join(map(audit.qi,values))+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
                applied.append({'database':schema,'table':table,'pk':pk,'action':'insert'})
                continue
            if found is None:raise ValueError(f'Missing historical target {pk}')
            old=dict(zip([d[0] for d in cursor.description],found))
            if all(old.get(k)==v for k,v in p['after'].items()):continue
            if p['before'] is None:raise ValueError(f'Insert ID collision {schema}.{table} {pk}')
            if not all(old.get(k)==v for k,v in p['before'].items()):raise ValueError(f'Stale historical patch {schema}.{table} {pk}')
            c.execute(f'UPDATE {audit.qi(schema)}.{audit.qi(table)} SET '+','.join(audit.qi(k)+'=?' for k in p['after'])+' WHERE '+' AND '.join(audit.qi(k)+'=?' for k in pk),[*p['after'].values(),*pk.values()])
            applied.append({'database':schema,'table':table,'pk':pk})
        for schema in ('main','extracted'):audit.checks(c,schema)
    audit.dump(OUT/(Path(filename).stem+'-apply-result.json'),{'applied':applied,'patch_count':len(patches),'source_files':{str(f):audit.digest(f) for f in files},'external_calls':0})


def holds():
    from s02a_duplicate_merger.tools.coordination import attached_transaction,save_hold
    decisions=audit.read(OUT/'historical-save-holds.json')['holds']
    results=[]
    with attached_transaction(WORK/'extracted.db',WORK/'main.db') as c:
        for decision in decisions:
            ids=decision['source_ids']
            rows=c.execute('SELECT id,publication_status,imported_to_s02a FROM extracted.extracted_projects WHERE id IN ('+','.join('?' for _ in ids)+')',ids).fetchall()
            assert len(rows)==len(ids) and all(r[1]=='pending_review' and r[2]==0 for r in rows)
            task=save_hold(c,decision['project'],decision['evidence'])
            assert save_hold(c,decision['project'],decision['evidence'])==task
            results.append({'task_id':task,'source_ids':ids,'repeat_same_task':True})
    audit.dump(OUT/'historical-holds-result.json',results)


def protected():
    assert audit.snapshot({k:ROOT/v for k,v in audit.DBS.items()})==audit.read(OUT/'production-before.json')
    assert audit.assets()==audit.read(OUT/'assets-before.json')
    return {'production_unchanged':True,'public_cache_ledgers_unchanged':True,'external_calls':0}


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline recovery forbids network')):
        if sys.argv[1]=='prepare':prepare()
        elif sys.argv[1]=='kenya':kenya()
        elif sys.argv[1]=='sarimay':sarimay()
        elif sys.argv[1]=='oman':oman()
        elif sys.argv[1]=='historical':historical(sys.argv[2] if len(sys.argv)>2 else 'historical-consolidated-patches.json')
        elif sys.argv[1]=='holds':holds()
        elif sys.argv[1]=='diff':print(json.dumps(diff(),ensure_ascii=False))
        else:raise ValueError('Use prepare or diff')
        print(protected())
