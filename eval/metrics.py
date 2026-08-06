"""Compare normalized extraction predictions with legacy and versioned rich-gold projects."""
import re
import sys
import pathlib
import unicodedata

_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums
import bri_parse

NUMERIC_TOL = 0.05
NAME_THRESHOLD = 80
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s\-_,，。.;；:：()（）\[\]【】]+")
_COMPANY_SUFFIX = re.compile(r"(?:有限责任公司|股份有限公司|有限公司|集团公司|集团)$")
_UNIT_ALIASES = [("兆瓦时", "mwh"), ("吉瓦时", "gwh"), ("千瓦时", "kwh"),
                 ("兆瓦", "mw"), ("吉瓦", "gw"), ("千瓦", "kw")]

# These groups mirror the Studio FIELD_REGISTRY metric types; only project_summary has no metric.
PROJECT_ENUM_FIELDS = ("category", "project_stage", "financing_model", "storage_status", "country")
PROJECT_NUMERIC_FIELDS = (
    "capacity_mw", "storage_power_mw", "storage_energy_mwh", "total_investment_usd",
    "contract_amount_usd", "financing_amount_usd",
)
PROJECT_DATE_FIELDS = ("planned_start_date", "planned_complete_date", "investment_date")
PROJECT_NAME_FIELDS = ("project_name", "project_developer")
PROJECT_TEXT_FIELDS = ("location",)
PROJECT_SET_FIELDS = ("generation_sources",)
LEGACY_FIELDS = ("category", "project_stage", "financing_model", "capacity_mw", "total_investment_usd")
ALL_FIELDS = PROJECT_ENUM_FIELDS + PROJECT_NUMERIC_FIELDS + PROJECT_DATE_FIELDS + PROJECT_NAME_FIELDS + PROJECT_TEXT_FIELDS + PROJECT_SET_FIELDS
FIELD_PRIMARY_METRIC = {
    **{field: "accuracy" for field in PROJECT_ENUM_FIELDS + PROJECT_NUMERIC_FIELDS + PROJECT_DATE_FIELDS + PROJECT_NAME_FIELDS + PROJECT_TEXT_FIELDS},
    "generation_sources": "f1",
}


def cmp_status(pred, gold) -> bool:
    """Compare the article-level status exactly after treating missing values as empty."""
    return (pred or "") == (gold or "")


def _norm_enum(kind: str, value):
    """Normalize a controlled enum only enough for exact canonical comparison."""
    if value is None:
        return None
    normalized = str(value).strip()
    valid = {
        "category": bri_enums.valid_categories,
        "project_stage": bri_enums.valid_stages,
        "financing_model": bri_enums.valid_financing,
        "involvement_type": bri_enums.valid_involvement,
    }
    return normalized if kind not in valid or normalized in valid[kind]() else normalized


def cmp_enum(kind: str, pred, gold) -> bool:
    """Compare enums exactly, with null gold accepting only an explicit empty representation."""
    if gold is None:
        return pred is None or (isinstance(pred, str) and (not pred.strip() or (kind == "storage_status" and pred.strip() == "not_mentioned")))
    if pred is None or (isinstance(pred, str) and not pred.strip()):
        return False
    return _norm_enum(kind, pred) == _norm_enum(kind, gold)


def cmp_numeric(pred, gold) -> bool:
    """Compare numeric values with the reviewed ±5% tolerance and explicit empty semantics."""
    left, right = bri_parse.parse_number(pred), bri_parse.parse_number(gold)
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if left == right == 0:
        return True
    return abs(left - right) / max(abs(left), abs(right)) <= NUMERIC_TOL


def cmp_date(pred, gold) -> bool:
    """Compare strict ISO date values exactly while treating omitted values as empty."""
    return str(pred or "").strip() == str(gold or "").strip()


def _norm_name(value) -> str:
    """Normalize project or entity names for conservative fuzzy identity matching."""
    normalized = str(value or "").strip().lower()
    for zh, en in _UNIT_ALIASES:
        normalized = normalized.replace(zh, en)
    return _WS.sub("", normalized)


