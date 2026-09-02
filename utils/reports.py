"""Report inventory lookup: report name -> URL, viewport, ready-state selector."""

from pathlib import Path

import yaml

REPORTS_PATH = Path(__file__).resolve().parent.parent / "mapping" / "reports.yaml"


def load_report(report_name: str) -> dict:
    with open(REPORTS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    reports = data.get("reports", {})
    if report_name not in reports:
        raise KeyError(f"No report inventory entry for '{report_name}' in {REPORTS_PATH}")
    return reports[report_name]
