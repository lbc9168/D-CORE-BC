"""verify_s03_zero_diff.py — 任务 3.4 R3：验证 s03 导出层改动对既有产物零字节变化。

用法：
    python verify_s03_zero_diff.py <baseline_a_dir> <baseline_b_dir>

baseline_a = 改代码前（R0）跑 build_flat + build_stats + export_json + export_events_csv
             的输出目录；baseline_b = 改代码后（R3，同一拷贝库）重跑同一管线的输出目录。

逐文件 SHA256 比对 baseline_a 中的既有产物在 baseline_b 中必须完全一致（metadata.json 除外——
其 last_updated 取自源库聚合值，与本次改动无关，理论上也应相同，但如两次运行 last_updated 计算
依据不同拷贝库快照会导致误报，故仍纳入比对，只在探测到差异时打印具体字段供人工判断）。
tx_flat.json / mf_flat.json 是 baseline_b 中的新增文件，预期存在但不参与"零变化"断言。

已知例外：projects_flat.xlsx 由 openpyxl 写出，其 zip 内 docProps/core.xml 每次写入都会
盖上当前时间戳（dcterms:created/modified），与代码改动无关。本脚本对 .xlsx 单独按 zip 成员
逐个比对，仅当 docProps/core.xml 之外的成员出现差异时才判定为 FAIL。
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

_XLSX_TIMESTAMP_MEMBER = "docProps/core.xml"

# 改动前后均应逐字节不变的既有产物（不含新增的 tx_flat.json / mf_flat.json）
EXPECTED_UNCHANGED = [
    "projects_flat.json",
    "projects_flat.csv",
    "projects_flat.xlsx",
    "stats_country.json",
    "stats_entity.json",
    "stats_timeline.json",
    "events_export.csv",
    "metadata.json",
]

NEW_FILES = ["tx_flat.json", "mf_flat.json"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_xlsx(fa: Path, fb: Path) -> tuple[bool, str]:
    """按 zip 成员逐个比对 .xlsx；仅 docProps/core.xml（时间戳）允许不同。"""
    za, zb = zipfile.ZipFile(fa), zipfile.ZipFile(fb)
    names_a, names_b = set(za.namelist()), set(zb.namelist())
    if names_a != names_b:
        return False, f"zip 成员列表不同: only_a={names_a - names_b} only_b={names_b - names_a}"
    diffs = [n for n in names_a if hashlib.sha256(za.read(n)).hexdigest() != hashlib.sha256(zb.read(n)).hexdigest()]
    unexpected = [n for n in diffs if n != _XLSX_TIMESTAMP_MEMBER]
    if unexpected:
        return False, f"数据成员有差异: {unexpected}"
    if diffs == [_XLSX_TIMESTAMP_MEMBER]:
        return True, f"仅 {_XLSX_TIMESTAMP_MEMBER} 时间戳字段不同（openpyxl 写入时自动盖时间戳，与代码改动无关）"
    return True, "完全一致"


def main(dir_a: str, dir_b: str) -> int:
    a, b = Path(dir_a), Path(dir_b)
    all_ok = True

    print("== 既有产物零变化比对 ==")
    for name in EXPECTED_UNCHANGED:
        fa, fb = a / name, b / name
        if not fa.exists() or not fb.exists():
            print(f"  [FAIL] 缺文件: {name} (a存在={fa.exists()} b存在={fb.exists()})")
            all_ok = False
            continue

        if name.endswith(".xlsx"):
            ok, msg = _check_xlsx(fa, fb)
            print(f"  [{'OK' if ok else 'FAIL'}]   {name}: {msg}")
            all_ok = all_ok and ok
            continue

        ha, hb = sha256(fa), sha256(fb)
        if ha == hb:
            print(f"  [OK]   {name}: {ha[:16]}...")
        else:
            if name == "metadata.json":
                ma = json.loads(fa.read_text(encoding="utf-8"))
                mb = json.loads(fb.read_text(encoding="utf-8"))
                diff_keys = [k for k in ma if ma.get(k) != mb.get(k)]
                print(f"  [DIFF] {name}: 字段差异 {diff_keys} (a={ma} b={mb})")
            else:
                print(f"  [FAIL] {name}: hash 不同 a={ha[:16]}... b={hb[:16]}...")
            all_ok = False

    print("\n== 新增产物存在性检查（不参与零变化断言）==")
    for name in NEW_FILES:
        fb = b / name
        status = "存在" if fb.exists() else "缺失"
        print(f"  {name}: {status} (b目录)")
        if not fb.exists():
            all_ok = False

    print(f"\n结论: {'PASS（既有产物零变化，新文件已生成）' if all_ok else 'FAIL（见上）'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"用法: {sys.argv[0]} <baseline_a_dir> <baseline_b_dir>")
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