def _norm_company_name(value) -> str:
    """Normalize common company suffixes for developer-name comparison."""
    return _COMPANY_SUFFIX.sub("", _PUNCT.sub("", _norm_name(value)))


def _name_sim(left, right) -> float:
    """Return normalized project-name token-set similarity, or zero when either name is empty."""
    from rapidfuzz import fuzz
    left_normalized, right_normalized = _norm_name(left), _norm_name(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return fuzz.token_set_ratio(left_normalized, right_normalized)


def cmp_name(field: str, pred, gold) -> bool:
    """Dispatch project names to similarity and developer names to normalized exact comparison."""
    if not _norm_name(gold):
        return not _norm_name(pred)
    if not _norm_name(pred):
        return False
    if field == "project_name":
        return _name_sim(pred, gold) >= NAME_THRESHOLD
    return _norm_company_name(pred) == _norm_company_name(gold)


def cmp_text(pred, gold) -> bool:
    """Compare text with Studio's NFKC, casefold, alias, punctuation, and whitespace canonicalization."""
    def normalize(value) -> str:
        """Mirror Studio evidence._normalize_text without importing the optional Studio package."""
        normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
        normalized = normalized.replace("company", "co").replace("limited", "ltd")
        return "".join(character for character in normalized if character.isalnum())
    return normalize(pred) == normalize(gold)


def _attr_bonus(prediction: dict, gold: dict) -> int:
    """Break project alignment ties using matching category and capacity evidence."""
    bonus = int(bool(prediction.get("category")) and prediction.get("category") == gold.get("category"))
    return bonus + int(cmp_numeric(prediction.get("capacity_mw"), gold.get("capacity_mw")))


def align_projects(pred_projects, gold_projects, threshold: int = NAME_THRESHOLD):
    """Greedily align project names and return matched index pairs plus unmatched indexes."""
    candidates = []
    for pred_index, prediction in enumerate(pred_projects):
        for gold_index, gold in enumerate(gold_projects):
            similarity = _name_sim(prediction.get("project_name"), gold.get("project_name"))
            if similarity >= threshold:
                candidates.append((similarity, _attr_bonus(prediction, gold), pred_index, gold_index))
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]), reverse=True)
    used_pred, used_gold, matched = set(), set(), []
    for _similarity, _bonus, pred_index, gold_index in candidates:
        if pred_index not in used_pred and gold_index not in used_gold:
            used_pred.add(pred_index)
            used_gold.add(gold_index)
            matched.append((pred_index, gold_index))
    unmatched_pred = [index for index in range(len(pred_projects)) if index not in used_pred]
    unmatched_gold = [index for index in range(len(gold_projects)) if index not in used_gold]
    return matched, unmatched_pred, unmatched_gold


def score_entities(pred_entities, gold_entities):
    """Score normalized entity-name sets and roles within one aligned project."""
    predicted = {_norm_name(item.get("name")): item.get("involvement_type") for item in pred_entities if item.get("name")}
    expected = {_norm_name(item.get("name")): item.get("involvement_type") for item in gold_entities if item.get("name")}
    predicted_names, expected_names = set(predicted), set(expected)
    if not predicted_names and not expected_names:
        return 1.0, 1.0, 1.0, 0, 0
    matched = predicted_names & expected_names
    precision = len(matched) / len(predicted_names) if predicted_names else 0.0
    recall = len(matched) / len(expected_names) if expected_names else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    role_correct = sum(cmp_enum("involvement_type", predicted[name], expected[name]) for name in matched)
    return precision, recall, f1, role_correct, len(matched)


def _eligible(gold: dict, field: str) -> bool:
    """Return a rich-gold field's explicit eligibility, defaulting legacy records to evaluable."""
    return gold.get("field_eligibility", {}).get(field, True)


def _set_counts(pred, gold) -> tuple[int, int, int]:
    """Return micro set true-positive, prediction-total, and gold-total counts."""
    predicted, expected = set(pred or []), set(gold or [])
    return len(predicted & expected), len(predicted), len(expected)


