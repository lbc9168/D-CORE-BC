"""Apply the user-approved MF audit, retaining source rows and stable IDs.

Dry-run by default. The third (boundary) group is deliberately untouched.
Run before offline s03 export; no model calls or online deployment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUSIONS = {
    "IND-MF-0005": "烧碱/PVC生产，不是新能源制造",
    "VNM-MF-0002": "轮胎制造",
    "IDN-MF-0005": "印刷厂房", "IDN-MF-0006": "洗衣机/冰箱制造",
    "IDN-MF-0007": "普通包装厂房", "IDN-MF-0008": "食品加工",
    "IDN-MF-0014": "食品加工产业园", "IDN-MF-0017": "OKI化学品及水处理配套安装",
    "IDN-MF-0022": "煤焦堆场", "IDN-MF-0029": "PP-R/PE/PVC管材制造",
    "IDN-MF-0031": "普通纯碱生产", "IDN-MF-0032": "普通溴素/氢溴酸生产",
    "THA-MF-0001": "通信光模块，非光伏组件",
    "THA-MF-0005": "PCB制造", "THA-MF-0007": "家电智能控制器制造",
    "THA-MF-0008": "PCB用玻纤制造", "SAU-MF-0013": "钢铁厚板制造",
    "TUR-MF-0007": "瓦楞纸制造", "IRQ-MF-0001": "磷肥生产",
    "MAR-MF-0007": "磷化工", "EGY-MF-0011": "磷肥工业园",
    "BRA-MF-0004": "尿素/液氨化肥生产", "EGY-MF-0012": "家电洗碗机制造",
    "KAZ-MF-0006": "普通铝制品；同文风电已记录为KAZ-WD-0038",
    "UZB-MF-0002": "非制造业；纳沃伊光储已有UZB-HB-0005/UZB-HB-0026，保留本行来源供查重",
    "USA-MF-0010": "PCS产品进入美国市场，未披露当地建厂",
    "EGY-MF-0014": "Ras Shukeir风电项目60套塔筒供货，未披露当地建厂；保留供货来源",
}
PV = {
    "BRA-MF-0002": (35., "巴西玲珑轮胎配套35MW光伏项目", "Linglong Tire Brazil 35MW Solar PV Project", None, "拟开发", "项目拟建设的35MW光伏发电站"),
    "SRB-MF-0001": (24., "塞尔维亚玲珑轮胎配套24MWp分布式光伏项目", "Linglong Tire Serbia 24MWp Distributed Solar PV Project", "分布式", "拟开发", "分布式光伏发电站，安装总容量为24MWp"),
    "SAU-MF-0002": (None, "沙特信义玻璃工厂屋顶光伏项目", "Saudi Xinyi Glass Factory Rooftop Solar PV Project", "分布式", "签约", "两条浮法玻璃生产线及其配套公辅设施及屋顶光伏电站"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(conn: sqlite3.Connection) -> dict:
    """Capture all rows so the audit can prove unrelated records are unchanged."""
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {t: [dict(r) for r in conn.execute('SELECT * FROM "' + t.replace('"', '""') + '"')] for t in tables}


def run(db: Path, apply: bool) -> dict:
    db = db.resolve()
    if not db.is_relative_to(ROOT) or not db.is_file() or db.suffix != ".db":
        raise ValueError("Expected an existing workspace .db file")
    before_hash = digest(db)
    conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    before = snapshot(conn)
    projects = {r['id']: r for r in before['canonical_projects']}
    for pid in EXCLUSIONS.keys() | PV.keys():
        if pid not in projects or projects[pid]['category'] not in {"新能源制造业", "光伏"}:
            raise ValueError(f"Unexpected project identity/category: {pid}")
        if len([r for r in before['project_events'] if r['canonical_id'] == pid]) != 1:
            raise ValueError(f"Expected one audited event: {pid}")
    plan = {'excluded': EXCLUSIONS, 'pv': PV, 'before_sha256': before_hash}
    if not apply:
        conn.close()
        assert digest(db) == before_hash
        return plan
    directory = db.parent / 'backups' / ('mf_scope_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    directory.mkdir(parents=True, exist_ok=False)
    backup = directory / db.name
    with sqlite3.connect(backup) as target:
        conn.backup(target)
    target.close()
    conn.close()
    logging.basicConfig(filename=directory / 'apply.log', level=logging.INFO, encoding='utf-8', force=True)
    logging.info('Source %s sha256=%s backup=%s', db, before_hash, backup)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    with conn:
        conn.executemany('UPDATE canonical_projects SET excluded=1, excluded_reason=? WHERE id=?',
                         [('mf-audit-2026-09-06: ' + reason, pid) for pid, reason in EXCLUSIONS.items()])
        for pid, (mw, cn, en, sub, stage, evidence) in PV.items():
            conn.execute('UPDATE canonical_projects SET category=?, project_name=?, project_name_en=?, subcategory=?, technology_tags_json=? WHERE id=?',
                         ('光伏', cn, en, sub, '[]', pid))
            note = 'mf-audit-2026-09-06: 仅保留配套光伏；原工业项目摘要和来源保留。整体工厂投资、融资与工期不可作为光伏子项事实。'
            if mw is None:
                note += ' 屋顶光伏容量未披露，待补证；不推算。'
            conn.execute('''UPDATE project_events SET project_stage=?, capacity_mw=?, capacity_mw_raw=?,
                capacity_details=?, pv_capacity_mw=?, generation_sources_json='["光伏"]',
                storage_status='not_mentioned', capacity_source=?, energy_composition_evidence=?,
                production_capacity_value=NULL, production_capacity_unit=NULL, production_capacity_raw=NULL,
                total_investment_usd=NULL, total_investment_usd_raw=NULL, investment_date=NULL,
                contract_amount_usd=NULL, contract_amount_usd_raw=NULL,
                financing_amount_usd=NULL, financing_amount_usd_raw=NULL,
                financing_model='Unknown', financing_model_details='',
                planned_start_date=NULL, planned_complete_date=NULL, additional_remarks=?, needs_manual_review=?
                WHERE canonical_id=?''',
                (stage, mw, str(mw) if mw is not None else '', evidence, mw, 'manual' if mw is not None else None, evidence, note, int(mw is None), pid))
        after = snapshot(conn)
        changes = []
        allowed_projects = EXCLUSIONS.keys() | PV.keys()
        for table, old_rows in before.items():
            new_rows = after[table]
            assert len(old_rows) == len(new_rows), table
            for old, new in zip(old_rows, new_rows):
                if old == new:
                    continue
                assert (table == 'canonical_projects' and old['id'] in allowed_projects) or (table == 'project_events' and old['canonical_id'] in PV), (table, old)
                assert old['id'] == new['id']
                changes.append({'table': table, 'id': old['id'], 'before': old, 'after': new})
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        assert all(r['excluded'] == 1 for r in after['canonical_projects'] if r['id'] in EXCLUSIONS)
        assert all(r['category'] == '光伏' for r in after['canonical_projects'] if r['id'] in PV)
    conn.close()
    result = {**plan, 'after_sha256': digest(db), 'backup': str(backup), 'changes': changes,
              'unchanged_other_rows': True, 'unchanged_ids_counts_and_entities': True}
    (directory / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    logging.info('Verified %d row changes; SHA256=%s', len(changes), result['after_sha256'])
    logging.shutdown()
    return {k: v for k, v in result.items() if k != 'changes'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 's02a_duplicate_merger/data/db/bri_re_database.db')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.apply), ensure_ascii=False, indent=2))
