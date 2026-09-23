"""Apply the reviewed eight-article delta only; no extraction, export or network.

Prepare freezes the approved rows and baseline. Rehearse proves rollback, exact
inserts, old-row preservation and repeat no-op. Apply requires that evidence and
fresh verified backups, then commits the same four-database transaction.
"""
from contextlib import closing, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'output/pipeline-production-readiness-20260923-144217'
DEFAULT = ROOT / 'output/production-pilot-commit-20260923-161131-v2'
DBS = {'main': 's02a_duplicate_merger/data/db/bri_re_database.db',
       'raw': 's01a_news_crawler/data/raw_news.db',
       'extracted': 's01b_project_extractor/data/projects.db',
       'review': 's01b_project_extractor/data/review.db'}
TABLES = {'main': ['canonical_projects', 'project_events', 'chinese_entities'],
          'raw': ['raw_news'], 'extracted': ['extracted_projects', 's02a_coordination', 's02a_coordination_sources'],
          'review': ['discarded_records', 'review_queue']}
IDENTITY = ['entity_id', 'entity_name', 'group_entity_id', 'raw_entity_name', 'identity_status', 'entity_evidence_json']
COUNTS = {'canonical_projects': 4, 'project_events': 5, 'chinese_entities': 7,
          'raw_news': 8, 'extracted_projects': 11, 's02a_coordination': 1,
          's02a_coordination_sources': 2, 'discarded_records': 1, 'review_queue': 8}
KEYS = {'s02a_coordination': 'task_id', 's02a_coordination_sources': 'source_id'}
NEW_SQL = {
    's02a_coordination': """CREATE TABLE extracted.s02a_coordination (
      task_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('pending','resolved')),
      project_json TEXT NOT NULL, source_rows_json TEXT NOT NULL, evidence_json TEXT NOT NULL,
      created_at TEXT NOT NULL, resolution_json TEXT, resolved_at TEXT)""",
    's02a_coordination_sources': """CREATE TABLE extracted.s02a_coordination_sources (
      source_id INTEGER PRIMARY KEY REFERENCES extracted_projects(id),
      task_id TEXT NOT NULL REFERENCES s02a_coordination(task_id))"""}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)


def qi(name):
    require(bool(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name)), 'Invalid SQL identifier')
    return '"' + name + '"'


def names(conn, schema):
    return [r[0] for r in conn.execute(f"SELECT name FROM {qi(schema)}.sqlite_master WHERE type='table' ORDER BY name")]


def info(conn, schema, table):
    return [list(r) for r in conn.execute(f'PRAGMA {qi(schema)}.table_info({qi(table)})')]


