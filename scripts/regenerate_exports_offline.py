"""Regenerate s03/s04 artifacts offline while preserving the last reviewed province map."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd


if sys.stdout.encoding and sys.stdout.encoding.lower() in {"gbk", "cp936", "cp950"}:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


WORKSPACE = Path(__file__).resolve().parents[1]
S03_ROOT = WORKSPACE / "s03_data_exporter"
sys.path.insert(0, str(S03_ROOT / "src"))

from s03_data_exporter import events_exporter, flat_builder  # noqa: E402
from s03_data_exporter.flat_builder import build_flat, build_mf_flat, build_tx_flat  # noqa: E402
from s03_data_exporter.json_exporter import export_json  # noqa: E402
from s03_data_exporter.stats_builder import build_stats  # noqa: E402


def _load_province_map(export_db: Path) -> dict[str, str | None]:
    """Read the prior reviewed province mapping before rebuilding its SQLite container."""
    if not export_db.is_file():
        raise FileNotFoundError(f"缺少既有导出数据库，无法离线复用省份映射: {export_db}")
    with sqlite3.connect(export_db) as connection:
        return {
            str(project_id): province
            for project_id, province in connection.execute(
                "SELECT project_id, province FROM projects_flat"
            )
        }


def regenerate(main_db: Path, output_dir: Path, public_dir: Path) -> dict[str, int]:
    """Rebuild flat tables, JSON, CSV, and XLSX without making any model or network call."""
    export_db = output_dir / "bri_re_export.db"
    province_map = _load_province_map(export_db)
    project_count = build_flat(main_db, export_db, province_map)
    tx_count = build_tx_flat(main_db, export_db, province_map)
    mf_count = build_mf_flat(main_db, export_db, province_map)
    build_stats(export_db)

    with sqlite3.connect(export_db) as connection:
        frame = pd.read_sql("SELECT * FROM projects_flat", connection)
    frame.to_excel(output_dir / "projects_flat.xlsx", index=False)
    frame.to_csv(output_dir / "projects_flat.csv", index=False, encoding="utf-8-sig")

    export_json(export_db, output_dir, src_db=main_db)
    public_dir.mkdir(parents=True, exist_ok=True)
    export_json(export_db, public_dir, src_db=main_db)
    event_count = events_exporter.export_events_csv(
        main_db,
        [output_dir / "events_export.csv", public_dir / "events_export.csv"],
        flat_db=export_db,
        iso3_to_en=flat_builder.ISO3_TO_EN_NAME,
        province_map=province_map,
    )
    return {
        "projects_flat": project_count,
        "tx_flat": tx_count,
        "mf_flat": mf_count,
        "events_export": event_count,
    }


def main() -> None:
    """Parse optional paths and print a compact, machine-readable regeneration result."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--main-db",
        type=Path,
        default=WORKSPACE / "s02a_duplicate_merger" / "data" / "db" / "bri_re_database.db",
    )
    parser.add_argument("--output", type=Path, default=S03_ROOT / "data" / "output")
    parser.add_argument(
        "--public", type=Path, default=WORKSPACE / "s04_web_visualization" / "public" / "data"
    )
    args = parser.parse_args()
    print(regenerate(args.main_db.resolve(), args.output.resolve(), args.public.resolve()))


if __name__ == "__main__":
    main()
