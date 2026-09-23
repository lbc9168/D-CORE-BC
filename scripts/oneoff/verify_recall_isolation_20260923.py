"""Prepare bounded historical copies or demonstrate article failure recovery offline.

No network is permitted here. Historical matching itself must use the separate
budgeted runner; recovery reuses saved postprocessed results without regeneration.
"""
from __future__ import annotations
import argparse
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), *[str(ROOT / p / 'src') for p in
    ('pipeline', 's01b_project_extractor', 's02a_duplicate_merger', 's02b_entity_normalizer', 's03_data_exporter')]]
from entity_recovery_validation import fingerprints, write_json


def backup(source, target):
    """SQLite-consistent snapshot, refusing to replace an existing copy."""
    if target.exists():
        raise FileExistsError(target)
    with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)


def rows(path):
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: [dict(r) for r in conn.execute(f'SELECT * FROM "{t}" ORDER BY id')]
                for t in ('canonical_projects', 'project_events', 'chinese_entities', 'non_project_records') if t in tables}


def historical(args, out, samples):
    work = out / 'work'
    work.mkdir()
    expected = {31: 'OMN-WD-0002', 39: 'THA-TH-0001'}
    urls = {s['source']['url'] for s in samples if s['raw_id'] in expected}
    backup(ROOT / 's02a_duplicate_merger/data/db/bri_re_database.db', work / 'main.db')
    before = rows(work / 'main.db')
    write_json(out / 'main-before.json', before)
    backup(args.run_root / 'work/projects.db', work / 'projects.db')
    with sqlite3.connect(work / 'projects.db') as conn:
        placeholders = ','.join('?' for _ in urls)
        conn.execute(f'DELETE FROM extracted_projects WHERE source_link NOT IN ({placeholders})', tuple(urls))
        conn.execute('UPDATE extracted_projects SET imported_to_s02a=0')
        actual_urls = {r[0] for r in conn.execute('SELECT DISTINCT source_link FROM extracted_projects WHERE publication_status="eligible"')}
        if actual_urls != urls:
            raise RuntimeError('Both historical candidates must be eligible before preparing the drill')
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    records=[]
    for raw_id, cid in expected.items():
        cp=next(r for r in before['canonical_projects'] if r['id']==cid)
        events=[r for r in before['project_events'] if r['canonical_id']==cid]
        entities=[r for r in before['chinese_entities'] if r['event_id'] in {e['id'] for e in events}]
        records.append(dict(raw_id=raw_id,expected_canonical_id=cid,canonical=cp,events=events,entities=entities))
    write_json(out / 'targeted-mapping.json', {'targets': records,
        'rule':'Match physical identity, preserve all pre-existing canonical/event/entity IDs. New stage events may be added under same canonical; no rewriting legacy evidence.',
        'unselected_ambiguities': ['raw113 Gobustan legacy developer/entity conflict; do not use name+100MW as identity proof', 'raw25 existing COL-PV-0004 conflates two sites; needs separate historical coordination'],
        'run_command': 'validate_recall_restoration_20260923.py --out RUN_ROOT --phase targeted --name historical-live --ids 31 39 --work historical/work --merge-only',
        'repeat_command':'same runner --phase repeat --name historical-repeat --ids 31 39 --work historical/work --merge-only',
        'actual_external_requests':0})


