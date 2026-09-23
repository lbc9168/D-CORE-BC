"""Prepare eight frozen articles offline; --execute is a separately authorized action.

Never writes production. All retries count against a new durable SDK request ledger.
Existing successful extraction URLs and unresolved coordination items use normal skips.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import functools
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
MODULES = ('pipeline', 's01a_news_crawler', 's01b_project_extractor',
           's02a_duplicate_merger', 's02b_entity_normalizer', 's03_data_exporter')
sys.path[:0] = [str(ROOT), *[str(ROOT / m / 'src') for m in MODULES]]
DEFAULT_OUT = ROOT / 'output/pipeline-production-readiness-20260923-144217'
LIMITS = {'extraction': 112, 'dedup': 40, 'translation': 8}
ALLOWED = {('gemini', 'gemini-2.5-flash-lite'), ('deepseek', 'deepseek-v4-flash')}


def dump(path, value):
    """Atomic local replacement prevents half-written ledgers after interruption."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def ro(path):
    return sqlite3.connect(f'file:{Path(path).as_posix()}?mode=ro', uri=True)


def protection(audit):
    paths = [ROOT / p for p in audit['hash_before']]
    paths += [ROOT / 'output/pipeline-recall-restoration-20260923-123237/calls.json']
    for folder in ('supporting_docs', 's04_web_visualization/public/data', 's03_data_exporter/data'):
        paths.extend(p for p in (ROOT / folder).rglob('*') if p.is_file())
    # Include SQLite journals, if any; source connections never enter write mode.
    for p in list(paths):
        if p.suffix == '.db':
            paths.extend(Path(str(p) + suffix) for suffix in ('-wal', '-shm') if Path(str(p) + suffix).exists())
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths)) if p.is_file()}


def code_hashes():
    paths = [Path(__file__)]
    for module in MODULES:
        paths.extend(p for p in (ROOT / module / 'src').rglob('*')
                     if p.is_file() and p.suffix in {'.py', '.json', '.txt', '.md'})
    paths.extend(p for p in ROOT.glob('bri_*.py'))
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def route():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from s01b_project_extractor.llm.client import build_stage_router_from_env
    router = build_stage_router_from_env(_client_factory=lambda c: {'provider': c.provider, 'model': c.model})
    value = {'default': router._default, 'stages': router._by_stage,
             'merge_s34': os.getenv('S01B_MERGE_S34'),
             'deepseek_endpoint': os.getenv('DEEPSEEK_BASE_URL') or os.getenv('DS_BASE_URL') or 'https://api.deepseek.com'}
    for item in [value['default'], *value['stages'].values()]:
        if (item['provider'], item['model']) not in ALLOWED:
            raise RuntimeError('Route outside the two proposed existing model endpoints')
    for stage in ('s1', 's2'):
        if value['stages'].get(stage, value['default']) != {'provider': 'gemini', 'model': 'gemini-2.5-flash-lite'}:
            raise RuntimeError(f'Unexpected route for {stage}')
    for stage in ('s34', 's5'):
        if value['stages'].get(stage, value['default']) != {'provider': 'deepseek', 'model': 'deepseek-v4-flash'}:
            raise RuntimeError(f'Unexpected route for {stage}')
    endpoint = urlparse(value['deepseek_endpoint'])
    if endpoint.scheme != 'https' or endpoint.hostname != 'api.deepseek.com' or value['merge_s34'] != '1':
        raise RuntimeError('Endpoint or combined-stage configuration differs from proposal')
    return value


