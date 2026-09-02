#!/usr/bin/env python
"""Open a Power BI report in a browser using the saved Playwright session.

Run this to manually check that a report renders, using the same session
file and page-object code the automated test suite uses.

Usage:
    python scripts/open_powerbi_report.py --url "https://app.powerbi.com/groups/<workspace-id>/reports/<report-id>?pageName=<page-id>"
    python scripts/open_powerbi_report.py --report dispute_outcomes_summary
    python scripts/open_powerbi_report.py --report dispute_outcomes_summary --headed --screenshot out.png
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", help="Report key in mapping/reports.yaml")
    parser.add_argument("--url", help="Report URL to open directly, bypassing mapping/reports.yaml")
    parser.add_argument("--headed", action="store_true", help="Show the browser window instead of running headless")
    parser.add_argument("--screenshot", help="If set, save a full-page screenshot to this path")
    parser.add_argument("--keep-open", action="store_true", help="Leave the browser open until you press Enter")
    args = parser.parse_args()

    if not args.report and not args.url:
        parser.error("Provide either --report <key> or --url <report-url>")

    if args.url:
        url, wait_selector = args.url, None
    else:
        report_cfg = load_report(args.report)
        url, wait_selector = report_cfg["url"], report_cfg.get("wait_selector")

    storage_state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
    storage_state_path = storage_state if storage_state and Path(storage_state).exists() else None
    if storage_state_path is None:
        print(f"Warning: no session file found at PLAYWRIGHT_STORAGE_STATE={storage_state!r} — you'll likely hit a login page.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        context = browser.new_context(storage_state=storage_state_path, viewport={"width": 1600, "height": 900})
        report_page = PowerBIReportPage(context.new_page())

        print(f"Opening {url}")
        report_page.open(url, wait_selector=wait_selector)

        if report_page.has_error_banner():
            print("Power BI rendered an error banner on this report.")
        else:
            titles = report_page.visual_titles()
            print(f"Loaded OK. Visual titles found: {titles or '(none)'}")

        if args.screenshot:
            report_page.screenshot(args.screenshot)
            print(f"Screenshot saved to {args.screenshot}")

        if args.keep_open:
            input("Press Enter to close the browser...")

        report_page.close()
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