def rowhash(conn, schema, table, columns, omitted=()):
    cols = ','.join(qi(c) for c in columns)
    key = KEYS.get(table, 'id')
    pk = [r[1] for r in info(conn, schema, table) if r[5]]
    order = ','.join(qi(c) for c in pk) if pk else 'rowid'
    where = f' WHERE {qi(key)} NOT IN ({",".join("?" for _ in omitted)})' if omitted else ''
    h = hashlib.sha256()
    count = 0
    for row in conn.execute(f'SELECT {cols} FROM {qi(schema)}.{qi(table)}{where} ORDER BY {order}', tuple(omitted)):
        h.update(json.dumps(list(row), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
        h.update(b'\n')
        count += 1
    return {'count': count, 'sha256': h.hexdigest()}


def snap(conn, schema='main'):
    result = {}
    for table in names(conn, schema):
        columns = info(conn, schema, table)
        result[table] = {'columns': columns, **rowhash(conn, schema, table, [r[1] for r in columns])}
        if table == 'sqlite_sequence':
            result[table]['values'] = dict(conn.execute(f'SELECT name,seq FROM {qi(schema)}.sqlite_sequence'))
    return result


def snapshot(paths):
    out = {}
    for schema, path in paths.items():
        with closing(ro(path)) as conn:
            out[schema] = snap(conn)
    return out


def checks(conn, schema):
    value = {'integrity': [r[0] for r in conn.execute(f'PRAGMA {qi(schema)}.integrity_check')],
             'foreign_keys': [list(r) for r in conn.execute(f'PRAGMA {qi(schema)}.foreign_key_check')]}
    require(value == {'integrity': ['ok'], 'foreign_keys': []}, f'Database check failed: {schema}: {value}')
    return value


def backup(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open('xb'):
        pass
    with closing(ro(src)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)
        require(snap(a) == snap(b), f'Backup logical mismatch: {src}')
        return {'source': str(src), 'path': str(dst), 'sha256': digest(dst), 'checks': checks(b, 'main'), 'tables': snap(b)}


def asset_paths():
    # Includes production export DB/caches and both historical call ledgers.
    paths = []
    for rel in ('supporting_docs', 's03_data_exporter/data', 's04_web_visualization/public/data'):
        paths.extend(p for p in (ROOT / rel).rglob('*') if p.is_file())
    paths.extend([SOURCE / 'prepared-pilot-v3/calls.json',
                  ROOT / 'output/pipeline-recall-restoration-20260923-123237/calls.json'])
    return sorted(set(paths))


def assets():
    return {str(p.relative_to(ROOT)): digest(p) for p in asset_paths()}


def rows_from_delta(delta):
    rows = {schema: {} for schema in DBS}
    for schema, tables in TABLES.items():
        for table in tables:
            rows[schema][table] = [dict(r) for r in (delta['main'][table]['inserted'] if schema == 'main'
                                   else delta['proposed_production_source_rows'][table])]
            require(len(rows[schema][table]) == COUNTS[table], f'Unexpected count {table}')
            rr = rows[schema][table]
            key = KEYS.get(table, 'id')
            require(len({r[key] for r in rr}) == len(rr), f'Duplicate IDs: {table}')
            require(all(set(r) == set(rr[0]) for r in rr), f'Inconsistent columns: {table}')
    require(all(not d['updated'] and not d['deleted_ids'] for d in delta['main'].values()), 'Only inserts authorized')
    require({r['id'] for r in rows['main']['canonical_projects']} == {'COD-TX-0009', 'UGA-TX-0009', 'GMB-TX-0003', 'SGP-TX-0002'}, 'Canonical scope drift')
    require({r['id'] for r in rows['main']['chinese_entities']} == set(range(6870, 6876)) | {6877}, 'Relationship scope drift')
    require(rows['main']['chinese_entities'][-1]['involvement_type'] == 'Unknown', 'Role review missing')
    raw = rows['raw']['raw_news']
    expected = {r['id']: r for r in read(SOURCE / 'prepared-pilot-v3/samples.json')}
    maps = delta['id_maps']['raw_news']
    for mapping in maps:
        new = next(r for r in raw if r['id'] == mapping['proposed_production_id'])
        orig = expected[mapping['isolated_id']]
        require(all(new[k] == orig[k] for k in ('url', 'title', 'content', 'publish_date')), 'Article content changed')
    src = {r['id']: r for r in rows['extracted']['extracted_projects']}
    source_map = {str(r['isolated_id']): str(r['proposed_production_id']) for r in delta['id_maps']['extracted_projects']}
    # Legacy review.canonical_id actually points to extracted_projects.id, not
    # a canonical project. The approved preview left these seven local IDs unmapped.
    for item in rows['review']['review_queue']:
        old_id = item['canonical_id']
        if old_id is not None:
            require(str(old_id) in source_map, 'Unknown review source pointer')
            item['canonical_id'] = source_map[str(old_id)]
            require(src[int(item['canonical_id'])]['source_link'] == item['source_link'], 'Review pointer URL mismatch')
    task = rows['extracted']['s02a_coordination'][0]
    held = sorted(r['source_id'] for r in rows['extracted']['s02a_coordination_sources'])
    require(held == [11850, 11851] and task['status'] == 'pending', 'Hold membership/status drift')
    project = json.loads(task['project_json'])
    require(sorted(project['_source_record_ids']) == held, 'Hold project ID mapping mismatch')
    require(json.loads(task['source_rows_json']) == [src[i] for i in held], 'Hold source snapshot mapping mismatch')
    task_hash = hashlib.sha256(json.dumps(held, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]
    require(task['task_id'] == 'identity-' + task_hash, 'Hold task hash mismatch')
    for r in src.values():
        require(r['imported_to_s02a'] == (0 if r['id'] in held else 1), 'Import flag mismatch')
    challenge = [r for r in rows['review']['review_queue'] if r['source_link'].endswith('/98142.html')]
    require(len(challenge) == 1 and challenge[0]['reason_type'] == 'status_challenge', 'Kenya challenge missing')
    # Production retains legacy TEXT numeric columns. Make SQLite's lossless
    # affinity conversion explicit, including snapshots used by targeted recovery.
    with closing(ro(ROOT / DBS['extracted'])) as conn:
        textcols = {r[1] for r in info(conn, 'main', 'extracted_projects') if r[2].upper() == 'TEXT'}
    for row in src.values():
        for col in textcols & row.keys():
            if isinstance(row[col], (int, float)):
                require(col in ('total_investment_usd', 'capacity_mw'), f'Unexpected numeric TEXT conversion: {col}')
                row[col] = str(row[col])
    task['source_rows_json'] = json.dumps([src[i] for i in held], ensure_ascii=False, sort_keys=True, allow_nan=False)
    return rows


def prepare(out):
    out.mkdir(exist_ok=False)
    delta_path = SOURCE / 'reviewed-candidate/production-delta.json'
    delta = read(delta_path)
    for rel, h in delta['production_baseline_hashes'].items():
        require(digest(ROOT / rel) == h, f'Production baseline drift: {rel}')
    rows = rows_from_delta(delta)
    paths = {s: ROOT / p for s, p in DBS.items()}
    baseline = snapshot(paths)
    for s, path in paths.items():
        with closing(ro(path)) as conn:
            checks(conn, 'main')
            for t, rr in rows[s].items():
                if t not in baseline[s]:
                    require(t in NEW_SQL, 'Unexpected new table')
                    continue
                key = KEYS.get(t, 'id')
                for row in rr:
                    require(not conn.execute(f'SELECT 1 FROM {qi(t)} WHERE {qi(key)}=?', (row[key],)).fetchone(), f'ID exists {t}/{row[key]}')
                if 'source_link' in rr[0] or 'url' in rr[0]:
                    col = 'url' if t == 'raw_news' else 'source_link'
                    require(all(not conn.execute(f'SELECT 1 FROM {qi(t)} WHERE {qi(col)}=?', (r[col],)).fetchone() for r in rr), f'URL already exists {t}')
    frozen = {'authorized_at': datetime.now(timezone.utc).isoformat(),
              'authorization': '用户：好的，我授权你做受控的小批量入库。仅数据库精确增量；无外部调用/公开导出/历史排除恢复/部署。',
              'delta_sha256': digest(delta_path), 'script_sha256': digest(Path(__file__)),
              'rows': rows, 'baseline': baseline, 'protected_assets': assets(),
              'production_file_hashes': {s: digest(p) for s, p in paths.items()},
              'production_paths': {s: str(p) for s, p in paths.items()},
              'review_pointer_corrections': [{'review_id': r['id'], 'before': old['canonical_id'], 'after': r['canonical_id']}
                for r, old in zip(rows['review']['review_queue'], delta['proposed_production_source_rows']['review_queue'])
                if r['canonical_id'] != old['canonical_id']],
              'affinity_normalizations': [{'source_id': r['id'], 'field': c, 'before': old[c], 'after': r[c]}
                for r, old in zip(rows['extracted']['extracted_projects'], delta['proposed_production_source_rows']['extracted_projects'])
                for c in r if r[c] != old[c]],
              'journal_modes': {s: journal_mode(p) for s, p in paths.items()},
              'metadata': read(ROOT / 's04_web_visualization/public/data/metadata.json')}
    dump(out / 'manifest.json', frozen)
    shutil.copy2(delta_path, out / 'approved-delta.json')
    logging.info('Prepared %s; no production changes', out)


def frozen(out):
    m = read(out / 'manifest.json')
    require(m['script_sha256'] == digest(Path(__file__)), 'Application code drift; review and re-prepare')
    require(m['delta_sha256'] == digest(out / 'approved-delta.json'), 'Frozen delta drift')
    require(m['rows'] == rows_from_delta(read(out / 'approved-delta.json')), 'Frozen rows drift')
    return m


def journal_mode(path):
    with closing(ro(path)) as conn:
        return conn.execute('PRAGMA journal_mode').fetchone()[0]


def validate(conn, m, applied):
    result = {}
    for schema, baseline in m['baseline'].items():
        target = m['rows'][schema]
        current = set(names(conn, schema))
        require(current == set(baseline) | (set(NEW_SQL) if applied and schema == 'extracted' else set()), f'Table set drift {schema}')
        for table, old in baseline.items():
            newcols = info(conn, schema, table)
            added = IDENTITY + ['publication_status'] if (schema, table) == ('extracted', 'extracted_projects') else IDENTITY if (schema, table) == ('main', 'chinese_entities') else []
            expected_cols = old['columns'] + ([[len(old['columns']) + i, c, 'TEXT', 0, None, 0] for i, c in enumerate(added)] if applied else [])
            require(newcols == expected_cols, f'Schema drift {schema}.{table}')
            if table == 'sqlite_sequence':
                expected_seq = dict(old['values'])
                if applied:
                    for t, rr in target.items():
                        if t in expected_seq:
                            expected_seq[t] = max(expected_seq[t], *(r['id'] for r in rr))
                actual_seq = dict(conn.execute(f'SELECT name,seq FROM {qi(schema)}.sqlite_sequence'))
                require(actual_seq == expected_seq, f'Sequence drift {schema}')
                continue
            omitted = [r[KEYS.get(table, 'id')] for r in target.get(table, [])] if applied else []
            require(rowhash(conn, schema, table, [r[1] for r in old['columns']], omitted) == {k: old[k] for k in ('count', 'sha256')}, f'Old rows changed {schema}.{table}')
            if applied and added:
                where = f'{qi(KEYS.get(table,"id"))} NOT IN ({",".join("?" for _ in omitted)})'
                require(conn.execute(f'SELECT count(*) FROM {qi(schema)}.{qi(table)} WHERE {where} AND ({" OR ".join(qi(c)+" IS NOT NULL" for c in added)})', omitted).fetchone()[0] == 0, 'Historical identity fields backfilled')
        if applied:
            for table, rr in target.items():
                key = KEYS.get(table, 'id')
                columns = list(rr[0])
                for row in rr:
                    actual = conn.execute(f'SELECT {",".join(qi(c) for c in columns)} FROM {qi(schema)}.{qi(table)} WHERE {qi(key)}=?', (row[key],)).fetchone()
                    require(actual is not None and list(actual) == [row[c] for c in columns], f'Inserted row mismatch {schema}.{table}/{row[key]}')
                require(conn.execute(f'SELECT count(*) FROM {qi(schema)}.{qi(table)}').fetchone()[0] == baseline.get(table, {}).get('count', 0) + len(rr), f'Count mismatch {schema}.{table}')
        result[schema] = checks(conn, schema)
    return result


def open_attached(paths, readonly=False):
    conn = ro(paths['main']) if readonly else sqlite3.connect(paths['main'], timeout=2)
    try:
        for schema in ('raw', 'extracted', 'review'):
            location = paths[schema].resolve().as_uri() + '?mode=ro' if readonly else str(paths[schema])
            conn.execute(f'ATTACH DATABASE ? AS {qi(schema)}', (location,))
        return conn
    except sqlite3.Error:
        conn.close()
        raise


class RollbackDrill(Exception):
    pass


def apply(paths, m, inject=False):
    # Repeat is entirely read-only; never infer completion from a file receipt alone.
    with closing(open_attached(paths, True)) as check:
        rr = m['rows']['main']['canonical_projects']
        present = sum(check.execute('SELECT count(*) FROM canonical_projects WHERE id=?', (r['id'],)).fetchone()[0] for r in rr)
        if present:
            require(present == len(rr), 'Partial main insertion detected; refuse automatic continuation')
            validate(check, m, True)
            return {'status': 'already_applied', 'checks': 'all exact rows and old baseline verified', 'external_calls': 0}
        validate(check, m, False)
    modes = {}
    try:
        for schema, path in paths.items():
            with closing(sqlite3.connect(path, timeout=2)) as conn:
                modes[schema] = conn.execute('PRAGMA journal_mode').fetchone()[0]
                require(conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete', 'Cannot acquire DELETE journal window')
        with closing(open_attached(paths)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            for schema in paths:
                conn.execute(f'PRAGMA {qi(schema)}.synchronous=FULL')
            conn.execute('BEGIN IMMEDIATE')
            try:
                # Explicitly obtain write locks for all four files before baseline validation.
                for schema, table in [('main', 'canonical_projects'), ('raw', 'raw_news'), ('extracted', 'extracted_projects'), ('review', 'review_queue')]:
                    conn.execute(f'UPDATE {qi(schema)}.{qi(table)} SET id=id WHERE 0')
                validate(conn, m, False)
                for schema, table, columns in [('main', 'chinese_entities', IDENTITY), ('extracted', 'extracted_projects', IDENTITY + ['publication_status'])]:
                    for col in columns:
                        conn.execute(f'ALTER TABLE {qi(schema)}.{qi(table)} ADD COLUMN {qi(col)} TEXT')
                for sql in NEW_SQL.values():
                    conn.execute(sql)
                for schema, tables in m['rows'].items():
                    for table, rr in tables.items():
                        columns = list(rr[0])
                        conn.executemany(f'INSERT INTO {qi(schema)}.{qi(table)} ({",".join(qi(c) for c in columns)}) VALUES ({",".join("?" for _ in columns)})', [[r[c] for c in columns] for r in rr])
                checked = validate(conn, m, True)
                if inject:
                    raise RollbackDrill('Injected after all schemas/rows and pre-commit validation')
                conn.commit()
            except (sqlite3.Error, RuntimeError, RollbackDrill):
                conn.rollback()
                raise
        with closing(open_attached(paths, True)) as conn:
            validate(conn, m, True)
        return {'status': 'applied', 'checks': checked, 'insert_counts': COUNTS, 'external_calls': 0}
    finally:
        for schema, mode in modes.items():
            require(mode in ('wal', 'delete', 'truncate', 'persist'), 'Unexpected initial journal mode')
            with closing(sqlite3.connect(paths[schema], timeout=2)) as conn:
                require(conn.execute('PRAGMA journal_mode=' + mode).fetchone()[0] == mode, f'Cannot restore journal mode {schema}')


def rehearse(out):
    m = frozen(out)
    require(assets() == m['protected_assets'], 'Protected assets drift')
    production = {s: Path(p) for s, p in m['production_paths'].items()}
    require(snapshot(production) == m['baseline'], 'Production logical drift')
    work = out / 'rehearsal'
    work.mkdir()
    paths = {s: work / (s + '.db') for s in DBS}
    copies = {s: backup(production[s], p) for s, p in paths.items()}
    before = snapshot(paths)
    try:
        apply(paths, m, inject=True)
    except RollbackDrill:
        require(snapshot(paths) == before, 'Injected rollback changed baseline/schema/sequence')
    else:
        raise RuntimeError('Rollback drill did not reach injection')
    result = apply(paths, m)
    applied_before_repeat = snapshot(paths)
    repeated = apply(paths, m)
    require(snapshot(paths) == applied_before_repeat and repeated['status'] == 'already_applied', 'Repeated apply changed rows')
    require(snapshot(production) == m['baseline'], 'Rehearsal touched production')
    require(assets() == m['protected_assets'], 'Rehearsal changed protected assets')
    dump(out / 'rehearsal.json', {'status': 'passed', 'rollback_all_four_databases': True,
          'schema_and_sequences_rolled_back': True, 'result': result, 'repeat': repeated,
          'production_unchanged': True, 'external_calls': 0, 'copies': copies,
          'after_snapshot': applied_before_repeat})
    logging.info('Rehearsal passed: injected rollback, exact inserts, repeat no-op')


def production_apply(out):
    m = frozen(out)
    rehearsal = read(out / 'rehearsal.json')
    require(rehearsal['status'] == 'passed', 'Rehearsal not passed')
    paths = {s: Path(p) for s, p in m['production_paths'].items()}
    require(assets() == m['protected_assets'], 'Protected assets drift')
    with closing(open_attached(paths, True)) as conn:
        count = conn.execute("SELECT count(*) FROM canonical_projects WHERE id IN ('COD-TX-0009','UGA-TX-0009','GMB-TX-0003','SGP-TX-0002')").fetchone()[0]
        if count:
            require(count == 4, 'Partial write detected')
            validate(conn, m, True)
            print('already_applied: verified read-only')
            return
    require(snapshot(paths) == m['baseline'], 'Production drift before backup')
    require({s: digest(p) for s, p in paths.items()} == m['production_file_hashes'], 'Production file drift before backup')
    backup_dir = out / 'production-backup'
    backup_dir.mkdir()
    receipts = {s: backup(p, backup_dir / (s + '.db')) for s, p in paths.items()}
    export = ROOT / 's03_data_exporter/data/output/bri_re_export.db'
    receipts['export'] = backup(export, backup_dir / 'export.db')
    # Full file copies preserve public assets and caches without changing destinations.
    for rel in ('supporting_docs', 's03_data_exporter/data', 's04_web_visualization/public/data'):
        shutil.copytree(ROOT / rel, backup_dir / 'files' / rel)
    copy_hashes = {str(p.relative_to(backup_dir / 'files')): digest(p) for p in (backup_dir / 'files').rglob('*') if p.is_file()}
    require(all(copy_hashes.get(rel) == h for rel, h in m['protected_assets'].items() if not rel.startswith('output')), 'Backup file hash mismatch')
    dump(backup_dir / 'manifest.json', {'databases': receipts, 'files': copy_hashes})
    # Check source baseline once again; attached transaction repeats this under locks.
    require(snapshot(paths) == m['baseline'] and assets() == m['protected_assets'], 'Drift during backup')
    dump(out / 'write-intent.json', {'delta_sha256': m['delta_sha256'], 'backup_manifest': str(backup_dir / 'manifest.json'),
                                    'started': datetime.now(timezone.utc).isoformat(), 'scope': COUNTS})
    result = apply(paths, m)
    require(assets() == m['protected_assets'], 'Protected assets changed after apply')
    with closing(open_attached(paths, True)) as conn:
        validate(conn, m, True)
    result.update(completed=datetime.now(timezone.utc).isoformat(), protected_assets_unchanged=True,
                  backup_manifest=str(backup_dir / 'manifest.json'), production_paths=m['production_paths'],
                  final_file_hashes={s: digest(p) for s, p in paths.items()}, metadata_unchanged=m['metadata'])
    dump(out / 'production-result.json', result)
    logging.info('Production committed and verified: %s', COUNTS)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'rehearse', 'apply'])
    parser.add_argument('--out', type=Path, default=DEFAULT)
    args = parser.parse_args()
    out = args.out.resolve()
    require(out.is_relative_to(ROOT / 'output'), 'Audit output must be under workspace/output')
    if args.mode != 'prepare':
        require(out.is_dir(), 'Missing prepared output')
    # No model clients or network calls are part of this application tool.
    def forbid(*a, **k):
        raise RuntimeError('No external calls permitted by this offline production application')
    with ExitStack() as stack:
        stack.enter_context(patch.object(socket.socket, 'connect', forbid))
        stack.enter_context(patch.object(socket, 'create_connection', forbid))
        if args.mode == 'prepare':
            prepare(out)
        else:
            logging.basicConfig(filename=out / 'apply.log', encoding='utf-8', level=logging.INFO)
            lock = out / '.active.lock'
            with lock.open('x', encoding='utf-8') as stream:
                stream.write(str(__import__('os').getpid()))
            try:
                (rehearse if args.mode == 'rehearse' else production_apply)(out)
            finally:
                lock.unlink()
    print(f'{args.mode}: complete; {out}')


if __name__ == '__main__':
    main()
