"""developer_seed_export：频次统计、拆部件、排除已可解、CSV 产出。"""
import csv
import sqlite3

from s02b_entity_normalizer.tools.developer_seed_export import export_seed


def _mk_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, excluded INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, project_developer TEXT)")
    conn.execute("INSERT INTO canonical_projects VALUES ('X-PV-0001','甲项目',0)")
    rows = [("X-PV-0001-%02d" % i, "X-PV-0001", "ACWA Power; 未知小公司") for i in range(1, 7)]
    rows += [("X-PV-0001-%02d" % i, "X-PV-0001", "Masdar") for i in range(7, 13)]  # 已在册→排除
    rows += [("X-PV-0001-13", "X-PV-0001", "只出现一次公司")]                      # 低频→排除
    conn.executemany("INSERT INTO project_events VALUES (?,?,?)", rows)
    conn.commit(); conn.close()
    return str(db)


def test_seed_export_filters_and_counts(tmp_path):
    out = export_seed(_mk_db(tmp_path), min_freq=5, out_csv=str(tmp_path / "seed.csv"))
    parts = {r["part"]: r for r in out}
    assert "ACWA Power" in parts and parts["ACWA Power"]["freq"] == 6
    assert "未知小公司" in parts
    assert "Masdar" not in parts          # registry 已可解，不进种子
    assert "只出现一次公司" not in parts   # 低频
    assert parts["ACWA Power"]["is_ascii"] is True


def test_seed_export_sample_projects_and_csv(tmp_path):
    out_csv = str(tmp_path / "seed.csv")
    out = export_seed(_mk_db(tmp_path), min_freq=5, out_csv=out_csv)
    parts = {r["part"]: r for r in out}
    row = parts["ACWA Power"]
    assert row["sample_projects"], "sample_projects 不应为空"
    assert "X-PV-0001" in row["sample_projects"][0][0]
    assert row["sample_projects"][0][1] == "甲项目"
    assert len(row["sample_projects"]) <= 3
    assert "current_en_cache" in row
    assert row["suggested_action"] == ""

    with open(out_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        assert fieldnames == [
            "part", "freq", "sample_projects", "current_en_cache",
            "is_ascii", "suggested_action",
        ]
        csv_rows = list(reader)
    assert len(csv_rows) == len(out)


def test_seed_export_min_freq_boundary(tmp_path):
    db = tmp_path / "t2.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE canonical_projects (id TEXT PRIMARY KEY, project_name TEXT, excluded INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE project_events (id TEXT PRIMARY KEY, canonical_id TEXT, project_developer TEXT)")
    conn.execute("INSERT INTO canonical_projects VALUES ('X-PV-0002','乙项目',0)")
    rows = [("X-PV-0002-%02d" % i, "X-PV-0002", "刚好达标公司") for i in range(1, 6)]  # 恰好5次
    conn.executemany("INSERT INTO project_events VALUES (?,?,?)", rows)
    conn.commit(); conn.close()

    out = export_seed(str(db), min_freq=5, out_csv=str(tmp_path / "seed2.csv"))
    parts = {r["part"]: r for r in out}
    assert "刚好达标公司" in parts  # >= min_freq 应含边界值
    assert parts["刚好达标公司"]["freq"] == 5
