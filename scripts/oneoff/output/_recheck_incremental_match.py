"""增量传播 dry-run 匹配器：把 recheck 的 46 exclude + 23 involvement 改动匹配到现有 bri_re_database.db 的
canonical/entity（经 source_link→event→canonical + country_iso/category/名称相似度消歧），只出报告不写库。UTF-8 输出。

用法：python _recheck_incremental_match.py            # dry-run，写报告
说明：apply 由单独脚本按本报告确认后执行。"""
import json
import pathlib
import sqlite3
from collections import defaultdict

from rapidfuzz import fuzz

root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
PDB = root / "s01b_project_extractor" / "data" / "projects.db"
DDB = root / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db"
BAK = root / "s01b_project_extractor" / "data" / "projects.db.bak_recheck_20260617_080052"  # apply 前（含旧 involvement）
out = []


# ── 载入 extracted_projects（当前 + 备份 involvement）──────────────────────────
pc = sqlite3.connect(str(PDB)); pc.row_factory = sqlite3.Row
ep = {r["id"]: r for r in pc.execute(
    "SELECT id, record_type, project_name, country_iso, category, involvement_type, "
    "implementing_entity, source_link, excluded, exclude_source, exclude_reason FROM extracted_projects")}
pc.close()

bc = sqlite3.connect(str(BAK))
bak_inv = {r[0]: r[1] for r in bc.execute("SELECT id, involvement_type FROM extracted_projects")}
bc.close()

excluded_46 = [r for r in ep.values() if (r["excluded"] or 0) == 1 and r["exclude_source"] == "recheck"]
involved_23 = [r for r in ep.values()
               if (r["involvement_type"] or "") != (bak_inv.get(r["id"]) or "")]
out.append(f"recheck-excluded 记录: {len(excluded_46)} | involvement 改动记录: {len(involved_23)}")

# ── 索引 bri_re_database.db ───────────────────────────────────────────────────
dc = sqlite3.connect(str(DDB)); dc.row_factory = sqlite3.Row
canon = {r["id"]: r for r in dc.execute(
    "SELECT id, project_name, country_iso, category, excluded, excluded_reason FROM canonical_projects")}
# url → set of (event_id, canonical_id)
url_index = defaultdict(set)
ev_canon = {}
for e in dc.execute("SELECT id, canonical_id, source_links FROM project_events"):
    ev_canon[e["id"]] = e["canonical_id"]
    try:
        for u in (json.loads(e["source_links"]) if e["source_links"] else []):
            url_index[u].add((e["id"], e["canonical_id"]))
    except json.JSONDecodeError:
        pass
# event_id → entities
ent_by_ev = defaultdict(list)
for r in dc.execute("SELECT id, event_id, implementing_entity, involvement_type FROM chinese_entities"):
    ent_by_ev[r["event_id"]].append(r)
dc.close()


def match_canonical(rec):
    """按 source_link→候选 canonical，再用 country_iso/category 过滤 + 名称相似度排序。返回 (status, best_cid, detail)。"""
    cands = {cid for (_eid, cid) in url_index.get(rec["source_link"], set())}
    if not cands:
        return "unmatched_no_link", None, "source_link 不在任何 event"
    # 过滤 country_iso + category
    filt = [cid for cid in cands if canon.get(cid)
            and (canon[cid]["country_iso"] or "") == (rec["country_iso"] or "")
            and (canon[cid]["category"] or "") == (rec["category"] or "")]
    pool = filt or list(cands)
    scored = sorted(
        ((fuzz.token_set_ratio(rec["project_name"] or "", canon[cid]["project_name"] or ""), cid) for cid in pool),
        reverse=True)
    best_score, best_cid = scored[0]
    status = "clean"
    if not filt:
        status = "weak_no_country_cat"  # 仅靠 source_link，country/category 没对上
    elif len([s for s, _ in scored if s >= best_score - 5]) > 1 and len(pool) > 1:
        status = "ambiguous_multi"      # 多个候选名称分接近
    return status, best_cid, f"score={best_score} cands={len(cands)} filt={len(filt)}"


# ── 46 exclude 匹配 ───────────────────────────────────────────────────────────
out.append("\n## 46 EXCLUDE 匹配")
stat = defaultdict(int)
exc_rows = []
for rec in sorted(excluded_46, key=lambda r: r["id"]):
    st, cid, detail = match_canonical(rec)
    already = (canon[cid]["excluded"] if cid and canon.get(cid) else None)
    stat[st] += 1
    if cid and (already or 0) == 1:
        stat["_already_excluded"] += 1
    exc_rows.append((rec["id"], rec["record_type"], st, cid, already, detail,
                     (rec["project_name"] or "")[:24], (canon[cid]["project_name"][:24] if cid and canon.get(cid) else "")))
out.append(f"状态统计: {dict(stat)}")
out.append("（_already_excluded=匹配到的 canonical 现已 excluded=1，本次幂等无净变化）")
out.append("\nid | record_type | status | canonical | already_excl | detail | ep_name → canon_name")
for row in exc_rows:
    out.append(f"{row[0]} | {row[1]} | {row[2]} | {row[3]} | {row[4]} | {row[5]} | {row[6]} → {row[7]}")

# ── 23 involvement 匹配 ───────────────────────────────────────────────────────
out.append("\n## 23 INVOLVEMENT 匹配")
inv_rows = []
istat = defaultdict(int)
for rec in sorted(involved_23, key=lambda r: r["id"]):
    new_inv = rec["involvement_type"]; old_inv = bak_inv.get(rec["id"])
    cands = {eid for (eid, _cid) in url_index.get(rec["source_link"], set())}
    # 在候选 event 里找 implementing_entity 相似的 entity
    best = None
    for eid in cands:
        for ent in ent_by_ev.get(eid, []):
            sc = fuzz.token_set_ratio(rec["implementing_entity"] or "", ent["implementing_entity"] or "")
            if best is None or sc > best[0]:
                best = (sc, eid, ent["id"], ent["involvement_type"], ent["implementing_entity"])
    if best is None:
        istat["unmatched"] += 1
        inv_rows.append((rec["id"], old_inv, new_inv, "UNMATCHED", "", "", ""))
    else:
        sc, eid, ent_id, cur_inv, cur_name = best
        tag = "clean" if sc >= 80 else ("weak" if sc >= 50 else "very_weak")
        istat[tag] += 1
        inv_rows.append((rec["id"], old_inv, new_inv, f"{tag}(score={sc})", ent_id,
                         (rec["implementing_entity"] or "")[:18], f"{(cur_name or '')[:18]}|cur_inv={cur_inv}"))
out.append(f"状态统计: {dict(istat)}")
out.append("\nep_id | old_inv → new_inv | match | entity_id | ep_entity → canon_entity|cur_inv")
for row in inv_rows:
    out.append(f"{row[0]} | {row[1]!r}→{row[2]!r} | {row[3]} | {row[4]} | {row[5]} → {row[6]}")

(pathlib.Path(__file__).parent / "_recheck_incremental_match.txt").write_text("\n".join(out), encoding="utf-8")
print("done")
