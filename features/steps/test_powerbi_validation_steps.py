import json
from pathlib import Path

import allure
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from pages.powerbi_report_page import PowerBIReportPage
from utils import claude_agent, powerbi_client, snowflake_client
from utils.mapping import load_consistency_check, load_grouped_measure, load_measure
from utils.reports import load_report
from utils.row_diff import diff_grouped_rows

scenarios("../powerbi_validation.feature")

BASELINE_DIR = Path(__file__).resolve().parent.parent.parent / "baselines"
CURRENT_SCREENSHOT_DIR = Path(__file__).resolve().parent.parent.parent / "screenshots" / "current"


# --- KPI reconciliation ------------------------------------------------------


def _print_reconciliation_summary(measure, mapping, snowflake_value, powerbi_value, result):
    """Print the query + both values to the live terminal (pytest.ini's `addopts = -s`
    keeps stdout unbuffered/unsuppressed so this shows on every run, pass or fail --
    not just in the Allure report, which requires opening the HTML report to see it).
    """
    sep = "=" * 78
    print(f"\n{sep}")
    print(f"MEASURE: {measure}")
    print(sep)
    print("Snowflake SQL:")
    for line in mapping["snowflake"]["sql"].strip().splitlines():
        print(f"    {line.strip()}")
    print(f"  -> Snowflake value : {snowflake_value}")
    print()
    print("Power BI DAX:")
    for line in mapping["powerbi"]["dax"].strip().splitlines():
        print(f"    {line.strip()}")
    print(f"  -> Power BI value  : {powerbi_value}")
    print("-" * 78)
    print(f"  Tolerance    : {mapping.get('tolerance_pct', 1.0)}%")
    print(f"  Delta        : {result.get('delta_pct')}%")
    print(f"  Status       : {str(result.get('status', '')).upper()}")
    print(f"  Explanation  : {result.get('explanation')}")
    print(f"{sep}\n")


@given(parsers.parse('the business mapping for measure "{measure}" is loaded'))
def _load_mapping(measure, test_context):
    test_context["measure"] = measure
    test_context["mapping"] = load_measure(measure)


@when("Claude reconciles the Power BI value against Snowflake")
def _reconcile(test_context):
    mapping = test_context["mapping"]
    snowflake_value = snowflake_client.run_scalar_query(mapping["snowflake"]["sql"])
    powerbi_value = powerbi_client.execute_scalar_dax(mapping["powerbi"]["dataset_id"], mapping["powerbi"]["dax"])

    test_context["snowflake_value"] = snowflake_value
    test_context["powerbi_value"] = powerbi_value
    test_context["result"] = claude_agent.reconcile_metric(
        measure_name=test_context["measure"],
        snowflake_value=snowflake_value,
        powerbi_value=powerbi_value,
        tolerance_pct=mapping.get("tolerance_pct", 1.0),
    )
    _print_reconciliation_summary(test_context["measure"], mapping, snowflake_value, powerbi_value, test_context["result"])


@then("the reconciliation result should match")
def _assert_reconciliation(test_context):
    result = test_context["result"]
    mapping = test_context["mapping"]
    allure.attach(
        json.dumps(
            {
                "measure": test_context["measure"],
                "snowflake_value": test_context["snowflake_value"],
                "powerbi_value": test_context["powerbi_value"],
                "tolerance_pct": mapping.get("tolerance_pct", 1.0),
                "delta_pct": result.get("delta_pct"),
                "within_tolerance": result.get("within_tolerance"),
                "status": result.get("status"),
                "likely_cause": result.get("likely_cause"),
                "explanation": result.get("explanation"),
            },
            indent=2,
            default=str,
        ),
        name="Snowflake vs Power BI",
        attachment_type=allure.attachment_type.JSON,
    )
    if result["status"] == "pass":
        return

    rca = claude_agent.root_cause_analysis(
        {
            "measure": test_context["measure"],
            "snowflake_value": test_context["snowflake_value"],
            "powerbi_value": test_context["powerbi_value"],
            "reconciliation_result": result,
        }
    )
    pytest.fail(
        f"Reconciliation failed for {test_context['measure']}: {result['explanation']}\n\n"
        f"Root cause analysis:\n{rca}"
    )


# --- Grouped (chart/table) reconciliation ------------------------------------
# Scalar KPI reconciliation above can't catch two categories being swapped or
# miscounted if the grand total still matches -- these verify the actual
# row-set (group key -> value) backing a chart/table. Row alignment itself is
# deterministic (utils/row_diff.py); Claude only judges the precomputed diff.