def score_article(pred: dict, gold: dict) -> dict:
    """Score one article through status, alignment, eligible scalar fields, entities, and taxonomy facts."""
    taxonomy = {
        "generation_sources": (0, 0, 0), "storage_status": (0, 0),
        "storage_power_mw": (0, 0), "storage_energy_mwh": (0, 0), "proposed_category": (0, 0),
    }
    out = {
        "status_correct": cmp_status(pred.get("status"), gold.get("status")), "scored": False,
        "project_set": None, "field_hits": {}, "set_hits": {"generation_sources": (0, 0, 0)},
        "entity": None, "taxonomy": taxonomy,
    }
    if not out["status_correct"] or gold.get("status") != "valid":
        return out
    out["scored"] = True
    pred_projects, gold_projects = pred.get("projects") or [], gold.get("projects") or []
    matched, _unmatched_pred, _unmatched_gold = align_projects(pred_projects, gold_projects)
    out["project_set"] = len(matched), len(pred_projects), len(gold_projects)
    field_correct = {field: 0 for field in ALL_FIELDS}
    field_total = {field: 0 for field in ALL_FIELDS}
    source_tp = source_pred = source_gold = 0
    taxonomy_hits = {key: [0, 0] for key in taxonomy if key != "generation_sources"}
    name_f1_sum = 0.0
    role_correct = role_matched = 0
    for pred_index, gold_index in matched:
        prediction, expected = pred_projects[pred_index], gold_projects[gold_index]
        for field in PROJECT_ENUM_FIELDS:
            if _eligible(expected, field):
                field_total[field] += 1
                field_correct[field] += cmp_enum(field, prediction.get(field), expected.get(field))
        for field in PROJECT_NUMERIC_FIELDS:
            if _eligible(expected, field):
                field_total[field] += 1
                field_correct[field] += cmp_numeric(prediction.get(field), expected.get(field))
        for field in PROJECT_DATE_FIELDS:
            if _eligible(expected, field):
                field_total[field] += 1
                field_correct[field] += cmp_date(prediction.get(field), expected.get(field))
        for field in PROJECT_NAME_FIELDS:
            if _eligible(expected, field):
                field_total[field] += 1
                field_correct[field] += cmp_name(field, prediction.get(field), expected.get(field))
        for field in PROJECT_TEXT_FIELDS:
            if _eligible(expected, field):
                field_total[field] += 1
                field_correct[field] += cmp_text(prediction.get(field), expected.get(field))
        _precision, _recall, name_f1, role_hits, role_total = score_entities(
            prediction.get("entities") or [], expected.get("entities") or []
        )
        name_f1_sum += name_f1
        role_correct += role_hits
        role_matched += role_total
        if _eligible(expected, "generation_sources"):
            true_positive, predicted_total, expected_total = _set_counts(
                prediction.get("generation_sources"), expected.get("generation_sources")
            )
            field_total["generation_sources"] += 1
            field_correct["generation_sources"] += set(prediction.get("generation_sources") or []) == set(expected.get("generation_sources") or [])
            if expected.get("generation_sources") is not None:
                source_tp += true_positive
                source_pred += predicted_total
                source_gold += expected_total
                taxonomy_hits["proposed_category"][1] += 1
                taxonomy_hits["proposed_category"][0] += prediction.get("proposed_category") == expected.get("category")
        if _eligible(expected, "storage_status") and expected.get("storage_status") is not None:
            taxonomy_hits["storage_status"][1] += 1
            taxonomy_hits["storage_status"][0] += prediction.get("storage_status") == expected.get("storage_status")
        for field in ("storage_power_mw", "storage_energy_mwh"):
            if _eligible(expected, field) and expected.get(field) is not None:
                taxonomy_hits[field][1] += 1
                taxonomy_hits[field][0] += cmp_numeric(prediction.get(field), expected.get(field))
    out["field_hits"] = {field: (field_correct[field], field_total[field]) for field in ALL_FIELDS}
    out["set_hits"] = {"generation_sources": (source_tp, source_pred, source_gold, field_correct["generation_sources"], field_total["generation_sources"])}
    out["entity"] = name_f1_sum, len(matched), role_correct, role_matched
    out["taxonomy"] = {
        "generation_sources": (source_tp, source_pred, source_gold),
        **{key: tuple(value) for key, value in taxonomy_hits.items()},
    }
    return out
