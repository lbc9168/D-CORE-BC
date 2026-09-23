"""Apply only the hash-bound correction approved by the user on 2026-09-23.

The transaction body is retained from the reviewed isolated rehearsal. Production
paths require an explicit mode; prepare creates fresh backups and rehearses it.
No extraction, export, cache replacement or external requests are performed.
"""
from contextlib import closing
from datetime import datetime
import json
import logging
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import zipfile
from unittest.mock import patch
import apply_production_pilot_20260923 as audit
from recover_corrections_20260923 import OUT

APPROVED_HASH = 'db542f691301a10688842714e3e48d396fa61911c61ea894f5368efdb16c22a1'
PRODUCTION = {k: audit.ROOT / v for k,v in audit.DBS.items()}

def apply_delta(plan, paths, *, fail_before_commit=False, production=False):
    """Require complete before/after snapshots; atomic DDL + exact row changes."""
    expected = {k: (audit.ROOT / v).resolve() for k, v in audit.DBS.items()}
    if production:
        if {k: Path(v).resolve() for k, v in paths.items()} != expected:
            raise ValueError('Only the four approved production paths are allowed')
        if plan != audit.read(OUT / 'production-correction-delta.json') or audit.digest(OUT / 'production-correction-delta.json') != APPROVED_HASH:
            raise ValueError('Production delta differs from approved artifact')
    elif any(not Path(p).resolve().is_relative_to(OUT.resolve()) for p in paths.values()):
        raise ValueError('Rehearsal is restricted to isolated output paths')
    current=audit.snapshot(paths)
    if current==plan['candidate']:return {'status':'already_applied'}
    if current!=plan['baseline']:raise ValueError('Stale or partial baseline; regenerate delta')
    modes={}
    try:
        for label,path in paths.items():
            with closing(sqlite3.connect(path)) as c:
                modes[label]=c.execute('PRAGMA journal_mode').fetchone()[0]
                assert c.execute('PRAGMA journal_mode=DELETE').fetchone()[0]=='delete'
        with closing(sqlite3.connect(paths['main'])) as c:
            c.execute('PRAGMA foreign_keys=ON')
            for label,path in paths.items():
                if label!='main':c.execute(f'ATTACH DATABASE ? AS {audit.qi(label)}',(str(path),))
                c.execute(f'PRAGMA {audit.qi(label)}.synchronous=FULL')
            c.execute('BEGIN IMMEDIATE')
            try:
                for label in paths:
                    if audit.snap(c,label)!=plan['baseline'][label]:raise ValueError('Baseline changed under lock')
                for addition in plan['schema_additions']:c.execute(addition['sql'])
                for label,tables in plan['changes'].items():
                    order={'canonical_projects':0,'project_events':1,'chinese_entities':2}
                    for table,changes in sorted(tables.items(),key=lambda item:(order.get(item[0],3),item[0])):
                        if table=='sqlite_sequence':continue
                        for change in changes:
                            old,new=change['before'],change['after']
                            if new is None:raise ValueError('Deletion is not part of this correction')
                            cols=list(new)
                            if old is None:
                                c.execute(f'INSERT INTO {audit.qi(label)}.{audit.qi(table)} ('+','.join(map(audit.qi,cols))+') VALUES ('+','.join('?' for _ in cols)+')',[new[k] for k in cols])
                            else:
                                keys=[r[1] for r in audit.info(c,label,table) if r[5]]
                                changeset={k:v for k,v in new.items() if old.get(k)!=v}
                                if changeset:
                                    c.execute(f'UPDATE {audit.qi(label)}.{audit.qi(table)} SET '+','.join(audit.qi(k)+'=?' for k in changeset)+' WHERE '+' AND '.join(audit.qi(k)+'=?' for k in keys),[*changeset.values(),*[old[k] for k in keys]])
                for label in paths:
                    audit.checks(c,label)
                    if audit.snap(c,label)!=plan['candidate'][label]:raise ValueError('Exact result differs: '+label)
                if fail_before_commit:raise RuntimeError('injected_before_commit')
                c.commit()
            except (sqlite3.Error, ValueError, RuntimeError, AssertionError):
                c.rollback()
                raise
    finally:
        for label,mode in modes.items():
            if mode not in {'wal','delete'}:raise ValueError('Unexpected journal mode')
            with closing(sqlite3.connect(paths[label])) as c:
                assert c.execute('PRAGMA journal_mode='+mode).fetchone()[0]==mode
    return {'status':'applied'}


def frozen_plan():
    manifest = audit.read(OUT / 'manifest.json')
    for group in ('artifacts', 'code'):
        for rel, digest in manifest[group].items():
            path = (OUT if group == 'artifacts' else audit.ROOT) / rel
            audit.require(audit.digest(path) == digest, 'Frozen evidence drift: ' + rel)
    audit.require(audit.digest(OUT / 'production-correction-delta.json') == APPROVED_HASH, 'Unapproved delta')
    return audit.read(OUT / 'production-correction-delta.json')