def _print_grouped_summary(measure, mapping, diff, result):
    sep = "=" * 78
    print(f"\n{sep}")
    print(f"GROUPED MEASURE: {measure}")
    print(sep)
    print("Snowflake SQL:")
    for line in mapping["snowflake"]["sql"].strip().splitlines():
        print(f"    {line.strip()}")
    print()
    print("Power BI DAX:")
    for line in mapping["powerbi"]["dax"].strip().splitlines():
        print(f"    {line.strip()}")
    print("-" * 78)
    print(f"  {'GROUP':<40} {'SNOWFLAKE':<14} {'POWER BI':<14} DELTA")
    for row in diff["matched"]:
        print(
            f"  {row['group']:<40} {row['snowflake_value']:<14} "
            f"{row['powerbi_value']:<14} {row['delta_pct']:.6f}%"
        )
    if diff["missing_in_powerbi"]:
        print(f"  MISSING IN POWER BI  : {diff['missing_in_powerbi']}")
    if diff["missing_in_snowflake"]:
        print(f"  MISSING IN SNOWFLAKE : {diff['missing_in_snowflake']}")
    print("-" * 78)
    print(f"  Status      : {str(result.get('status', '')).upper()}")
    print(f"  Explanation : {result.get('explanation')}")
    print(f"{sep}\n")


@given(parsers.parse('the grouped business mapping for chart "{chart}" is loaded'))
def _load_grouped_mapping(chart, test_context):
    test_context["chart"] = chart
    test_context["grouped_mapping"] = load_grouped_measure(chart)


@when("Claude reconciles the grouped Power BI values against Snowflake")
def _reconcile_grouped(test_context):
    mapping = test_context["grouped_mapping"]
    snowflake_rows = snowflake_client.run_query(mapping["snowflake"]["sql"])
    powerbi_rows = powerbi_client.execute_dax_query(mapping["powerbi"]["dataset_id"], mapping["powerbi"]["dax"])

    diff = diff_grouped_rows(
        snowflake_rows=snowflake_rows,
        powerbi_rows=powerbi_rows,
        snowflake_group_columns=mapping["group_columns"]["snowflake"],
        powerbi_group_columns=mapping["group_columns"]["powerbi"],
        snowflake_value_column=mapping["value_column"]["snowflake"],
        powerbi_value_column=mapping["value_column"]["powerbi"],
    )
    result = claude_agent.reconcile_grouped_metric(
        measure_name=test_context["chart"],
        diff=diff,
        tolerance_pct=mapping.get("tolerance_pct", 1.0),
    )
    test_context["grouped_diff"] = diff
    test_context["result"] = result
    _print_grouped_summary(test_context["chart"], mapping, diff, result)


@then("the grouped reconciliation result should match")
def _assert_grouped_reconciliation(test_context):
    result = test_context["result"]
    diff = test_context["grouped_diff"]
    allure.attach(
        json.dumps({"diff": diff, "result": result}, indent=2, default=str),
        name="Snowflake vs Power BI (grouped)",
        attachment_type=allure.attachment_type.JSON,
    )
    if result["status"] == "pass":
        return

    rca = claude_agent.root_cause_analysis(
        {
            "chart": test_context["chart"],
            "diff": diff,
            "reconciliation_result": result,
        }
    )
    pytest.fail(
        f"Grouped reconciliation failed for {test_context['chart']}: {result['explanation']}\n\n"
        f"Root cause analysis:\n{rca}"
    )


# --- Internal DAX consistency check -------------------------------------------
# No Snowflake involved -- verifies the model's own derived-column logic is
# internally exhaustive (e.g. every row lands in exactly one bucket). A
# deterministic arithmetic identity, not a Claude judgment call.


@given(parsers.parse('the consistency check "{check_name}" is loaded'))
def _load_consistency_check(check_name, test_context):
    test_context["check_name"] = check_name
    test_context["consistency_check"] = load_consistency_check(check_name)


