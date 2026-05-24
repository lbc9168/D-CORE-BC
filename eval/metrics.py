"""s01b 提取评测比较器：按字段类型比较预测 vs 金标准。"""
import re
import sys, pathlib
_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path: sys.path.insert(0, str(_root))
import bri_enums, bri_parse

NUMERIC_TOL = 0.05  # ±5%

def cmp_status(pred, gold) -> bool:
    return (pred or "") == (gold or "")

def _norm_enum(kind: str, v):
    if v is None: return None
    v = str(v).strip()
    valid = {"category": bri_enums.valid_categories, "project_stage": bri_enums.valid_stages,
             "financing_model": bri_enums.valid_financing, "involvement_type": bri_enums.valid_involvement}
    return v if v in valid[kind]() else v  # 归一交给上游清洗；此处按规范键比对

def cmp_enum(kind: str, pred, gold) -> bool:
    return _norm_enum(kind, pred) == _norm_enum(kind, gold)

def cmp_numeric(pred, gold) -> bool:
    a, b = bri_parse.parse_number(pred), bri_parse.parse_number(gold)
    if a is None and b is None: return True
    if a is None or b is None: return False
    if a == b == 0: return True
    return abs(a - b) / max(abs(a), abs(b)) <= NUMERIC_TOL

NAME_THRESHOLD = 80  # rapidfuzz 项目名相似度阈值（0-100）
_WS = re.compile(r"\s+")

def _norm_name(s) -> str:
    """项目/实体名归一：去空白、转小写，供相似度与集合比对。"""
    return _WS.sub("", str(s or "").strip().lower())

def _name_sim(a, b) -> float:
    """两个项目名的 rapidfuzz token_set_ratio（归一后；任一为空返回 0）。"""
    from rapidfuzz import fuzz
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    return fuzz.token_set_ratio(na, nb)

def _attr_bonus(p: dict, g: dict) -> int:
    """对齐平局打破的属性加分：同 category +1、capacity_mw 在 ±5% 内 +1。"""
    bonus = 0
    if p.get("category") and p.get("category") == g.get("category"):
        bonus += 1
    if cmp_numeric(p.get("capacity_mw"), g.get("capacity_mw")):
        bonus += 1
    return bonus

def align_projects(pred_projects, gold_projects, threshold: int = NAME_THRESHOLD):
    """贪心对齐预测项目↔金标准项目（名字相似度≥阈值，平局看属性）；返回 (matched[(pi,gi)], unmatched_pred[idx], unmatched_gold[idx])。"""
    candidates = []
    for pi, p in enumerate(pred_projects):
        for gi, g in enumerate(gold_projects):
            sim = _name_sim(p.get("project_name"), g.get("project_name"))
            if sim >= threshold:
                candidates.append((sim, _attr_bonus(p, g), pi, gi))
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    used_p, used_g, matched = set(), set(), []
    for _sim, _bonus, pi, gi in candidates:
        if pi in used_p or gi in used_g:
            continue
        used_p.add(pi)
        used_g.add(gi)
        matched.append((pi, gi))
    unmatched_pred = [i for i in range(len(pred_projects)) if i not in used_p]
    unmatched_gold = [i for i in range(len(gold_projects)) if i not in used_g]
    return matched, unmatched_pred, unmatched_gold

def score_entities(pred_entities, gold_entities):
    """比较两个项目的中企集合：返回 (name_p, name_r, name_f1, role_correct, role_matched)。
    name 按归一名集合算 P/R/F1；role 仅对两边同名企业比 involvement_type。"""
    pmap = {_norm_name(e.get("name")): e.get("involvement_type") for e in pred_entities if e.get("name")}
    gmap = {_norm_name(e.get("name")): e.get("involvement_type") for e in gold_entities if e.get("name")}
    ps, gs = set(pmap), set(gmap)
    if not ps and not gs:
        return (1.0, 1.0, 1.0, 0, 0)
    inter = ps & gs
    name_p = len(inter) / len(ps) if ps else 0.0
    name_r = len(inter) / len(gs) if gs else 0.0
    name_f1 = 2 * name_p * name_r / (name_p + name_r) if (name_p + name_r) else 0.0
    role_matched = len(inter)
    role_correct = sum(1 for n in inter if cmp_enum("involvement_type", pmap[n], gmap[n]))
    return (name_p, name_r, name_f1, role_correct, role_matched)

PROJECT_ENUM_FIELDS = ("category", "project_stage", "financing_model")
PROJECT_NUMERIC_FIELDS = ("capacity_mw", "total_investment_usd")

def score_article(pred: dict, gold: dict) -> dict:
    """单篇文章打分：status 闸门 → 贪心对齐 → 对上项目逐字段/实体打分。
    返回 {status_correct, scored, project_set:(matched,pred_n,gold_n)|None,
          field_hits:{field:(correct,total)}, entity:(sum_name_f1,n_matched,role_correct,role_matched)|None}。"""
    out = {"status_correct": cmp_status(pred.get("status"), gold.get("status")),
           "scored": False, "project_set": None, "field_hits": {}, "entity": None}
    if not out["status_correct"] or gold.get("status") != "valid":
        return out
    out["scored"] = True
    pred_projects = pred.get("projects") or []
    gold_projects = gold.get("projects") or []
    matched, _unp, _ung = align_projects(pred_projects, gold_projects)
    out["project_set"] = (len(matched), len(pred_projects), len(gold_projects))
    all_fields = PROJECT_ENUM_FIELDS + PROJECT_NUMERIC_FIELDS
    field_correct = {f: 0 for f in all_fields}
    field_total = {f: 0 for f in all_fields}
    sum_name_f1 = 0.0
    role_correct = role_matched = 0
    for pi, gi in matched:
        p, g = pred_projects[pi], gold_projects[gi]
        for f in PROJECT_ENUM_FIELDS:
            field_total[f] += 1
            if cmp_enum(f, p.get(f), g.get(f)):
                field_correct[f] += 1
        for f in PROJECT_NUMERIC_FIELDS:
            field_total[f] += 1
            if cmp_numeric(p.get(f), g.get(f)):
                field_correct[f] += 1
        _np, _nr, nf1, rc, rm = score_entities(p.get("entities") or [], g.get("entities") or [])
        sum_name_f1 += nf1
        role_correct += rc
        role_matched += rm
    out["field_hits"] = {f: (field_correct[f], field_total[f]) for f in all_fields}
    out["entity"] = (sum_name_f1, len(matched), role_correct, role_matched)
    return out