def prepare(out, prepared='prepared-pilot-v3'):
    audit_file = out / 'production-input-audit.json'
    audit = read(audit_file)
    before = protection(audit)
    samples = audit['proposed_first_batch']
    ids, urls = [s['id'] for s in samples], {s['url'] for s in samples}
    if len(ids) != 8 or len(set(ids)) != 8 or len(urls) != 8 or urls.intersection(audit['prior_isolated_urls']):
        raise RuntimeError('Expected eight unique new articles, excluding old 25')
    for rel, tables in [('s01b_project_extractor/data/projects.db', ('extracted_projects',)),
                        ('s01b_project_extractor/data/review.db', ('no_chinese_records', 'discarded_records', 'review_queue'))]:
        with ro(ROOT / rel) as conn:
            for table in tables:
                columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                field = next((f for f in ('source_link', 'source_url', 'url') if f in columns), None)
                if field is None:
                    raise RuntimeError(f'Missing source URL column in {table}')
                existing = {r[0] for r in conn.execute(f'SELECT "{field}" FROM {table}')}
                if urls.intersection(existing):
                    raise RuntimeError(f'Proposed URLs already handled in {table}')
    frozen_route = route()  # Config factory only: constructs no provider client.
    work = out / prepared
    work.mkdir(exist_ok=False)
    source = ROOT / audit['isolated_source']
    with ro(source) as src, sqlite3.connect(work / 'raw.db') as dst:
        src.row_factory = sqlite3.Row
        rows = [dict(src.execute('SELECT * FROM raw_news WHERE id=?', (i,)).fetchone()) for i in ids]
        for sample, row in zip(samples, rows):
            if any(row[k] != sample[k] for k in ('id', 'url', 'title', 'publish_date')):
                raise RuntimeError('Article changed since input audit')
        schema = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_news'").fetchone()[0]
        dst.execute(schema)
        fields = list(rows[0])
        quoted = ','.join('"' + f + '"' for f in fields)
        dst.executemany(f'INSERT INTO raw_news ({quoted}) VALUES ({",".join("?" for _ in fields)})',
                        [tuple(row[f] for f in fields) for row in rows])
    with ro(ROOT / 's02a_duplicate_merger/data/db/bri_re_database.db') as src, sqlite3.connect(work / 'main.db') as dst:
        src.backup(dst)
    support = work / 'supporting_docs'
    support.mkdir()
    for name in ('company_name_cache.json', 'company_hierarchy.json', 'company_hierarchy_v2.json', 'soe.md', 'residual_translation_cache.json'):
        if (ROOT / 'supporting_docs' / name).exists():
            shutil.copy2(ROOT / 'supporting_docs' / name, support / name)
    (work / 'responses').mkdir()
    dump(work / 'calls.json', [])
    dump(work / 'samples.json', rows)
    dump(work / 'manifest.json', {'status': 'prepared_only_not_authorized', 'external_calls': 0,
         'samples_sha256': digest(work / 'samples.json'), 'audit_sha256': digest(audit_file),
         'code_sha256': code_hashes(), 'route': frozen_route, 'protected': before,
         'limits': {'total': 160, 'per_article_across_resume': 14, **LIMITS},
         'concurrency': {'articles': 2, 'dedup': 2}, 'sdk_retries': 0,
         'source_raw': str(source), 'initial_main_sha256': digest(work / 'main.db'),
         'initial_raw_sha256': digest(work / 'raw.db'),
         'initial_support': {p.name: digest(p) for p in support.iterdir()}})
    after = protection(audit)
    if before != after:
        raise RuntimeError('Protected sources changed during preparation')
    with ro(work / 'main.db') as conn:
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Main copy integrity failure')
    dump(work / 'prepare-verification.json', {'external_calls': 0, 'source_unchanged': before == after,
                                            'raw_count': len(rows), 'main_copy_integrity': 'ok'})
    return work


class Ledger:
    """Every SDK attempt consumes a slot before network; started/failed count forever."""
    def __init__(self, work, allowed_ids):
        self.work, self.ids = Path(work), set(allowed_ids)
        self.calls = read(self.work / 'calls.json')
        self.lock = threading.Lock()

    def invoke(self, category, ids, provider, model, request, call, *, stage=None):
        ids = sorted(set(ids))
        if not ids or set(ids) - self.ids or category not in LIMITS or (provider, model) not in ALLOWED:
            raise RuntimeError('Unattributed or out-of-scope SDK call blocked')
        with self.lock:
            if len(self.calls) >= 160 or sum(r['category'] == category for r in self.calls) >= LIMITS[category]:
                raise RuntimeError('Cumulative request/category limit exhausted')
            if any(sum(i in r['raw_ids'] for r in self.calls) >= 14 for i in ids):
                raise RuntimeError('Cumulative per-article request limit exhausted')
            number = len(self.calls) + 1
            request_path = self.work / 'responses' / f'request-{number:04}.json'
            dump(request_path, request)
            record = {'number': number, 'category': category, 'stage': stage or category, 'raw_ids': ids, 'provider': provider,
                      'model': model, 'status': 'started', 'started': time.time(), 'request_sha256': digest(request_path)}
            self.calls.append(record)
            dump(self.work / 'calls.json', self.calls)
        try:
            response = call()
            payload = response.model_dump(mode='json')
            dump(self.work / 'responses' / f'response-{number:04}.json', payload)
            raw_text = response.text if provider == 'gemini' else response.choices[0].message.content
            with (self.work / 'responses' / f'response-{number:04}.txt').open('x', encoding='utf-8') as stream:
                stream.write(raw_text or '')
            usage = response.usage_metadata if provider == 'gemini' else response.usage
            with self.lock:
                record.update(status='completed', usage=usage.model_dump(mode='json') if usage else None)
            return response
        except Exception as exc:
            # Audit boundary: rethrow unchanged; never turn a transport error into a business verdict.
            with self.lock:
                record.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            with self.lock:
                record['finished'] = time.time()
                dump(self.work / 'calls.json', self.calls)


