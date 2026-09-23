"""Read-only historical follow-up inventory; creates no recovery authorization."""
from pathlib import Path
from collections import Counter,defaultdict
import hashlib,json,sqlite3,sys
from entity_recovery_validation import ROOT,fingerprints,write_json

out=ROOT/'output/pipeline-recall-restoration-20260923-123237/historical-followup'
if (out/'followup.json').exists():raise FileExistsError(out/'followup.json')
before=fingerprints()
intake=json.loads((out/'entity-intake.json').read_text(encoding='utf-8'))
dryrun=json.loads((out/'refresh-dryrun.json').read_text(encoding='utf-8'))
actions={r['row_id']:r for r in dryrun['rows']}
restore=json.loads((ROOT/'output/entity-s1-recovery-20260917/restore-apply.json').read_text(encoding='utf-8'))
restored={r['canonical_id'] for r in restore['candidates']}
def read(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c
with read(ROOT/'s02a_duplicate_merger/data/db/bri_re_database.db') as c:
 canonical={r['id']:dict(r) for r in c.execute('SELECT * FROM canonical_projects')}
 events=[dict(r) for r in c.execute('SELECT * FROM project_events')]
 relations=[dict(r) for r in c.execute('SELECT * FROM chinese_entities')]
url_events=defaultdict(list)
for event in events:
 for url in json.loads(event['source_links'] or '[]'):url_events[url].append(event)
with read(ROOT/'s01b_project_extractor/data/projects.db') as c:
 rows={r['id']:dict(r) for r in c.execute('SELECT * FROM extracted_projects')}
 schema=[r[1] for r in c.execute('PRAGMA table_info(extracted_projects)')]
with read(ROOT/'s01a_news_crawler/data/raw_news.db') as c:
 articles={r['url']:dict(r) for r in c.execute('SELECT id,url,title,publish_date,content,batch_id FROM raw_news')}
 batches=[dict(r) for r in c.execute('SELECT batch_id,COUNT(*) AS rows,MIN(publish_date) AS min_publish_date,MAX(publish_date) AS max_publish_date FROM raw_news GROUP BY batch_id ORDER BY batch_id DESC LIMIT 8')]
checklist=[]
for group in intake['entities']:
 for record in group['records']:
  rid=record['row_id'];row=rows[rid];url=row['source_link'];linked=url_events.get(url,[]);cids=sorted({e['canonical_id'] for e in linked});article=articles.get(url)
  action=actions[rid]['action']
  classification='requires_reextraction_and_existing_id_coordination' if linked else 'requires_reextraction'
  if action=='refresh':classification='saved_evidence_refresh_candidate_requires_review'
  checklist.append(dict(row_id=rid,source_url=url,raw_id=article['id'] if article else None,source_sha256=hashlib.sha256(article['content'].encode()).hexdigest() if article else None,title=article['title'] if article else None,project_name=row['project_name'],raw_entity_name=group['raw_name'],old_exclude_reason=row['exclude_reason'],imported_to_s02a=row['imported_to_s02a'],dryrun_action=action,classification=classification,existing_canonical_ids=cids,existing_event_ids=[e['id'] for e in linked],existing_relation_ids=[r['id'] for r in relations if r['event_id'] in {e['id'] for e in linked}],already_restored_canonical_ids=sorted(set(cids)&restored),next_step='Re-extract in a separately authorized isolated batch; inspect semantic evidence and preserve/co-ordinate old IDs before any production action.',reason='Production legacy extraction schema lacks entity_evidence_json and Participation Assessment. A name or old pending reason cannot stand in for a new model judgment.'))
metadata_path=ROOT/'s04_web_visualization/public/data/metadata.json';metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
protected={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for base in (ROOT/'s04_web_visualization/public/data',ROOT/'s03_data_exporter/data/output') for p in base.rglob('*') if p.is_file()}
uzb=[e for e in events if e['canonical_id']=='UZB-ES-0017'];uzb_urls={u for e in uzb for u in json.loads(e['source_links'] or '[]')}
summary=dict(rows=len(checklist),articles=len({r['source_url'] for r in checklist}),matches_previous_203_146=len(checklist)==203 and len({r['source_url'] for r in checklist})==146,classification_counts=dict(Counter(r['classification'] for r in checklist)),dryrun_actions=dict(Counter(r['dryrun_action'] for r in checklist)),refresh_candidate_rows=dryrun['changed_rows'],all_legacy_rows_lack_semantic_evidence='entity_evidence_json' not in schema,restored_13=[dict(canonical_id=cid,current_excluded=canonical[cid]['excluded'],action='already_restored_do_not_restore_again') for cid in sorted(restored)],uzb_es_0017=dict(canonical=canonical['UZB-ES-0017'],events=uzb,pending_row_ids=[r['row_id'] for r in checklist if r['source_url'] in uzb_urls],action='Retain explicit historical coordination; multi-source/relations must not be copied from isolated IDs.'),metadata_last_updated=metadata.get('last_updated'),production_raw_batches=batches,authorization='Inventory and dry-run only. No production schema migration, refresh, extraction, backfill or restoration authorized by this list.',external_calls=0)
write_json(out/'followup.json',dict(summary=summary,records=checklist))
lines=['# Historical pending follow-up (read-only)','',f"Legacy candidate pool: {summary['rows']} rows / {summary['articles']} URLs, matching the earlier frozen count.",'',f"Current refresh dry-run proposes {dryrun['changed_rows']} row changes; all 203 rows require re-extraction because saved semantic participation evidence is absent.",'',f"Next-step categories: {summary['classification_counts']}",'','The 13 previously restored canonicals remain restored and must not be restored again. UZB-ES-0017 remains a separate historical coordination case.','',f"Production metadata.last_updated: {summary['metadata_last_updated']}. This task did not advance the cutoff.",'','This is an actionable inventory, not approval to recover every row or a claim that all candidates qualify.','', '| Row | Raw ID | Existing canonical | Next step | Source |','|---|---|---|---|---|']
for r in checklist:lines.append(f"| {r['row_id']} | {r['raw_id']} | {','.join(r['existing_canonical_ids'])} | {r['classification']} | {r['source_url']} |")
(out/'followup.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
after=fingerprints();protected_after={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for base in (ROOT/'s04_web_visualization/public/data',ROOT/'s03_data_exporter/data/output') for p in base.rglob('*') if p.is_file()}
initial=json.loads((out.parent/'source-before.json').read_text(encoding='utf-8'))
write_json(out/'source-verification.json',dict(before=before,after=after,unchanged=before==after,unchanged_from_task_start=initial==after,production_exports_unchanged=protected==protected_after,metadata_sha256=hashlib.sha256(metadata_path.read_bytes()).hexdigest(),metadata_last_updated=metadata.get('last_updated'),actual_external_requests=0))
assert before==after==initial and protected==protected_after and metadata.get('last_updated')=='2026-09-15'
print(json.dumps({k:summary[k] for k in ('rows','articles','classification_counts','dryrun_actions','refresh_candidate_rows','metadata_last_updated')},ensure_ascii=False))