def prepare(destination):
    plan = frozen_plan()
    audit.require(audit.snapshot(PRODUCTION) == plan['baseline'], 'Production baseline drift')
    destination.mkdir(exist_ok=False)
    logging.basicConfig(filename=destination/'execution.log', level=logging.INFO, force=True)
    logging.info('User authorized production backfill; begin fresh backups')
    assets = audit.assets()
    audit.dump(destination/'assets-before.json', assets)
    backups = {k: audit.backup(p, destination/'backup'/f'{k}.db') for k,p in PRODUCTION.items()}
    export = audit.ROOT/'s03_data_exporter/data/output/bri_re_export.db'
    backups['export'] = audit.backup(export, destination/'backup/export.db')
    with zipfile.ZipFile(destination/'protected-assets.zip','x',compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in assets:
            archive.write(audit.ROOT/rel, rel)
    import hashlib
    with zipfile.ZipFile(destination/'protected-assets.zip') as archive:
        for rel, digest in assets.items():
            audit.require(hashlib.sha256(archive.read(Path(rel).as_posix())).hexdigest()==digest, 'Asset backup mismatch: '+rel)
    paths={k:destination/'rehearsal'/f'{k}.db' for k in PRODUCTION}
    for k,p in paths.items(): audit.backup(destination/'backup'/f'{k}.db',p)
    try:
        apply_delta(plan,paths,fail_before_commit=True)
    except RuntimeError as exc:
        if str(exc)!='injected_before_commit': raise
    else:
        raise AssertionError('Failure injection did not execute')
    audit.require(audit.snapshot(paths)==plan['baseline'],'Rollback mismatch')
    first=apply_delta(plan,paths)
    repeat=apply_delta(plan,paths)
    audit.require(first['status']=='applied' and repeat['status']=='already_applied','Rehearsal failed')
    audit.require(audit.snapshot(paths)==plan['candidate'],'Candidate mismatch')
    audit.require(audit.snapshot(PRODUCTION)==plan['baseline'],'Production changed during prepare')
    audit.require(audit.assets()==assets,'Protected assets changed')
    audit.dump(destination/'manifest.json',{'delta_sha256':APPROVED_HASH,'entrypoint_sha256':audit.digest(Path(__file__)),
        'authorization':'User: 好的，我给你授权来进行生产回填','backups':backups,
        'rollback_passed':True,'rehearsal_first':first,'rehearsal_repeat':repeat,'external_calls':0})
    logging.info('Backups verified; rollback, exact apply and repeat no-op passed')
    return {'prepared':str(destination),'rollback_passed':True,'rehearsal_repeat':repeat}


def apply(destination):
    plan=frozen_plan()
    manifest=audit.read(destination/'manifest.json')
    audit.require(manifest['delta_sha256']==APPROVED_HASH and manifest['entrypoint_sha256']==audit.digest(Path(__file__)), 'Commit manifest drift')
    audit.require(manifest['rollback_passed'] and manifest['rehearsal_repeat']['status']=='already_applied','Missing rehearsal')
    for item in manifest['backups'].values():
        audit.require(audit.digest(item['path'])==item['sha256'],'Backup drift')
    assets=audit.read(destination/'assets-before.json')
    audit.require(audit.assets()==assets,'Assets changed before application')
    logging.basicConfig(filename=destination/'execution.log',level=logging.INFO,force=True)
    logging.info('Begin authorized production attached transaction')
    result=apply_delta(plan,PRODUCTION,production=True)
    logging.info('Transaction returned %s',result)
    audit.dump(destination/'transaction-result.json',result)
    post=audit.snapshot(PRODUCTION)
    audit.require(post==plan['candidate'],'Post-commit readback mismatch')
    repeat=apply_delta(plan,PRODUCTION,production=True)
    audit.require(repeat['status']=='already_applied','Repeat was not no-op')
    checks={}
    for k,p in PRODUCTION.items():
        with closing(audit.ro(p)) as c: checks[k]=audit.checks(c,'main')
    audit.require(audit.assets()==assets,'Protected assets changed after application')
    audit.dump(destination/'post-write-readback.json',post)
    result.update({'production_applied':True,'repeat':repeat,'exact_candidate_match':True,'checks':checks,
        'counts':{t:post['main'][t]['count'] for t in ('canonical_projects','project_events','chinese_entities')},
        'public_cache_ledgers_unchanged':True,'external_calls':0,'metadata_last_updated':audit.read(audit.ROOT/'s04_web_visualization/public/data/metadata.json')['last_updated']})
    audit.dump(destination/'production-result.json',result)
    logging.info('Exact readback, integrity, foreign keys and protected assets passed')
    return result


if __name__=='__main__':
    mode=sys.argv[1]
    destination=Path(sys.argv[2]).resolve()
    audit.require(destination.parent==OUT.resolve() and destination.name.startswith('production-commit-'),'Invalid commit directory')
    with patch.object(socket.socket,'connect',side_effect=AssertionError('External requests forbidden')):
        if mode=='prepare': result=prepare(destination)
        elif mode=='apply': result=apply(destination)
        else: raise ValueError('Expected prepare or apply')
    print(json.dumps(result,ensure_ascii=False))