def execute(out, export=False, prepared='prepared-pilot-v3'):
    work = out / prepared
    manifest, samples = read(work / 'manifest.json'), read(work / 'samples.json')
    audit = read(out / 'production-input-audit.json')
    if (manifest['code_sha256'] != code_hashes() or manifest['route'] != route()
            or manifest['audit_sha256'] != digest(out / 'production-input-audit.json')
            or manifest['samples_sha256'] != digest(work / 'samples.json')
            or manifest['protected'] != protection(audit)):
        raise RuntimeError('Frozen code, route, audit, samples or protected production sources drifted; re-prepare and review')
    if not read(work / 'calls.json'):
        if digest(work / 'main.db') != manifest['initial_main_sha256'] or {
            p.name: digest(p) for p in (work / 'supporting_docs').iterdir() if p.is_file()
        } != manifest['initial_support']:
            raise RuntimeError('Prepared main copy or supporting cache changed before first execution')
    with ro(work / 'raw.db') as conn:
        conn.row_factory = sqlite3.Row
        current = {r['id']: dict(r) for r in conn.execute('SELECT * FROM raw_news')}
        if set(current) != {r['id'] for r in samples} or any(
            current[r['id']][f] != r[f] for r in samples for f in ('url', 'title', 'content', 'publish_date')):
            raise RuntimeError('Frozen article content changed')
    active = work / '.active.lock'
    with active.open('x', encoding='utf-8') as stream:
        stream.write(str(os.getpid()))
    before = protection(audit)
    try:
        _execute_pipeline(work, samples, export)
    finally:
        after = protection(audit)
        dump(work / 'execute-invariance.json', {'before': before, 'after': after, 'unchanged': before == after})
        active.unlink()
        if before != after:
            raise RuntimeError('Protected source/cache/export changed')


