#!/usr/bin/env python
"""Draft a candidate reconciliation measure (Snowflake SQL + Power BI DAX) from a Jira ticket.

This is a draft generator, not an oracle: output is staged to mapping/generated/
for human review, never written straight into mapping/measures.yaml. Check the
printed assumptions/open_questions, and confirm the SQL actually executes
against live Snowflake (see the "Discrepancy to resolve" note in
.claude/Dispute Outcomes Summary/skill.md about report-label vs. real view
column names) before promoting it into measures.yaml and wiring it into a test.

Usage:
    python scripts/generate_measure_from_jira.py --ticket ABC-123 [--confirm]
"""

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

from utils import claude_agent, jira_client  # noqa: E402

SKILL_PATH = Path(__file__).resolve().parent.parent / ".claude" / "Dispute Outcomes Summary" / "skill.md"
MEASURES_PATH = Path(__file__).resolve().parent.parent / "mapping" / "measures.yaml"
GENERATED_DIR = Path(__file__).resolve().parent.parent / "mapping" / "generated"

EXAMPLE_MEASURE_KEYS = ["win_rebate_dollars", "total_dispute_count", "win_percentage"]


def _load_example_measures() -> str:
    with open(MEASURES_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    measures = data.get("measures", {})
    examples = {k: measures[k] for k in EXAMPLE_MEASURE_KEYS if k in measures}
    return yaml.safe_dump({"measures": examples}, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket", required=True, help="Jira issue key, e.g. ABC-123")
    parser.add_argument("--confirm", action="store_true", help="Required to overwrite an existing staged draft")
    args = parser.parse_args()

    print(f"Fetching {args.ticket} from Jira...")
    ticket = jira_client.get_ticket(args.ticket)

    schema_context = SKILL_PATH.read_text(encoding="utf-8")
    example_measures = _load_example_measures()

    print("Drafting measure with Claude...")
    result = claude_agent.generate_measure_from_ticket(
        ticket=ticket,
        schema_context=schema_context,
        example_measures=example_measures,
    )

    print(f"\nmeasure_name: {result['measure_name']}")
    print(f"confidence:   {result['confidence']}")
    if result["assumptions"]:
        print("assumptions:")
        for a in result["assumptions"]:
            print(f"  - {a}")
    if result["open_questions"]:
        print("open_questions (resolve before trusting this as ground truth):")
        for q in result["open_questions"]:
            print(f"  - {q}")

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GENERATED_DIR / f"{result['measure_name']}.yaml"
    if out_path.exists() and not args.confirm:
        print(f"\n{out_path} already exists. Re-run with --confirm to overwrite.")
        sys.exit(1)

    staged = {
        "source_ticket": ticket["key"],
        "confidence": result["confidence"],
        "assumptions": result["assumptions"],
        "open_questions": result["open_questions"],
        "measures": {
            result["measure_name"]: {
                "description": result["description"],
                "page": "REPLACE_WITH_PAGE_KEY",
                "tolerance_pct": result["tolerance_pct"],
                "snowflake": {"sql": result["snowflake_sql"]},
                "powerbi": {
                    "dataset_id": "REPLACE_WITH_DATASET_ID",
                    "report_id": "REPLACE_WITH_REPORT_ID",
                    "dax": result["powerbi_dax"],
                },
            }
        },
    }
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(staged, f, sort_keys=False)

    print(f"\nDraft staged to {out_path}")
    print("Review it, confirm the SQL runs against live Snowflake, then merge the measure entry into mapping/measures.yaml by hand.")


if __name__ == "__main__":
    main()