@when("the component counts are summed and compared against the total")
def _run_consistency_check(test_context):
    check = test_context["consistency_check"]
    rows = powerbi_client.execute_dax_query(check["powerbi"]["dataset_id"], check["powerbi"]["dax"])
    row = rows[0]

    components = {key: float(row[f"[{key}]"]) for key in check["component_keys"]}
    total = float(row[f"[{check['total_key']}]"])
    components_sum = sum(components.values())
    tolerance_pct = check.get("tolerance_pct", 0.01)
    delta_pct = abs(components_sum - total) / total * 100 if total else (0.0 if components_sum == 0 else float("inf"))

    test_context["consistency_components"] = components
    test_context["consistency_total"] = total
    test_context["consistency_sum"] = components_sum
    test_context["consistency_delta_pct"] = delta_pct
    test_context["consistency_within_tolerance"] = delta_pct <= tolerance_pct

    # Informational only -- an existing scalar measure's Snowflake value, printed
    # alongside Power BI's Total purely for visual cross-reference. This check's
    # pass/fail never depends on it (see the YAML comment on reference_snowflake_measure).
    snowflake_reference = None
    reference_measure_name = check.get("reference_snowflake_measure")
    if reference_measure_name:
        reference_measure = load_measure(reference_measure_name)
        snowflake_reference = snowflake_client.run_scalar_query(reference_measure["snowflake"]["sql"])
        test_context["consistency_snowflake_reference"] = snowflake_reference

    sep = "=" * 78
    print(f"\n{sep}")
    print(f"CONSISTENCY CHECK: {test_context['check_name']}")
    print(sep)
    print("Power BI DAX (components + total, single query):")
    for line in check["powerbi"]["dax"].strip().splitlines():
        print(f"    {line.strip()}")
    print("-" * 78)
    for key, value in components.items():
        print(f"  {key:<20}: {value}")
    print(f"  {'Sum (components)':<20}: {components_sum}")
    print(f"  {'Total (Power BI)':<20}: {total}")
    if snowflake_reference is not None:
        print(f"  {'Total (Snowflake)':<20}: {snowflake_reference}   [reference only, via '{reference_measure_name}' -- "
              f"not part of this check's pass/fail, see the KPI reconciliation scenario for that comparison]")
    print(f"  {'Delta':<20}: {delta_pct}%  (tolerance {tolerance_pct}%)")
    print(f"{sep}\n")


@then("the components should sum to the total within tolerance")
def _assert_consistency(test_context):
    allure.attach(
        json.dumps(
            {
                "components": test_context["consistency_components"],
                "sum": test_context["consistency_sum"],
                "total_powerbi": test_context["consistency_total"],
                "total_snowflake_reference_only": test_context.get("consistency_snowflake_reference"),
                "delta_pct": test_context["consistency_delta_pct"],
            },
            indent=2,
            default=str,
        ),
        name="Component sum vs total",
        attachment_type=allure.attachment_type.JSON,
    )
    if test_context["consistency_within_tolerance"]:
        return

    rca = claude_agent.root_cause_analysis(
        {
            "check": test_context["check_name"],
            "components": test_context["consistency_components"],
            "sum": test_context["consistency_sum"],
            "total": test_context["consistency_total"],
            "delta_pct": test_context["consistency_delta_pct"],
        }
    )
    pytest.fail(
        f"Consistency check failed for {test_context['check_name']}: components sum to "
        f"{test_context['consistency_sum']} but total is {test_context['consistency_total']} "
        f"(delta {test_context['consistency_delta_pct']:.6f}%)\n\n"
        f"Root cause analysis:\n{rca}"
    )


# --- Visual QA ---------------------------------------------------------------
# All Playwright interaction (navigation, waits, locators, screenshotting) lives
# in features/pages/powerbi_report_page.py — steps only orchestrate.


@given(parsers.parse('the baseline screenshot for report "{report}" exists'))
def _baseline_exists(report, test_context):
    baseline_path = BASELINE_DIR / f"{report}.png"
    assert baseline_path.exists(), (
        f"No baseline screenshot at {baseline_path}. Run "
        f"`python scripts/refresh_baselines.py --report {report} --confirm` against a "
        "known-good report state first."
    )
    test_context["report"] = report
    test_context["baseline_path"] = str(baseline_path)


@given(parsers.parse('the current dashboard screenshot for report "{report}" is captured'))
def _capture_current(report, test_context, playwright_context):
    report_cfg = load_report(report)
    CURRENT_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    current_path = CURRENT_SCREENSHOT_DIR / f"{report}.png"

    report_page = PowerBIReportPage(playwright_context.new_page())
    try:
        report_page.open(report_cfg["url"], wait_selector=report_cfg.get("wait_selector"))
        assert not report_page.has_error_banner(), f"Power BI rendered an error banner for report '{report}'"
        report_page.screenshot(str(current_path))
    finally:
        report_page.close()

    test_context["current_path"] = str(current_path)


@when("Claude compares the current screenshot against the baseline")
def _compare_screenshots(test_context):
    test_context["result"] = claude_agent.compare_screenshots(
        baseline_path=test_context["baseline_path"],
        current_path=test_context["current_path"],
        measure_name=test_context["report"],
    )


@then("the visual comparison result should pass")
def _assert_visual(test_context):
    result = test_context["result"]
    assert result["status"] == "pass", (
        f"Visual QA failed for {test_context['report']} (severity={result['severity']}): {result['differences']}"
    )
