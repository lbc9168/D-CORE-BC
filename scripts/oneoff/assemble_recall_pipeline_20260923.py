"""Copy a completed recorded replay into a new normal-pipeline staging workspace.

No responses or publication decisions are changed. Only copied import flags are
reset; all expected article URLs must already be represented before acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from entity_recovery_validation import ROOT, fingerprints, write_json
from verify_recall_isolation_20260923 import backup


def hashes(folder):
    return {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ('projects.db','review.db')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--replay',default='final-recorded')
    parser.add_argument('--work',default='final-work')
    args=parser.parse_args();root=args.run_root.resolve()
    source=(root/args.replay).resolve();target=(root/args.work).resolve()
    if not root.is_relative_to(ROOT/'output') or not source.is_relative_to(root) or not target.is_relative_to(root):
        parser.error('All paths must remain within the isolated run root')
    if target.exists():parser.error('Destination already exists; use a new work name')
    samples=json.loads((root/'frozen-25.json').read_text(encoding='utf-8'))['samples']
    samples=[s for s in samples if s['group']!='holdout']
    results=json.loads((source/'results.json').read_text(encoding='utf-8'))
    errors=[r for r in results if 'error' in r]
    if errors:raise RuntimeError(f'Replay contains unresolved extraction failures: {[r["source"]["id"] for r in errors]}')
    if {r['source']['url'] for r in results}!={s['source']['url'] for s in samples}:
        raise RuntimeError('Replay must cover exactly the frozen diagnostic twenty articles')
    processed=set()
    with sqlite3.connect((source/'projects.db').as_uri()+'?mode=ro',uri=True) as c:
        processed.update(r[0] for r in c.execute('SELECT DISTINCT source_link FROM extracted_projects'))
    with sqlite3.connect((source/'review.db').as_uri()+'?mode=ro',uri=True) as c:
        processed.update(r[0] for r in c.execute('SELECT source_link FROM no_chinese_records UNION SELECT source_link FROM discarded_records'))
    expected={s['source']['url'] for s in samples}
    if not expected<=processed:raise RuntimeError('Unrepresented URLs would trigger new extraction; refuse to assemble')
    before=fingerprints();source_before=hashes(source)
    target.mkdir()
    try:
        backup(source/'projects.db',target/'projects.db');backup(source/'review.db',target/'review.db')
        with sqlite3.connect(target/'projects.db') as c:
            row_count=c.execute('SELECT COUNT(*) FROM extracted_projects').fetchone()[0]
            c.execute('UPDATE extracted_projects SET imported_to_s02a=0')
            assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        write_json(target/'assembly-manifest.json',dict(source=str(source),source_hashes=source_before,source_unchanged=source_before==hashes(source),copied_rows=row_count,processed_frozen_urls=len(expected),raw_ids=[s['raw_id'] for s in samples],main_database='Not copied; normal pipeline creates a new empty main DB.',changed_fields=['isolated extracted_projects.imported_to_s02a reset to 0'],external_requests=0,
            next_command=f'validate_recall_restoration_20260923.py --out {root} --phase targeted --name final-pipeline --ids '+ ' '.join(str(s['raw_id']) for s in samples)+f' --work {args.work}'))
        assert source_before==hashes(source)
    finally:
        after=fingerprints();write_json(target/'assembly-source-verification.json',dict(before=before,after=after,unchanged=before==after));assert before==after

if __name__=='__main__':main()
