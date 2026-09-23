"""Public exports convert paired values without mutating source records or double scaling."""
import hashlib
import json
import sqlite3

import pytest

from s03_data_exporter.json_exporter import export_json
from s03_data_exporter.production_capacity import public_production_capacity


@pytest.mark.parametrize("value, unit, expected", [
    (3, "万吨/年", (30000, "t/year")),
    (0.15, "万吨/年", (1500, "t/year")),
    (0, "万吨/年", (0, "t/year")),
    (None, "万吨/年", (None, "t/year")),
    (5, "GW/年", (5, "GW/year")),
    (12, "GWh/年", (12, "GWh/year")),
    (None, None, (None, None)),
    (10, "Unknown", (10, "Unknown")),
])
def test_conversion_and_idempotence(value, unit, expected):
    result = public_production_capacity(value, unit)
    assert result == expected
    assert public_production_capacity(*result) == expected


def test_json_export_converts_units_and_preserves_database(tmp_path):
    db = tmp_path / "export.sqlite"
    with sqlite3.connect(db) as conn:
        for table in ("projects_flat", "tx_flat", "stats_country", "stats_entity", "stats_timeline"):
            conn.execute(f"CREATE TABLE {table}(project_id TEXT)")
        conn.execute("CREATE TABLE mf_flat(project_id TEXT, production_capacity_value REAL, production_capacity_unit TEXT)")
        conn.executemany("INSERT INTO mf_flat VALUES(?,?,?)", [
            ("material", 3, "万吨/年"), ("battery", 12, "GWh/年"),
            ("solar", 5, "GW/年"), ("already-public", 30000, "t/year"),
        ])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    out = tmp_path / "public"
    export_json(db, out)
    rows = json.loads((out / "mf_flat.json").read_text(encoding="utf-8"))
    assert [(r["production_capacity_value"], r["production_capacity_unit"]) for r in rows] == [
        (30000, "t/year"), (12, "GWh/year"), (5, "GW/year"), (30000, "t/year"),
    ]
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
