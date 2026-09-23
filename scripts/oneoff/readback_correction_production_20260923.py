"""Read-only business acceptance after the approved production correction."""
import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import apply_production_pilot_20260923 as audit
from recover_corrections_20260923 import ROOT, OUT
COMMIT=OUT/'production-commit-20260923-authorized-v2'
BASE=COMMIT/'backup'

def run():
    paths={k:ROOT/v for k,v in audit.DBS.items()}
    with (OUT/'export/events_export.csv').open(encoding='utf-8-sig',newline='') as f:
        event_ids={r['event_id'] for r in csv.DictReader(f)}
    # Confirm the resolved target rather than treating every project in a URL as identical.
    historical=audit.read(OUT/'historical-audit.json')
    restored_sources=[]
    with closing(audit.ro(paths['extracted'])) as s,closing(audit.ro(paths['main'])) as m:
        s.row_factory=m.row_factory=sqlite3.Row
        for record in historical['records']:
            if record['category'] not in ('a','a_nonproject'):continue
            source=s.execute('SELECT * FROM extracted_projects WHERE id=?',(record['row_id'],)).fetchone()
            assert (source['publication_status'],source['imported_to_s02a'],source['excluded'])==('eligible',1,0)
            if record['category']=='a':
                target=m.execute('SELECT * FROM project_events WHERE id=?',(record['resolved_event_id'],)).fetchone()
                assert target['canonical_id']==record['resolved_canonical_id']
                urls=json.loads(target['source_links'])
            else:
                target=m.execute('SELECT * FROM non_project_records WHERE id=?',(record['non_project_id'],)).fetchone()
                urls=[target['source_link']]
                remarks=target['additional_remarks'] or ''
                if remarks.startswith('{'):urls+=json.loads(remarks).get('source_urls',[])
            assert source['source_link'] in urls,record['row_id']
            restored_sources.append(record['row_id'])
    audit.dump(COMMIT/'historical-direct-readback.json',{'scope':203,'restored_count':len(restored_sources),'restored_source_ids':restored_sources,'exact_url_target_mapping':True})
    with closing(audit.ro(paths['main'])) as c:
        assert c.execute('SELECT capacity_mw FROM non_project_records WHERE id=1580').fetchone()==(None,)
        assert c.execute("SELECT total_investment_usd FROM project_events WHERE id='IDN-BM-0015-02'").fetchone()==(163.0,)
        assert c.execute("SELECT implementing_entity FROM chinese_entities WHERE id=6745").fetchone()==('Unknown',)
        withheld_events={r[0] for r in c.execute("SELECT id FROM project_events WHERE publication_status IN ('pending_review','excluded')")}
        assert not (withheld_events & event_ids)
        for event_id in ('KHM-PV-0022-01','KHM-BM-0002-01','KHM-ES-0003-01','KHM-ES-0004-01'):
            relations=c.execute("SELECT entity_id,group_entity_id FROM chinese_entities WHERE event_id=? AND (publication_status IS NULL OR publication_status='eligible')",(event_id,)).fetchall()
            assert len(relations)==1 and all(v in (None,'') for v in relations[0]),'Unresolved subsidiary must not be counted twice or receive a guessed ID'
    restored={r['canonical_id'] for r in audit.read(ROOT/'output/entity-s1-recovery-20260917/restore-apply.json')['candidates']}
    with closing(audit.ro(BASE/'main.db')) as b,closing(audit.ro(paths['main'])) as c:
        for table in ('canonical_projects','project_events','chinese_entities'):
            cols=[r[1] for r in audit.info(b,'main',table)]
            condition={'canonical_projects':'id','project_events':'canonical_id','chinese_entities':'event_id'}[table]
            keys=restored if table!='chinese_entities' else {r[0] for r in b.execute('SELECT id FROM project_events WHERE canonical_id IN ('+','.join('?' for _ in restored)+')',sorted(restored))}
            query='SELECT '+','.join(map(audit.qi,cols))+' FROM '+audit.qi(table)+' WHERE '+audit.qi(condition)+' IN ('+','.join('?' for _ in keys)+') ORDER BY id'
            assert b.execute(query,sorted(keys)).fetchall()==c.execute(query,sorted(keys)).fetchall(),'Prior restored13 modified: '+table
    with closing(audit.ro(paths['extracted'])) as c:
        c.row_factory=sqlite3.Row
        coordination=[]
        for rid in (11749,11750):
            hold=c.execute('SELECT publication_status,imported_to_s02a FROM extracted_projects WHERE id=?',(rid,)).fetchone()
            assert tuple(hold)==('pending_review',0)
        for task in c.execute('SELECT * FROM s02a_coordination'):
            saved=json.loads(task['source_rows_json'])
            for source in saved:
                current=dict(c.execute('SELECT * FROM extracted_projects WHERE id=?',(source['id'],)).fetchone())
                expected=dict(source)
                if task['status']=='resolved':expected['imported_to_s02a']=1
                assert current==expected, f"Coordination source drift: {source['id']}"
            coordination.append({'task_id':task['task_id'],'status':task['status'],'source_ids':[r['id'] for r in saved]})
    with closing(audit.ro(paths['review'])) as r, closing(audit.ro(paths['extracted'])) as s:
        pointer,url=r.execute('SELECT canonical_id,source_link FROM review_queue WHERE id=214').fetchone()
        assert str(pointer)=='11852'
        assert s.execute('SELECT source_link FROM extracted_projects WHERE id=11852').fetchone()[0]==url
        assert url.endswith('/98142.html')
        assert r.execute('SELECT COUNT(*) FROM discarded_records WHERE id=4790').fetchone()[0]==1
    assert audit.snapshot(paths)==audit.read(OUT/'production-correction-delta.json')['candidate']
    assert audit.assets()==audit.read(COMMIT/'assets-before.json')
    value={'historical_restored_sources':len(restored_sources),'exact_url_target_mapping':True,
        'review214_source11852_url_match':True,'original_discard4790_preserved':True,
        'prior_restored13_unchanged':True,'coordination':coordination,
        'withheld_event_ids':sorted(withheld_events),'exact_verified_export_input_match':True,
        'production_read_only_check':True,'public_cache_ledgers_unchanged':True}
    audit.dump(COMMIT/'business-readback.json',value)
    print(json.dumps(value,ensure_ascii=False))

if __name__=='__main__':run()
