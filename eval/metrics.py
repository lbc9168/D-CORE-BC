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

def entity_prf(pred_list, gold_list):
    ps, gs = set(x.strip() for x in pred_list if x), set(x.strip() for x in gold_list if x)
    if not ps and not gs: return 1.0, 1.0, 1.0
    tp = len(ps & gs)
    p = tp / len(ps) if ps else 0.0
    r = tp / len(gs) if gs else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1

ENUM_FIELDS = ("category", "project_stage", "financing_model", "involvement_type")
# 储能功率/容量字段 P0 暂不评测：提取器尚未单独产出，gold/predict/build_draft 均无对应列，
# 计入会因 cmp_numeric(None,None)=True 恒判对而虚高数值分；待储能提取落地后随四处一并补回。
NUMERIC_FIELDS = ("capacity_mw", "total_investment_usd")

def score_record(pred: dict, gold: dict) -> dict:
    """返回 {status_correct, fields:{field:bool}, entity_prf:(p,r,f1)}；status 错则 fields 留空。"""
    out = {"status_correct": cmp_status(pred.get("status"), gold.get("status")), "fields": {}, "entity_prf": None}
    if not out["status_correct"] or gold.get("status") != "valid":
        return out
    for f in ENUM_FIELDS:
        out["fields"][f] = cmp_enum(f, pred.get(f), gold.get(f))
    for f in NUMERIC_FIELDS:
        out["fields"][f] = cmp_numeric(pred.get(f), gold.get(f))
    out["entity_prf"] = entity_prf(pred.get("entities", []), gold.get("entities", []))
    return out

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
