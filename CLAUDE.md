# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A pytest-bdd framework that validates a Power BI report ("Dispute Outcomes
Summary") four ways, with Claude doing the judgment calls (reconciliation
verdicts, visual comparison, root-cause analysis on failures):

| Scenario type | What it checks | Tools used |
|---|---|---|
| KPI reconciliation | Whole-page scalar metric vs Snowflake source | Snowflake connector + Power BI DAX |
| Grouped reconciliation | Chart/table row-set (group key -> value) vs Snowflake, matched by key | Snowflake + DAX `SUMMARIZECOLUMNS`, deterministic row diff |
| Internal consistency check | Model's own derived-column logic sums correctly (no Snowflake) | Pure DAX, deterministic arithmetic |
| Visual QA | Rendered dashboard vs approved baseline screenshot | Playwright + Claude vision |

Scalar KPI reconciliation only catches a wrong grand total — it can't catch two categories being
swapped or miscounted if the total still matches. Grouped reconciliation and the internal consistency
check exist for that gap: the first verifies a chart/table's actual row-by-row breakdown against
Snowflake (e.g. `mapping/measures.yaml`'s `dispute_status_by_dispute_code`, backing the "Dispute Status
by Dispute Code" stacked bar), the second verifies the model's derived buckets are internally exhaustive
(e.g. `dispute_status_components_sum_to_total`: `WinDisputeCount + LossDisputeCount + OpenDisputeCount`
plus the blank-status row count must equal `TotalDisputeCount` exactly, since `Win Loss Status` is a
SWITCH-derived column with exactly those four outcomes). Both were verified live 2026-08-28.

A schema/contract check and a static `.pbix` integrity lint used to exist here too but were removed
(2026-08-28) — this project no longer relies on a local `.pbix` at all going forward. New measures and
baselines are sourced from Jira tickets (`scripts/generate_measure_from_jira.py`), not from
reverse-engineering a `.pbix`.

The 6 measures already in `mapping/measures.yaml` and the pages in `mapping/reports.yaml` were
originally populated by reverse-engineering the real "Dispute Outcomes Summary" report (a
Snowflake-backed Medicaid rebate dispute model, single-tenant per manufacturer) and have since been
verified end-to-end against live Snowflake + the published Power BI dataset. See
`.claude/Dispute Outcomes Summary/skill.md` for the full data-model writeup (tables, DAX measures,
relationships, and known model quirks) — it's also the schema/crosswalk context fed to the Jira-driven
generator, so it stays relevant even without the `.pbix` lint.

## Commands

```bash
# Setup
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # non-secret config only -- see Credentials below

# Run everything
pytest -v

# Run one scenario-outline example (measure-level)
pytest -v "features/steps/test_powerbi_validation_steps.py::test_kpi_reconciles_against_snowflake[win_rebate_dollars]"

# Run the grouped (chart/table) reconciliation and internal consistency checks
pytest -v features/steps/test_powerbi_validation_steps.py::test_dispute_status_by_dispute_code_chart_reconciles_against_snowflake
pytest -v features/steps/test_powerbi_validation_steps.py::test_win_loss_open_and_unclassified_dispute_counts_add_up_to_the_total

# Run the visual QA scenario
pytest -v features/steps/test_powerbi_validation_steps.py::test_dashboard_visual_matches_the_approved_baseline

# Generate/refresh a Playwright authenticated session (Power BI login is interactive; do this by hand)
python -m playwright codegen https://app.powerbi.com --save-storage=playwright_storage_state.json

# Seed or update the golden baseline screenshot used by the visual-QA scenario
python scripts/refresh_baselines.py --report dispute_outcomes_summary --confirm

# Draft a candidate measure (Snowflake SQL + Power BI DAX) from a Jira ticket's description.
# Requires JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN (see Credentials). Stages output to
# mapping/generated/<measure>.yaml for human review -- never writes mapping/measures.yaml directly.
python scripts/generate_measure_from_jira.py --ticket ABC-123 --confirm
```

`pytest.ini` sets `testpaths = features/steps` and `python_files = test_*.py` — pytest only discovers
tests under `features/steps/`; the Gherkin feature file itself is not directly runnable.

## Credentials

Real secrets (Snowflake key-pair credentials, Power BI service-principal client secret) come from
**AWS Secrets Manager**, not `.env` — `utils/secrets_manager.py` fetches them via `boto3` given
`AWS_REGION` + `SNOWFLAKE_SECRET_NAME` / `PBI_SECRET_NAME` (secret name pointers, which *do* live in
`.env`). **Snowflake auth is RSA key-pair, not password**, despite field naming in the secret that
suggests otherwise: `SNOWFLAKE_KEY` is a base64-encoded, passphrase-encrypted PEM private key, and
`SNOWFLAKE_PASS` is that key's decryption passphrase (confirmed empirically 2026-08-28 — plain
password auth is rejected by Snowflake, but `SNOWFLAKE_PASS` correctly decrypts `SNOWFLAKE_KEY`).
`utils/snowflake_client.py` decodes/decrypts it and passes `private_key=` to the connector instead of
`password=`. Only
non-secret config (`PBI_WORKSPACE_ID`, model names, feature flags) lives directly in
`.env`. AWS credentials/profile for `boto3` must already be configured in the environment (not managed
by this repo). Jira integration (`utils/jira_client.py`) is the one exception still using plain `.env`
values. `create_ticket()` (failure-reporting) only activates when `JIRA_ENABLED=true`; `get_ticket()`
(reads a ticket for `scripts/generate_measure_from_jira.py`) always needs `JIRA_BASE_URL`/`JIRA_EMAIL`/
`JIRA_API_TOKEN` regardless of that flag, since it isn't part of the optional failure-reporting feature.

