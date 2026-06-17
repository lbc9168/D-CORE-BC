"""一次性：给 C2 队列的 low_confidence_field 按"影响"打 severity 标记（与 confidence 正交），输出带标清单 + 分布。

severity 只看"若 critic 说得对，后果多大"，不掺 confidence：
  高 = 改变是否收录（category 改成非 RE / exclude）
  中 = category 任何类别挑战、involvement 真·角色换角色、capacity 有建议且大幅变、project_name 非张冠李戴真改
  低 = 信息缺失/降级/噪声（capacity 无建议或小变、involvement →Unknown 或无建议、project_name 疑似张冠李戴、其余）
confidence 原样留一列，供最后一遍 AI 复核作上下文（不并进 severity）。

只读 CSV，不改任何库。输出 tagged CSV + UTF-8 分布报告。
"""
import csv
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

_root = next(p for p in Path(__file__).resolve().parents if (p / "bri_enums.py").exists())
sys.path.insert(0, str(_root))
import bri_enums as enums
import bri_parse

CSV_IN = _root / "review" / "data" / "review_queue_c2_dryrun.csv"
CSV_OUT = Path(__file__).resolve().parent / "review_queue_c2_tagged.csv"
REPORT = Path(__file__).resolve().parent / "severity_summary.md"

RE_CATS = set(enums.valid_categories()) - {"Unknown"}
CAP_BIG_DELTA = 0.30          # capacity 视为"大幅变"的相对阈值
NAME_CONFUSE_SIM = 0.5        # project_name 相似度低于此视为疑似张冠李戴（留原值更安全）


def _has_sug(r) -> bool:
    return bool((r.get("suggested_value") or "").strip())


def severity_of(r: dict) -> str:
    """确定性算单条 severity（高/中/低），只看字段 + 改动方向，不看 confidence。"""
    field = r["field"]
    cur = (r.get("current_value") or "").strip()
    sug = (r.get("suggested_value") or "").strip()

    if field == "category":
        if _has_sug(r) and sug not in RE_CATS:
            return "高"          # 改成非 RE / exclude → 影响是否收录
        return "中"              # 其余类别挑战（含无建议）→ 可能误分类，值得复核

    if field == "involvement_type":
        if _has_sug(r) and sug and sug != "Unknown" and sug != cur:
            return "中"          # 真·角色换角色
        return "低"              # →Unknown 降级 / 无建议

    if field == "capacity_mw":
        if _has_sug(r):
            c, s = bri_parse.parse_number(cur), bri_parse.parse_number(sug)
            if c and s and c != 0 and abs(s - c) / abs(c) >= CAP_BIG_DELTA:
                return "中"      # 有建议且大幅变 → 真容量错
        return "低"              # 无建议 / 小变

    if field == "project_name":
        if _has_sug(r) and sug and sug != cur:
            if SequenceMatcher(None, cur, sug).ratio() >= NAME_CONFUSE_SIM:
                return "中"      # 小幅订正，非张冠李戴
        return "低"              # 疑似张冠李戴 / 无建议 → 留原值更安全

    return "低"


def main() -> None:
    rows = [r for r in csv.DictReader(open(CSV_IN, encoding="utf-8-sig"))
            if r["reason_type"] == "low_confidence_field"]
    for r in rows:
        r["severity"] = severity_of(r)

    # 写带标 CSV（utf-8-sig 供 Excel）
    fields = list(rows[0].keys())
    with open(CSV_OUT, "w", encoding="utf-8-sig", newline="") as f:
        wri = csv.DictWriter(f, fieldnames=fields)
        wri.writeheader()
        wri.writerows(rows)

    # 分布报告
    L = []
    def w(s=""): L.append(s)
    w(f"# severity 标记分布（low_confidence_field 共 {len(rows)} 条）\n")
    sev_count = Counter(r["severity"] for r in rows)
    w("## 总分布")
    for s in ("高", "中", "低"):
        w(f"- {s}: {sev_count.get(s, 0)}")
    final_scope = sev_count.get("高", 0) + sev_count.get("中", 0)
    w(f"\n**最后一遍复核范围（高+中）= {final_scope} 条；低（维持原值）= {sev_count.get('低', 0)} 条**\n")

    w("## severity × field 交叉")
    by = Counter((r["severity"], r["field"]) for r in rows)
    w("| severity | field | 条数 |")
    w("|---|---|---|")
    for (s, fld), n in sorted(by.items(), key=lambda x: ("高中低".index(x[0][0]), -x[1])):
        w(f"| {s} | {fld} | {n} |")

    w("\n## severity × confidence 分桶（确认两轴正交：高 severity 里也有低 conf）")
    def cbin(c):
        try: c = float(c)
        except (TypeError, ValueError): return "?"
        if c >= 0.9: return "≥0.9"
        if c >= 0.7: return "0.7~0.9"
        return "<0.7"
    cc = Counter((r["severity"], cbin(r["critic_confidence"])) for r in rows)
    w("| severity | conf 桶 | 条数 |")
    w("|---|---|---|")
    for (s, b), n in sorted(cc.items(), key=lambda x: ("高中低".index(x[0][0]), x[0][1])):
        w(f"| {s} | {b} | {n} |")

    REPORT.write_text("\n".join(L), encoding="utf-8")
    print(f"tagged CSV → {CSV_OUT}")
    print(f"summary    → {REPORT}")


if __name__ == "__main__":
    main()
