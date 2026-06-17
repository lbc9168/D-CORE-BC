"""export_queue：review_queue pending → CSV（join projects.db 补项目名/原值），只读不改库。"""
import csv
import sqlite3

from review import export_queue, queue


def _mini_projects(p):
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE extracted_projects (id INTEGER PRIMARY KEY, project_name TEXT, "
              "category TEXT, involvement_type TEXT, capacity_mw REAL)")
    c.execute("INSERT INTO extracted_projects (id, project_name, category, involvement_type, capacity_mw) "
              "VALUES (1,'某光伏项目','光伏','Unknown',50.0)")
    c.commit()
    c.close()


def test_export_pending_writes_csv(tmp_path):
    rdb = tmp_path / "review.db"
    pdb = tmp_path / "projects.db"
    out = tmp_path / "out.csv"
    _mini_projects(pdb)
    queue.init_queue(rdb)
    queue.add_item(rdb, canonical_id="1", source_link="http://a", reason_type="status_challenge",
                   field="status", current_value=None, suggested_value="irrelevant",
                   critic_confidence=0.9, evidence="油气")
    queue.add_item(rdb, canonical_id="1", source_link="http://a", reason_type="low_confidence_field",
                   field="involvement_type", current_value=None, suggested_value=None,
                   critic_confidence=0.5, evidence="存疑")

    n = export_queue.export_pending(rdb, pdb, out)
    assert n == 2
    assert out.exists()
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert len(rows) == 2
    # status_challenge 应排在前（高价值优先）
    assert rows[0]["reason_type"] == "status_challenge"
    # involvement_type 行应从 projects.db join 出原值与项目名
    inv = [r for r in rows if r["field"] == "involvement_type"][0]
    assert inv["current_value"] == "Unknown"
    assert inv["project_name"] == "某光伏项目"
    assert inv["category"] == "光伏"


def test_export_empty_queue(tmp_path):
    rdb = tmp_path / "review.db"
    pdb = tmp_path / "projects.db"
    out = tmp_path / "out.csv"
    _mini_projects(pdb)
    queue.init_queue(rdb)
    n = export_queue.export_pending(rdb, pdb, out)
    assert n == 0
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert rows == []
