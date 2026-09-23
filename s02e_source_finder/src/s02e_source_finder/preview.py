"""Project a private one-event report into the development-only acceptance page."""

import argparse
import json
from pathlib import Path

from .sample import MODULE, ROOT


def build_preview(report_path: Path) -> Path:
    """Export only reference metadata and reasons, using the existing public row for layout."""
    source = report_path.resolve(strict=True)
    if not source.is_relative_to((MODULE / "data/samples").resolve()):
        raise ValueError("report_must_be_under_sample_directory")
    report = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("prototype") is not True or report.get("source_unchanged") is not True:
        raise ValueError("not_a_valid_read_only_sample")
    public = ROOT / "s04_web_visualization/public/data/projects_flat.json"
    projects = json.loads(public.read_text(encoding="utf-8"))
    project = next(row for row in projects if row["project_id"] == report["project_id"])
    project["external_references"] = report["external_references"]
    project.pop("official_website_links", None)
    project.pop("non_official_website_links", None)
    data = {"project": project, "run_id": report["run_id"], "results": [{key: row.get(key, "") for key in ("title", "decision", "reason")} for row in report["results"]]}
    output = ROOT / "s04_web_visualization/src/dev/reference-sample.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> None:
    """Generate a local preview without making requests or changing production data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    print(build_preview(parser.parse_args().report))


if __name__ == "__main__":
    main()
