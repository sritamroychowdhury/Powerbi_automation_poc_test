# Power BI agentic validation — pytest-bdd framework

Gherkin scenarios drive four validation types. The current mapping tables (`mapping/measures.yaml`,
`mapping/reports.yaml`) were originally populated from analyzing a real report — see "What was found"
below — but going forward, new measures and baselines come from Jira tickets
(`scripts/generate_measure_from_jira.py`), not from a local `.pbix`. A schema/contract check and a
static `.pbix` integrity lint used to exist here too but were removed (2026-08-28) for the same reason.

| Scenario type            | What it checks                                 | Tools used                              |
|---------------------------|------------------------------------------------|------------------------------------------|
| KPI reconciliation        | Whole-page scalar metric vs Snowflake source   | Snowflake connector + Power BI DAX      |
| Grouped reconciliation    | Chart/table row-set vs Snowflake, matched by group key | Snowflake + DAX `SUMMARIZECOLUMNS`, deterministic row diff |
| Internal consistency check | Model's derived-column buckets sum to the total (no Snowflake) | Pure DAX, deterministic arithmetic |
| Visual QA                 | Rendered dashboard vs approved baseline        | Playwright + Claude vision              |

Scalar reconciliation alone can't catch a chart/table category being swapped or miscounted if the grand
total still matches — grouped reconciliation and the internal consistency check close that gap. See
`mapping/measures.yaml`'s `grouped_measures` / `consistency_checks` sections for real, live-verified
examples against the "Dispute Status by Dispute Code" chart.

## Structure

```
features/powerbi_validation.feature          Gherkin scenarios (business-readable)
features/steps/test_powerbi_validation_steps.py  Given/When/Then bindings (orchestration only)
pages/powerbi_report_page.py                 Page Object — all Playwright code (locators, waits, screenshotting)
utils/snowflake_client.py                    Snowflake source-of-truth queries
utils/powerbi_client.py                      Power BI REST API: DAX queries, refresh polling, retry/backoff
utils/claude_agent.py                        reconcile / reconcile_grouped / compare_screenshots / root_cause_analysis
utils/row_diff.py                            deterministic row-set alignment by group key, for grouped reconciliation
utils/mapping.py, mapping/measures.yaml      measure -> SQL / DAX / tolerance (measures / grouped_measures / consistency_checks)
utils/reports.py, mapping/reports.yaml       report -> URL / ready-state selector, for screenshot capture
utils/jira_client.py                         Layer 5: file a Jira ticket on failure (disabled by default); also reads tickets for the generator below
scripts/refresh_baselines.py                 seed/update a golden baseline screenshot, via the page object
scripts/generate_measure_from_jira.py        draft a candidate SQL/DAX measure from a Jira ticket, staged to mapping/generated/ for review
conftest.py                                  test_context fixture, Playwright browser context, Jira-on-failure hook
```

`pages/powerbi_report_page.py` is a Page Object: every locator (CSS selector or XPath) and every
Playwright call (`goto`, `wait_for_selector`, `screenshot`, ...) lives on `PowerBIReportPage`. Steps and
`scripts/refresh_baselines.py` only call methods on it (`.open()`, `.has_error_banner()`, `.screenshot()`) —
if Power BI changes a class name or DOM structure, that's a one-file fix.

`test_context` is a plain dict fixture threading state between Given/When/Then steps — no step class,
no hidden globals, and you can print it mid-run to see exactly what's flowed through.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in the values below
```

### Environment variables (see `.env.example`)

- **Snowflake**: `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PASSWORD`, `SNOWFLAKE_WAREHOUSE`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_ROLE`
- **Power BI** (Azure AD service principal): `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`, `PBI_WORKSPACE_ID`
- **Claude**: no API key — `utils/claude_agent.py` shells out to the `claude` CLI (headless `-p` mode),
  which must be installed and logged in on the machine running `pytest` (see "Model choice" below).
  `ANTHROPIC_MODEL` (default `claude-sonnet-5`) and `ANTHROPIC_EFFORT` still apply, passed as
  `--model`/`--effort`.