**No `ANTHROPIC_API_KEY` needed**: `utils/claude_agent.py` shells out to the `claude` CLI in headless
mode (`claude -p ...`) rather than the `anthropic` SDK, authenticating with whatever Claude Code login
is active on the machine running the tests (OAuth/subscription). The `claude` CLI must be installed and
logged in (`claude auth`/interactive login once) wherever `pytest` runs — this is a machine-level
prerequisite, not something `.env` can configure. `ANTHROPIC_MODEL`/`ANTHROPIC_RCA_MODEL`/
`ANTHROPIC_EFFORT`/`ANTHROPIC_RCA_EFFORT` in `.env` still apply (passed as `--model`/`--effort`), but
`ANTHROPIC_API_KEY` and `ANTHROPIC_MAX_TOKENS` do nothing now and can be deleted from `.env`.

## Architecture

**Layering, thin to thick:**

- `features/powerbi_validation.feature` — Gherkin scenarios, business-readable, no logic.
- `features/steps/test_powerbi_validation_steps.py` — Given/When/Then bindings. Pure orchestration:
  load a mapping, call a util, call `claude_agent`, assert on `result["status"]`. No SQL/DAX literals,
  no Playwright calls live here directly.
- `pages/powerbi_report_page.py` — Page Object. Every Playwright locator/call (`goto`, `wait_for_selector`,
  `screenshot`, error-banner XPath) lives on `PowerBIReportPage`; steps and `scripts/refresh_baselines.py`
  only call its methods. A Power BI DOM change is a one-file fix.
- `utils/mapping.py` + `mapping/measures.yaml` — measure name → Snowflake SQL / Power BI DAX / tolerance.
  Adding a new measure to test is a YAML edit, not a code change. Three shapes live in this one file:
  `measures` (scalar KPI reconciliation), `grouped_measures` (chart/table row-set reconciliation --
  SQL/DAX each return multiple rows, matched by a `group_columns` key), and `consistency_checks`
  (pure-DAX internal invariants, no Snowflake side at all).
- `utils/row_diff.py` — `diff_grouped_rows()`: deterministic row-set alignment by group key for grouped
  reconciliation. Row matching is plain data processing, not judgment, so it happens here in code, not
  via Claude -- Claude only judges the precomputed diff (see `claude_agent.reconcile_grouped_metric()`).
- `utils/reports.py` + `mapping/reports.yaml` — report name → URL / ready-state selector, for screenshot
  capture and navigation.
- `utils/snowflake_client.py` / `utils/powerbi_client.py` — source-of-truth data access. `powerbi_client`
  wraps the service-principal client-credentials OAuth flow (cached token), DAX query execution via
  `executeQueries`, and refresh trigger/poll. All outbound HTTP goes through `utils/retry.py`'s
  `with_backoff` decorator (429/5xx + connection-error retry with exponential backoff).
