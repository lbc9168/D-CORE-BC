"""s01b 提取评测比较器：按字段类型比较预测 vs 金标准。"""
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
NUMERIC_FIELDS = ("capacity_mw", "total_investment_usd", "storage_power_mw", "storage_energy_mwh")

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
