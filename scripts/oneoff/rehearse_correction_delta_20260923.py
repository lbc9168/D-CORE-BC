"""Rehearse the exact reviewed correction delta, never target production here."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import socket
import sys
from unittest.mock import patch
import apply_production_pilot_20260923 as audit
from recover_corrections_20260923 import OUT, BASE, WORK, protected


def apply_delta(plan, paths, *, fail_before_commit=False):
    """Require complete before/after snapshots; atomic DDL + exact row changes."""
    if any(not Path(p).resolve().is_relative_to(OUT.resolve()) for p in paths.values()):
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


def run():
    plan=audit.read(OUT/'production-correction-delta.json')
    directory=sys.argv[1] if len(sys.argv)>1 else 'rehearsal'
    if Path(directory).name!=directory or not directory.startswith('rehearsal'):raise ValueError('Expected isolated rehearsal directory name')
    paths={k:OUT/directory/f'{k}.db' for k in audit.DBS}
    for label,path in paths.items():audit.backup(BASE/f'{label}.db',path)
    try:apply_delta(plan,paths,fail_before_commit=True)
    except RuntimeError as e:
        if str(e)!='injected_before_commit':raise
    else:raise AssertionError('Failure injection was not reached')
    assert audit.snapshot(paths)==plan['baseline']
    first=apply_delta(plan,paths)
    second=apply_delta(plan,paths)
    assert first['status']=='applied' and second['status']=='already_applied'
    assert audit.snapshot(paths)==plan['candidate']
    checks={}
    for k,p in paths.items():
        with closing(audit.ro(p)) as c:checks[k]=audit.checks(c,'main')
    result={'rollback_schema_rows_sequences':True,'first':first,'repeat':second,'exact_candidate_match':True,'checks':checks,**protected()}
    audit.dump(OUT/'rehearsal.json',result)
    print(json.dumps(result))


if __name__=='__main__':
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls')):run()
