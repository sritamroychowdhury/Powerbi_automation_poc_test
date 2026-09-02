#!/usr/bin/env python
"""Capture a fresh baseline screenshot for a Power BI report.

Run this once against a known-good report state to seed/update the golden
baseline used by the visual-QA scenario. Overwriting an existing baseline
requires --confirm.

Usage:
    python scripts/refresh_baselines.py --report revenue_dashboard --confirm
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from pages.powerbi_report_page import PowerBIReportPage
from utils.reports import load_report

BASELINE_DIR = Path(__file__).resolve().parent.parent / "baselines"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="Report key in mapping/reports.yaml")
    parser.add_argument("--confirm", action="store_true", help="Required to overwrite an existing baseline")
    args = parser.parse_args()

    report_cfg = load_report(args.report)
    baseline_path = BASELINE_DIR / f"{args.report}.png"

    if baseline_path.exists() and not args.confirm:
        print(f"Baseline already exists at {baseline_path}. Re-run with --confirm to overwrite.")
        sys.exit(1)

    storage_state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
    storage_state_path = storage_state if storage_state and Path(storage_state).exists() else None

    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state_path, viewport={"width": 1600, "height": 900})
        report_page = PowerBIReportPage(context.new_page())
        report_page.open(report_cfg["url"], wait_selector=report_cfg.get("wait_selector"))
        report_page.screenshot(str(baseline_path))
        report_page.close()
        context.close()
        browser.close()

    print(f"Baseline saved to {baseline_path}")


if __name__ == "__main__":
    main()