- `utils/claude_agent.py` — every Claude call in the framework, made via the `claude` CLI in headless
  mode (`claude -p ... --output-format json`), not the `anthropic` SDK — see Credentials above for why.
  Structured checks (`reconcile_metric`, `reconcile_grouped_metric`, `compare_screenshots`) pass
  `--json-schema` so `result["status"]` is a controlled enum and pytest assertions stay deterministic —
  never parse prose for these. `reconcile_grouped_metric` only judges a diff already computed by
  `utils/row_diff.diff_grouped_rows()` (tolerance + likely cause), the same division of labor as
  `reconcile_metric` -- Claude judges, it doesn't align rows itself. The internal consistency check
  (component counts summing to a total) skips Claude entirely: it's a deterministic arithmetic identity
  with no interpretive tolerance question, so it's a plain assertion in the step, with
  `root_cause_analysis` only invoked on failure, same as the other checks. `compare_screenshots` grants
  the CLI's own `Read` tool (`--allowedTools Read
  --permission-mode bypassPermissions`) so the subprocess can view the two image files itself, instead
  of base64-embedding them in an API request. `root_cause_analysis` is a plain prose call (no schema),
  fired only on failures. Models/effort levels are still env-overridable (`ANTHROPIC_MODEL`,
  `ANTHROPIC_RCA_MODEL`, `ANTHROPIC_EFFORT`, `ANTHROPIC_RCA_EFFORT`). Cost/latency note: each call is a
  fresh `claude` process re-sending the full Claude Code system prompt (no session to resume), so a
  single isolated call costs meaningfully more than a lean SDK call — a batch pytest run mostly hits
  Anthropic's server-side prompt cache after the first call, but don't assume this is as cheap as the
  old SDK path.
- `conftest.py` — `test_context` is a plain dict fixture threading state between Given/When/Then steps
  (deliberately not a step class with `self.` attributes — trivially printable mid-run). `playwright_context`
  is a session-scoped browser context built from `PLAYWRIGHT_STORAGE_STATE`. `pytest_runtest_makereport`
  files a Jira ticket on any failing test when Jira is enabled.
- `scripts/refresh_baselines.py` — seeds/updates a golden baseline screenshot via the same Page Object;
  requires `--confirm` to overwrite an existing baseline.
- `scripts/generate_measure_from_jira.py` + `utils/claude_agent.generate_measure_from_ticket()` — the
  Jira-driven test generator. Pulls a ticket via `jira_client.get_ticket()`, grounds Claude with
  `.claude/Dispute Outcomes Summary/skill.md` (schema/crosswalk/quirks) plus a few existing
  `measures.yaml` entries as style examples, and drafts a new SQL/DAX pair with `assumptions` /
  `open_questions` / `confidence` attached. Output lands in `mapping/generated/`, not
  `mapping/measures.yaml` — ticket prose is often ambiguous enough that the draft needs a human
  to resolve open questions and confirm the SQL actually runs against live Snowflake before it's
  trusted as a reconciliation oracle.

**Data flow for a KPI reconciliation scenario:** feature Example row → `load_measure()` reads
`mapping/measures.yaml` → `snowflake_client.run_scalar_query()` and `powerbi_client.execute_scalar_dax()`
fetch both sides independently → `claude_agent.reconcile_metric()` judges pass/fail within tolerance and
names a likely discrepancy category if it fails → on failure, `claude_agent.root_cause_analysis()` runs
before the assertion message is built, so failures come with a diagnosis attached, not just a delta.

**Data flow for a grouped reconciliation scenario:** `load_grouped_measure()` reads `grouped_measures` →
`snowflake_client.run_query()` and `powerbi_client.execute_dax_query()` each fetch a multi-row result set
→ `row_diff.diff_grouped_rows()` deterministically aligns them by `group_columns` and computes per-group
deltas → `claude_agent.reconcile_grouped_metric()` judges the precomputed diff against tolerance (missing
groups on either side are an automatic fail). Terminal output prints every matched group's Snowflake vs
Power BI value and delta, not just the overall verdict.

**Data flow for the internal consistency check:** `load_consistency_check()` reads `consistency_checks` →
a single DAX query fetches all named components plus the total in one round trip → the step sums the
components and compares to the total with a small float-safety tolerance, entirely in Python (no Claude
call on the happy path — there's no interpretive tolerance judgment to make on an arithmetic identity);
`root_cause_analysis` only runs if the sum doesn't match.

**Published to the Power BI Service** (confirmed live 2026-08-28, superseding the original "not yet
published" assumption): workspace `c5ad4ae8-eb20-4388-8cf2-923a7b82dc04`, report
`c7c3166e-dd37-4404-9259-d876530a88ce`, dataset `a6dcb475-1034-49b8-a535-b90ae4c63f22`. All three IDs
are filled in in `mapping/reports.yaml` / `mapping/measures.yaml`. End-to-end KPI reconciliation has
been run for real against this dataset and matches Snowflake exactly (see `mapping/measures.yaml`'s
2026-08-28 header note).

**Auth for screenshots**: Power BI's web UI requires interactive Azure AD login that headless automation
can't complete, so Playwright reuses a storage-state file captured once via `playwright codegen` by hand.
Sessions expire and need periodic regeneration.
