"""signals：确定性预筛——对单个项目记录算可疑字段，挑出值得花一次批判调用的候选。"""
import sys
import pathlib

_root = next(p for p in pathlib.Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
import bri_enums  # noqa: E402


def _is_unknown(v) -> bool:
    """值是否为空/Unknown。"""
    s = str(v or "").strip()
    return s == "" or s.lower() == "unknown"


def suspicious_fields(proj: dict) -> list[str]:
    """返回该项目的可疑字段名列表（空=不可疑）。proj 用 eval.predict.flat_prediction 的扁平结构。"""
    out: list[str] = []
    cat = proj.get("category")
    ents = proj.get("entities") or []
    real_ents = [e for e in ents if not e.get("is_unnamed_placeholder")
                 and str(e.get("name", "")).strip() != bri_enums.UNNAMED_ENTITY_PLACEHOLDER]
    # 实体：有真实实体但名字 Unknown / 或一个真实实体都没有（且非 unnamed 占位场景）
    if ents and all(_is_unknown(e.get("name")) for e in real_ents) and real_ents:
        out.append("entities")
    elif not ents:
        out.append("entities")
    # 容量：该类目本应有容量却空
    if cat and not bri_enums.is_no_capacity(cat) and _is_unknown(proj.get("capacity_mw")):
        out.append("capacity_mw")
    # 参与方式：Unknown/空
    if _is_unknown(proj.get("involvement_type")):
        out.append("involvement_type")
    return out
