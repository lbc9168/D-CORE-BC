"""Zero-network cross-module acceptance of the reviewed isolated recovery."""
import csv
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import socket
import sqlite3
import sys
from unittest.mock import patch
from recover_corrections_20260923 import ROOT, OUT, WORK, BASE, audit, protected
sys.path.insert(0,str(ROOT/'scripts'))


def run():
    from bri_pipeline.flow import PipelineConfig,run_pipeline
    from regenerate_exports_offline import regenerate
    paths={k:WORK/f'{k}.db' for k in audit.DBS}
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
    audit.dump(OUT/'historical-direct-readback.json',{'scope':203,'restored_count':len(restored_sources),'restored_source_ids':restored_sources,'exact_url_target_mapping':True})
    before=audit.snapshot(paths)
    # Successful URLs must skip extraction, including the corrected S1 challenge.
    config=PipelineConfig(skip_s01a=True,raw_news_db_path=str(paths['raw']),
        projects_db_path=str(paths['extracted']),review_db_path=str(paths['review']),db_path=str(paths['main']),
        raw_ids=[28080,28087,28088],run_entity_intake=False,run_location_normalize=False,
        run_developer_normalize=False,run_en_backfill=False,run_txmf_backfill=False,
        run_residual_translate=False,run_storage_subcat_backfill=False,run_energy_taxonomy_shadow=False,
        run_export=False,start_date='2026-09-16',end_date='2026-09-23')
    result=run_pipeline(config)
    assert audit.snapshot(paths)==before,'Repeat changed source/business rows'
    export=OUT/'export';web=OUT/'preview-data'
    export.mkdir(exist_ok=True)
    counts=regenerate(paths['main'],export,web,
        province_seed_db=ROOT/'s03_data_exporter/data/output/bri_re_export.db',
        external_references=ROOT/'s02e_source_finder/data/export-development-validation-v1/external-references.json')
    baseline_export=OUT/'baseline-export';baseline_export.mkdir(exist_ok=True)
    baseline_counts=regenerate(BASE/'main.db',baseline_export,OUT/'baseline-preview-data',
        province_seed_db=ROOT/'s03_data_exporter/data/output/bri_re_export.db',
        external_references=ROOT/'s02e_source_finder/data/export-development-validation-v1/external-references.json')
    with (export/'events_export.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    def read_csv(path):
        with path.open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))
    def difference(old,new):
        a=Counter(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in old)
        b=Counter(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in new)
        return {'before_count':len(old),'after_count':len(new),
            'added':[json.loads(r) for r,n in (b-a).items() for _ in range(n)],
            'removed':[json.loads(r) for r,n in (a-b).items() for _ in range(n)]}
    audit.dump(OUT/'export-delta.json',{'same_code_baseline_vs_corrected':difference(read_csv(baseline_export/'events_export.csv'),rows),
        'public_snapshot_vs_corrected':difference(read_csv(ROOT/'s04_web_visualization/public/data/events_export.csv'),rows),
        'note':'Diagnostic only. Public delta also includes previously authorized production intake and earlier export-policy changes; do not publish as a pure correction delta.'})
    event_ids={r['event_id'] for r in rows}
    assert 'IDN-BM-0015-01' not in event_ids and 'IDN-BM-0015-02' in event_ids
    assert 'UZB-PV-0013-01' not in event_ids and 'UZB-PV-0013-02' in event_ids
    assert 'UZB-PV-0024-01' not in event_ids
    assert 'OMN-WD-0002-02' in event_ids and not any(r['canonical_id']=='OMN-WD-0005' for r in rows)
    assert not any('98142' in str(r) for r in rows),'Equipment order leaked as power plant'
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
    checks={}
    for label,path in paths.items():
        with closing(audit.ro(path)) as c:checks[label]=audit.checks(c,'main')
    assert audit.snapshot(paths)==before,'Export changed source data'
    value={'pipeline_repeat':result,'repeat_complete_rows_unchanged':True,'exports':counts,'same_code_baseline_exports':baseline_counts,
        'withheld_event_ids':sorted(withheld_events),'coordination':coordination,'checks':checks,
        'kenya_nonproject_only':True,'bogor_old_event_hidden_new_event_visible':True,
        'sarimay_old_id_reused':True,'oman_old_id_reused':True,'prior_restored13_unchanged':True,**protected()}
    audit.dump(OUT/'verification.json',value)
    print(json.dumps({k:v for k,v in value.items() if k!='pipeline_repeat'},ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Verification forbids external requests')):run()