def recovery(args, out, samples):
    from bri_pipeline import flow
    from s01b_project_extractor import main as bulk
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s01b_project_extractor.llm.client import LLMClient
    from google import genai
    from google.genai import models
    from openai.resources.chat.completions import Completions
    selected = [s for s in samples if s['raw_id'] in (2,31)]
    if len(selected)!=2:
        raise RuntimeError('Frozen recovery article IDs missing')
    records={s['source']['title']:json.loads((args.run_root/'live'/f"article-{s['raw_id']}.json").read_text(encoding='utf-8')) for s in selected}
    source_hashes={f"article-{s['raw_id']}.json":hashlib.sha256((args.run_root/'live'/f"article-{s['raw_id']}.json").read_bytes()).hexdigest() for s in selected}
    with sqlite3.connect(out/'raw.db') as conn:
        conn.execute('CREATE TABLE raw_news(id INTEGER PRIMARY KEY,url TEXT,title TEXT,content TEXT,publish_date TEXT,batch_id TEXT,passed_keyword_filter INTEGER)')
        conn.executemany("INSERT INTO raw_news VALUES(?,?,?,?,?,'recovery',1)",[(s['raw_id'],s['source']['url'],s['source']['title'],s['source']['content'],s['source']['publish_date']) for s in selected])
    phase=['first']; attempts=[]; forbidden_calls=[]
    def forbid(*a,**kw):
        forbidden_calls.append(phase[0]);raise RuntimeError('External request forbidden during zero-call recovery test')
    class NoNetwork:
        @property
        def models(self): return self
        def generate_content(self,*a,**kw): return forbid(*a,**kw)
    def extract(title,content,date,**kw):
        item=records[title];rid=item['source']['id'];attempts.append({'phase':phase[0],'raw_id':rid})
        if phase[0]=='first' and rid==31:
            raise TimeoutError('Injected technical article failure; no service was contacted')
        return deepcopy(item['result'])
    cfg=flow.PipelineConfig(skip_s01a=True,raw_news_db_path=str(out/'raw.db'),projects_db_path=str(out/'projects.db'),review_db_path=str(out/'review.db'),db_path=str(out/'main.db'),max_extract_workers=1,
        run_location_normalize=False,run_developer_normalize=False,run_residual_translate=False,run_export=True,export_dir=str(out/'export'),export_web_dir=str(out/'web/data'),export_cache_path=str(out/'province-cache.json'),export_allow_model_calls=False)
    with ExitStack() as stack:
        for target,name,value in [(writer,'extract_one',extract),(writer,'_resolve_entity_en',lambda n,c:''),(writer,'_resolve_developer_en',lambda n,c:n),(writer,'_save_name_cache',forbid),(genai,'Client',lambda *a,**kw:NoNetwork()),(models.Models,'generate_content',forbid),(Completions,'create',forbid),(LLMClient,'_raw_call',forbid),(bulk,'_RUN_LOG_PATH',out/'run.log')]:
            stack.enter_context(patch.object(target,name,value))
        stack.enter_context(patch.dict(os.environ,{'GEMINI_API_KEY':'offline-unused'}))
        first=flow.run_pipeline(cfg)
        assert first['status']=='partial' and len(first['extraction']['failed_urls'])==1
        phase[0]='resume';resumed=flow.run_pipeline(cfg)
        assert resumed['status']=='completed'
        assert [a['raw_id'] for a in attempts if a['phase']=='resume']==[31]
        stable=rows(out/'main.db')
        phase[0]='repeat';repeated=flow.run_pipeline(cfg)
        assert repeated['status']=='completed'
        assert not [a for a in attempts if a['phase']=='repeat']
        assert rows(out/'main.db')==stable
    with sqlite3.connect(out/'review.db') as conn:
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        failed_url=next(s['source']['url'] for s in selected if s['raw_id']==31)
        exclusions={t:conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE source_link=?',(failed_url,)).fetchone()[0] for t in ('no_chinese_records','discarded_records') if t in tables}
    with sqlite3.connect(out/'main.db') as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchall();fk=conn.execute('PRAGMA foreign_key_check').fetchall()
    assert not forbidden_calls and not any(exclusions.values()) and integrity==[('ok',)] and not fk
    write_json(out/'verification.json',dict(first=first,resume=resumed,repeat=repeated,article_attempts=attempts,source_result_hashes=source_hashes,counts={t:len(v) for t,v in stable.items()},core_rows_unchanged_on_repeat=True,technical_failure_business_exclusions=exclusions,external_calls=0,blocked_network_attempts=forbidden_calls,integrity=integrity,foreign_keys=fk,limitation='Fault injected before writer result; saved postprocessed extraction reused. Does not claim a real service outage or new generation.'))


def verify_historical(args, out, samples):
    before=json.loads((out/'main-before.json').read_text(encoding='utf-8'))
    after=rows(out/'work/main.db')
    expected={31:'OMN-WD-0002',39:'THA-TH-0001'}
    changed=[];missing=[]
    for table, old_rows in before.items():
        new={r['id']:r for r in after[table]}
        for row in old_rows:
            if row['id'] not in new:
                missing.append([table,row['id']]);continue
            differences={k:[v,new[row['id']].get(k)] for k,v in row.items() if new[row['id']].get(k)!=v}
            if differences:changed.append(dict(table=table,id=row['id'],differences=differences))
    matches=[]
    for sample in samples:
        rid=sample['raw_id']
        if rid not in expected:continue
        url=sample['source']['url']
        events=[r for r in after['project_events'] if url in json.loads(r.get('source_links') or '[]')]
        matches.append(dict(raw_id=rid,expected_canonical_id=expected[rid],actual_event_ids=[r['id'] for r in events],actual_canonical_ids=sorted({r['canonical_id'] for r in events}),correct=bool(events) and {r['canonical_id'] for r in events}=={expected[rid]}))
    with sqlite3.connect(out/'work/main.db') as conn:
        integrity=conn.execute('PRAGMA integrity_check').fetchall();fk=conn.execute('PRAGMA foreign_key_check').fetchall()
    result=dict(matches=matches,missing_legacy_ids=missing,changed_legacy_rows=changed,before_counts={t:len(v) for t,v in before.items()},after_counts={t:len(v) for t,v in after.items()},new_canonical_ids=sorted({r['id'] for r in after['canonical_projects']}-{r['id'] for r in before['canonical_projects']}),integrity=integrity,foreign_keys=fk)
    result['passed']=all(m['correct'] for m in matches) and not missing and not changed and not result['new_canonical_ids'] and integrity==[('ok',)] and not fk
    write_json(out/'matching-verification.json',result)
    assert result['passed'], 'Historical drill failed; inspect matching-verification.json'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',type=Path,required=True);parser.add_argument('--mode',choices=['historical','recovery','historical-verify'],required=True)
    args=parser.parse_args();args.run_root=args.run_root.resolve()
    if not args.run_root.is_relative_to(ROOT/'output'):parser.error('Run root must remain under output')
    out=args.run_root/('historical' if args.mode=='historical-verify' else args.mode)
    if args.mode!='historical-verify':out.mkdir(exist_ok=False)
    samples=json.loads((args.run_root/'frozen-25.json').read_text(encoding='utf-8'))['samples']
    before=fingerprints()
    try:
        {'historical':historical,'historical-verify':verify_historical,'recovery':recovery}[args.mode](args,out,samples)
    finally:
        after=fingerprints();write_json(out/f'{args.mode}-source-verification.json',dict(before=before,after=after,unchanged=before==after,external_calls=0));assert before==after

if __name__=='__main__':main()
