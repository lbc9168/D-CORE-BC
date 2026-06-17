"""增量传播 APPLY：把 recheck 的 exclude/involvement 改动写入现有 bri_re_database.db（保留 B4/B5/B6 成果）。
- 43 canonical exclude（42 clean + 6071 weak-name100 + 11114→IRQ-BM-0004 override），跳过 2 设备订单（无 canonical）。
- 22 involvement 更新（exact entity 名匹配），跳过 4411（unmatched）。
先 .bak、单事务、改后核验。UTF-8 输出。"""
import json
import pathlib
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime

from rapidfuzz import fuzz

root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
PDB = root / "s01b_project_extractor" / "data" / "projects.db"
DDB = root / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
BAK = root / "s01b_project_extractor" / "data" / "projects.db.bak_recheck_20260617_080052"
OVERRIDE_EXCLUDE = {11114: "IRQ-BM-0004"}   # 用户确认：巴格达垃圾发电 → IRQ-BM-0004
out = []

pc = sqlite3.connect(str(PDB)); pc.row_factory = sqlite3.Row
ep = {r["id"]: r for r in pc.execute(
    "SELECT id, project_name, country_iso, category, involvement_type, implementing_entity, "
    "source_link, excluded, exclude_source, exclude_reason FROM extracted_projects")}
pc.close()
bc = sqlite3.connect(str(BAK)); bak_inv = {r[0]: r[1] for r in bc.execute("SELECT id, involvement_type FROM extracted_projects")}; bc.close()

excluded_46 = [r for r in ep.values() if (r["excluded"] or 0) == 1 and r["exclude_source"] == "recheck"]
involved_23 = [r for r in ep.values() if (r["involvement_type"] or "") != (bak_inv.get(r["id"]) or "")]

dc = sqlite3.connect(str(DDB)); dc.row_factory = sqlite3.Row
canon = {r["id"]: r for r in dc.execute("SELECT id, project_name, country_iso, category, excluded FROM canonical_projects")}
url_index = defaultdict(set)
for e in dc.execute("SELECT id, canonical_id, source_links FROM project_events"):
    try:
        for u in (json.loads(e["source_links"]) if e["source_links"] else []):
            url_index[u].add((e["id"], e["canonical_id"]))
    except json.JSONDecodeError:
        pass
ent_by_ev = defaultdict(list)
for r in dc.execute("SELECT id, event_id, implementing_entity FROM chinese_entities"):
    ent_by_ev[r["event_id"]].append(r)


def best_canonical(rec):
    cands = {cid for (_e, cid) in url_index.get(rec["source_link"], set())}
    if not cands:
        return None
    filt = [cid for cid in cands if canon.get(cid)
            and (canon[cid]["country_iso"] or "") == (rec["country_iso"] or "")
            and (canon[cid]["category"] or "") == (rec["category"] or "")]
    pool = filt or list(cands)
    return max(pool, key=lambda cid: fuzz.token_set_ratio(rec["project_name"] or "", canon[cid]["project_name"] or ""))


# ── 决定 exclude 目标 canonical ───────────────────────────────────────────────
exclude_targets = {}   # canonical_id -> reason
skipped_exc = []
for rec in excluded_46:
    if rec["id"] in OVERRIDE_EXCLUDE:
        cid = OVERRIDE_EXCLUDE[rec["id"]]
    else:
        cid = best_canonical(rec)
    if cid is None or cid not in canon:
        skipped_exc.append(rec["id"]); continue
    exclude_targets.setdefault(cid, rec["exclude_reason"] or "recheck 二次复核排除")

# ── 决定 involvement 目标 entity ──────────────────────────────────────────────
inv_targets = {}   # entity_id -> new_involvement
skipped_inv = []
for rec in involved_23:
    cands = {eid for (eid, _c) in url_index.get(rec["source_link"], set())}
    best = None
    for eid in cands:
        for ent in ent_by_ev.get(eid, []):
            sc = fuzz.token_set_ratio(rec["implementing_entity"] or "", ent["implementing_entity"] or "")
            if best is None or sc > best[0]:
                best = (sc, ent["id"])
    if best is None or best[0] < 50:
        skipped_inv.append(rec["id"]); continue
    inv_targets[best[1]] = rec["involvement_type"]

# ── 改前快照 ──────────────────────────────────────────────────────────────────
before_excl = dc.execute("SELECT COUNT(*) FROM canonical_projects WHERE excluded=1").fetchone()[0]
net_new = sum(1 for cid in exclude_targets if (canon[cid]["excluded"] or 0) == 0)
out.append(f"exclude 目标 canonical: {len(exclude_targets)}（净新增 excluded={net_new}，已 excluded={len(exclude_targets)-net_new}）；跳过 ep={skipped_exc}")
out.append(f"involvement 目标 entity: {len(inv_targets)}；跳过 ep={skipped_inv}")
out.append(f"改前 canonical excluded=1 总数: {before_excl}")

# ── 备份 + 单事务写 ───────────────────────────────────────────────────────────
relabel_bak = f"{DDB}.bak_pre_incremental_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(str(DDB), relabel_bak)
out.append(f"已备份 → {pathlib.Path(relabel_bak).name}")

try:
    with dc:
        for cid, reason in exclude_targets.items():
            dc.execute("UPDATE canonical_projects SET excluded=1, "
                       "excluded_reason=COALESCE(NULLIF(excluded_reason,''), ?) WHERE id=?", (reason, cid))
        for ent_id, inv in inv_targets.items():
            dc.execute("UPDATE chinese_entities SET involvement_type=? WHERE id=?", (inv, ent_id))
    after_excl = dc.execute("SELECT COUNT(*) FROM canonical_projects WHERE excluded=1").fetchone()[0]
    out.append(f"\n改后 canonical excluded=1 总数: {after_excl}（净增 {after_excl - before_excl}）")
    # 抽验 involvement
    out.append("involvement 抽验（前5 entity）:")
    for ent_id in list(inv_targets)[:5]:
        r = dc.execute("SELECT id, implementing_entity, involvement_type FROM chinese_entities WHERE id=?", (ent_id,)).fetchone()
        out.append(f"  entity {r[0]} {r[1][:18] if r[1] else ''} → involvement={r[2]}")
finally:
    dc.close()

(pathlib.Path(__file__).parent / "_recheck_incremental_apply.txt").write_text("\n".join(out), encoding="utf-8")
print("done")