def _execute_pipeline(work, samples, export):
    from google import genai
    from google.genai import models, types
    import openai
    from openai.resources.chat.completions import Completions
    from s01b_project_extractor.llm.client import LLMClient
    from s01b_project_extractor.tools import project_extractor_tool as writer
    from s01b_project_extractor import main as bulk
    from s02a_duplicate_merger.tools import duplicate_merger_tool as merger
    from bri_pipeline.flow import PipelineConfig, run_pipeline
    ledger, local = Ledger(work, [r['id'] for r in samples]), threading.local()
    url_ids = {r['url']: r['id'] for r in samples}
    by_article = {(r['title'], r['content'], r['publish_date']): r['id'] for r in samples}
    original_gemini, original_chat = models.Models.generate_content, Completions.create
    original_client, original_openai = genai.Client, openai.OpenAI.__init__
    original_extract, original_complete = writer.extract_one, LLMClient.complete

    def recorded(provider, model, request, invoke):
        return ledger.invoke(getattr(local, 'category', ''), getattr(local, 'ids', []), provider, model, request, invoke,
                             stage=getattr(local, 'stage', ''))

    def gemini(self, **kw):
        config = kw.get('config')
        request = {'model': kw['model'], 'contents': kw.get('contents'),
                   'config': config.model_dump(mode='json') if hasattr(config, 'model_dump') else config}
        return recorded('gemini', kw['model'], request, lambda: original_gemini(self, **kw))

    def chat(self, *args, **kw):
        endpoint = urlparse(str(self._client.base_url))
        if endpoint.scheme != 'https' or endpoint.hostname != 'api.deepseek.com':
            raise RuntimeError('Unexpected DeepSeek endpoint')
        return recorded('deepseek', kw['model'], kw, lambda: original_chat(self, *args, **kw))

    def client(*args, **kw):
        if args or kw.get('vertexai') or kw.get('project'):
            raise RuntimeError('Only existing Gemini developer API is allowed')
        opts = kw.get('http_options')
        if opts is not None and getattr(opts, 'base_url', None):
            raise RuntimeError('Custom Gemini endpoint not authorized')
        if any(os.getenv(k) for k in ('GOOGLE_GEMINI_BASE_URL', 'GOOGLE_VERTEX_BASE_URL', 'GOOGLE_GENAI_USE_VERTEXAI')):
            raise RuntimeError('Gemini endpoint override is not authorized')
        if opts is None:
            opts = types.HttpOptions(timeout=120000)
        opts.retry_options = types.HttpRetryOptions(attempts=1)
        kw['http_options'] = opts
        return original_client(*args, **kw)

    def openai_init(self, *args, **kw):
        kw['max_retries'] = 0
        return original_openai(self, *args, **kw)

    def extract(title, content, date, **kw):
        # Writer performs name translation after extract_one returns on this same
        # worker. Retain attribution until the worker starts its next article.
        local.ids, local.category, local.stage = [by_article[(title, content, date)]], 'translation', 'name_translation'
        return original_extract(title, content, date, **kw)

    def complete(self, prompt, *, stage, json_mode=True):
        previous = (local.category, local.stage)
        local.category, local.stage = 'extraction', stage
        try:
            return original_complete(self, prompt, stage=stage, json_mode=json_mode)
        finally:
            local.category, local.stage = previous

    def bound(fn, projects):
        @functools.wraps(fn)
        def wrapped(*args, **kw):
            records = projects(args, kw)
            ids = {url_ids[u] for r in records for u in r.get('source_links', []) if u in url_ids}
            old = (getattr(local, 'ids', []), getattr(local, 'category', ''), getattr(local, 'stage', ''))
            local.ids, local.category, local.stage = sorted(ids), 'dedup', fn.__name__
            try:
                return fn(*args, **kw)
            finally:
                local.ids, local.category, local.stage = old
        return wrapped

    cfg = PipelineConfig(skip_s01a=True, raw_news_db_path=str(work / 'raw.db'),
        projects_db_path=str(work / 'projects.db'), review_db_path=str(work / 'review.db'),
        db_path=str(work / 'main.db'), raw_ids=[r['id'] for r in samples], max_extract_workers=2,
        run_export=export, export_allow_model_calls=False, export_dir=str(work / 'export'),
        export_web_dir=str(work / 'web/data'), export_cache_path=str(work / 'province-cache.json'))
    for name in ('run_entity_intake', 'run_location_normalize', 'run_developer_normalize', 'run_en_backfill',
                 'run_txmf_backfill', 'run_residual_translate', 'run_storage_subcat_backfill', 'run_energy_taxonomy_shadow'):
        setattr(cfg, name, False)
    logging.basicConfig(filename=work / 'run.log', encoding='utf-8', level=logging.INFO)
    with ExitStack() as stack:
        for obj, name, value in (
            (models.Models, 'generate_content', gemini), (Completions, 'create', chat),
            (genai, 'Client', client), (openai.OpenAI, '__init__', openai_init),
            (writer, 'extract_one', extract), (LLMClient, 'complete', complete),
            (writer, '_SUPPORTING_DOCS', work / 'supporting_docs'), (bulk, '_RUN_LOG_PATH', work / 'run.log'),
            (merger, 'DEDUP_WORKERS', 2), (merger, '_infer_location_from_name', lambda *a, **k: 0),
            (merger, '_apply_location_norm', lambda *a, **k: 0),
            (merger, '_llm_confirm_raising', bound(merger._llm_confirm_raising, lambda a, k: [a[2], a[3]])),
            (merger, '_llm_confirm_canonical', bound(merger._llm_confirm_canonical, lambda a, k: [a[2]])),
            (merger, '_merge_group', bound(merger._merge_group, lambda a, k: a[2]))):
            stack.enter_context(patch.object(obj, name, value))
        result = run_pipeline(cfg)
    dump(work / 'result.json', result)
    with ro(work / 'main.db') as conn:
        dump(work / 'database-checks.json', {'integrity': conn.execute('PRAGMA integrity_check').fetchall(),
                                           'foreign_keys': conn.execute('PRAGMA foreign_key_check').fetchall()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--prepared', default='prepared-pilot-v3', help='New child directory name for preparation, same name for execution/resume')
    parser.add_argument('--execute', action='store_true', help='Requires separate user authorization; consumes new budget')
    parser.add_argument('--export', action='store_true', help='Optional offline export within prepared-pilot only')
    args = parser.parse_args()
    out = args.out.resolve()
    if not out.is_relative_to(ROOT / 'output'):
        parser.error('Output must be under this workspace output')
    if not args.prepared or Path(args.prepared).name != args.prepared or args.prepared in {'.', '..'}:
        parser.error('--prepared must be a single child directory name')
    if args.export and not args.execute:
        parser.error('--export requires --execute')
    if args.execute:
        execute(out, args.export, args.prepared)
    else:
        print(prepare(out, args.prepared))


if __name__ == '__main__':
    main()