- **Playwright**: `PLAYWRIGHT_STORAGE_STATE` — path to a saved authenticated-session state file (see below)
- **Jira** (optional for failure-reporting): `JIRA_ENABLED=true` plus `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`.
  `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN` are also required (regardless of `JIRA_ENABLED`) by
  `scripts/generate_measure_from_jira.py`, which reads a ticket rather than filing one.

### Power BI auth for screenshots

Power BI's web UI sits behind interactive Azure AD login, which headless automation can't complete on
its own. The standard workaround: log in once by hand in a real browser, save the authenticated session,
and have Playwright reuse it:

```bash
python -m playwright codegen https://app.powerbi.com --save-storage=playwright_storage_state.json
# log in, close the codegen window once the workspace loads
```

Point `PLAYWRIGHT_STORAGE_STATE` at that file. Sessions expire — regenerate periodically (or wire this into
a scheduled task if your tenant's session lifetime allows it).

### Seed a baseline

```bash
python scripts/refresh_baselines.py --report dispute_outcomes_summary --confirm
```

## Run

```bash
pytest -v
```

## Test reporting with Allure

```bash
pip install allure-pytest-bdd   # NOT allure-pytest -- see note below
pytest -v --alluredir=allure-results
allure generate allure-results -o allure-report --clean
allure open allure-report
```

Install **only** `allure-pytest-bdd`, not `allure-pytest` — both auto-register a `--alluredir` CLI
option and pytest errors with `option names {'--alluredir'} already added` if both are installed and
active simultaneously. Since every test in this suite is a pytest-bdd scenario, `allure-pytest-bdd`
alone is sufficient (and if both end up installed, disable the generic one per-run with
`-p no:allure_pytest`). `allure-pytest-bdd` captures the actual Gherkin structure (feature name,
scenario name, each Given/When/Then as its own step), which the generic plugin doesn't; without it,
Allure just shows a bare parametrized test name with no step breakdown.

`--alluredir` accumulates results across runs — pass `--clean` on `generate` (already above) so stale
runs don't mix into the report. `allure open` starts a local web server; opening `allure-report/index.html`
directly via `file://` won't work since the report fetches its data as JSON over HTTP.

## Model choice

`ANTHROPIC_MODEL` defaults to `claude-sonnet-5` — near-Opus quality on structured reasoning at a third
of the cost, which fits a batch validation pipeline that may run per-measure, per-report, on every refresh.
Root-cause analysis on failures uses `ANTHROPIC_RCA_MODEL` (defaults to the same model) with higher
effort, since that call benefits from more reasoning and only fires on failures, not every run. Bump
either to `claude-opus-4-8` if you need stronger judgment on ambiguous visual diffs or schema drift.

Every Claude call shells out to `claude -p ... --output-format json` (the Claude Code CLI in headless
mode) instead of the `anthropic` SDK, so it authenticates with whatever Claude Code login is active on
the machine — no `ANTHROPIC_API_KEY` to manage. Structured checks (reconciliation, visual comparison)
pass `--json-schema`, the CLI-mode equivalent of the API's `output_config.format`:
the CLI validates the model's output against the schema and returns it in `structured_output`, so
`result["status"]` is a controlled enum and pytest assertions stay deterministic
(`assert result["status"] == "pass"`) instead of parsing prose. `compare_screenshots` grants the CLI's
own `Read` tool so the subprocess reads the two screenshot files itself rather than base64-embedding
them. Tradeoff: each call is a fresh process re-sending the full Claude Code system prompt (no session
to resume), so it's slower/pricier per call than a lean SDK request — a batch `pytest` run mostly hits
Anthropic's server-side prompt cache after the first call, but a single isolated call pays full price.

## What was found analyzing "Dispute Outcomes Summary.pbix"

The mapping tables above aren't hypothetical — they come from unpacking the actual binary `.pbix`
(model via `pbixray`, report definition via the embedded PBIR JSON).

- **Data source**: Snowflake (`HYJORBY-XPA44053.snowflakecomputing.com`, warehouse/database `QA_PLATFORM`,
  schema `PLATFORM_EXPORT_ADAPTER`). The dispute page's fact table `FactMedicaidDispute` imports from view
  `EXPORT_MEDICAID_DISPUTE_DETAIL_REPORT`, filtered to `client = 'ferring'` (the model's `p_customer`
  parameter). Swap that filter before reusing this mapping for a different manufacturer's copy.
- **2 report pages**: "Dispute Outcomes Summary" (visible: dispute detail table, Win/Loss stacked bar by
  dispute code, 4 slicers) and "Page 1" (hidden-in-view-mode working page with Win/Loss/Open % cards).
  The model carries 93 measures but these pages use ~20 — the pbix looks like a trimmed copy of a larger
  Medicaid operations report, with the ProTrend*/RebVar*/StateSummary*/Payment* measure families unused.
- **KPI measures under test**: `TotalRebateAmount` (SUM of Disputed Amount), `Win/Loss/OpenRebateDollars`
  (same, filtered by `Win Loss Status`), `TotalDisputeCount` (COUNTROWS), and `Win/Loss/OpenPercentage`
  (status count over all-status count). All are whole-table aggregates, so the reconciliation SQL is a
  direct filtered aggregate over the Snowflake view — no slicer-context gymnastics needed this time.
- **Real defects found** (details + numbered list in `mapping/measures.yaml`):
  1. The detail table visual carries conditional-formatting rules referencing `'METRIC C&P Bugs
     Resolved'[Bugs Resolved]` and `'METRIC C&P All Tickets'[Target Tickets]` — tables that don't exist
     in this model (leftovers from a copy-pasted visual). Found via one-off `pbixray` static analysis;
     not a standing check anymore since the `.pbix`-based lint was removed (2026-08-28).
  2. `DimDates` has **no relationship to any fact table** — the PeriodYearQuarter slicer on the main page
     only changes dynamic titles (via `SELECTEDVALUE`); the data visuals don't filter by it.
  3. The detail table's "State" column comes from `DimState[StateCode]`, a calculated table that's also
     unrelated to `FactMedicaidDispute` — while the fact table's own `State` column sits unused.
  4. The M source filters with `Date.IsInPreviousNYears([Invoice Date], 5)`, which **excludes the current
     calendar year** — current-year invoices never load into the model. The reconciliation SQL mirrors
     this window so the checks match the report as-built; confirm with the report owner whether it's
     intentional.
  5. `"Invoice Date"` is a computed `VARCHAR`, not a real date (view DDL replaces `util_quarter` codes
     q1-q4 with quarter-end dates) — a stray quarter code outside q1-q4 anywhere in the underlying table
     crashed every measure's query via Snowflake's predicate pushdown, even for rows belonging to other
     clients. Fixed by wrapping the column in `TRY_TO_DATE()`.
- **Published** (confirmed live 2026-08-28): workspace `c5ad4ae8-eb20-4388-8cf2-923a7b82dc04`, report
  `c7c3166e-dd37-4404-9259-d876530a88ce`, dataset `a6dcb475-1034-49b8-a535-b90ae4c63f22` — all filled in
  in `mapping/reports.yaml` / `mapping/measures.yaml`. End-to-end KPI reconciliation has been run for
  real against Snowflake and this dataset agrees exactly.

## What you'll still need to adapt

- **`claude` CLI must be installed and logged in** on whatever machine runs `pytest` — `utils/claude_agent.py`
  shells out to it rather than using an API key; there's no `.env` setting that substitutes for this.
- **Jira wiring**: `conftest.py`'s `pytest_runtest_makereport` hook files a ticket on any failing test when
  `JIRA_ENABLED=true`. Point it at Teams/Slack instead (or in addition) by adding a similar client under `utils/`.
- **CI schedule**: this repo doesn't include a CI config. Wire `pytest` into your scheduler (cron, Airflow,
  GitHub Actions) after each ETL/report refresh completes — `powerbi_client.trigger_dataset_refresh()` +
  `poll_refresh_status()` are there if you want the suite to trigger and wait for the refresh itself.
