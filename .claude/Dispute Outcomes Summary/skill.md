---
name: dispute-outcomes-summary
description: Explains the data model and business logic behind the "Dispute Outcomes Summary" Power BI report (Medicaid rebate dispute win/loss/open tracking for client "ferring"), so Claude can reason about its measures, relationships, and known quirks without re-deriving them from the .pbix/.pbip each time.
---

# Dispute Outcomes Summary — report logic

Source analyzed: `C:\Users\chowdsr\Downloads\Dispute Outcomes Summary.pbip`
(+ its `.Report` and `.SemanticModel` sibling folders, PBIR/TMDL format, report ID
`c7c3166e-dd37-4404-9259-d876530a88ce`). Cross-referenced against this project's
existing `mapping/reports.yaml` and `mapping/measures.yaml`, which were derived
earlier from the raw `.pbix` via `pbixray`.

## What this report is

A single-tenant Medicaid rebate **dispute-resolution scorecard**. Every source
query is filtered to one manufacturer (`p_customer` Snowflake parameter,
currently `"ferring"` = Ferring Pharmaceuticals) — this model is a per-client
template, redeployed by swapping that parameter. Data source: Snowflake,
`QA_PLATFORM.PLATFORM_EXPORT_ADAPTER`, host `HYJORBY-XPA44053.snowflakecomputing.com` (as recorded in
the PBIX's M-query). The automation's own Snowflake secret resolves to a differently-formatted account
identifier (`kca59186.us-east-1`, an account-locator form vs. this host's org-account-name form) —
initially flagged as a possible environment mismatch, but resolved: querying
`platform_export_adapter.export_medicaid_dispute_detail_report` through that secret and reconciling
against live Power BI DAX (both `total_dispute_count` and `total_disputed_amount`) produced exact
matches, confirming it's the same underlying data.

The core question the report answers: *"for a given invoice quarter / product
family / program / dispute code, how many Medicaid rebate disputes were Won,
Lost, or are still Open — and what dollar amounts are involved?"*

## Core business concept: Win Loss Status

`FactMedicaidDispute[Win Loss Status]` is the outcome of a dispute a state
Medicaid agency raised against a manufacturer's invoiced/claimed rebate. Three
values only:

- **Win** — dispute resolved in the manufacturer's favor → `Won Amount` / `Won/Dismissed Units`
- **Loss** — resolved against the manufacturer → `Lost Amount` / `Lost Units`
- **Open** — unresolved → `Outstanding Amount` / `Outstanding Units`

Calculated columns `Validation Result Units` / `Validation Result Dollars` on
`FactMedicaidDispute` implement this Win/Loss/Open → amount-bucket mapping via
`SWITCH`.

**Dispute Codes** (`DimErrorDisputeCode`, joined via `FactMedicaidDispute[Dispute Codes]
→ DimErrorDisputeCode[DISPUTE_CODE]`) are the reason codes for why a state disputed
a claim. Don't confuse with `FactMedicaid[ERROR_CODES]`, a separate broader error-code
list on the (mostly unused-by-this-report) `FactMedicaid` fact table.

## Semantic model — tables

| Table | Role | Notes |
|---|---|---|
| `FactMedicaidDispute` | **Primary fact for this report.** Source view `EXPORT_MEDICAID_DISPUTE_DETAIL_REPORT`, filtered to client + `Date.IsInPreviousNYears([Invoice Date], 5)` | Drives every visual on the visible page. Key columns: State, Program Group, Invoice/Utilization Quarter, Win Loss Status, Disputed Amount, Submitted Amount, Rebate Amount Due, Won/Lost/Outstanding Amount & Units, Dispute Codes |
| `FactMedicaid` | Broader payment/rebate-processing fact, filtered to client + last 5 years by `PROCESS_DATE` | Backs a large family of measures (ProgramTrending, RebateVariance waterfall, DisputeAging, OutstandingDisputes, FedSupplemental, StateSummary, PaymentOverview, ProgramSummary) that **are not used on either page currently in this report** — see "Orphaned measures" below |
| `FactMediPaymentTracker` | Payment tracker detail, filtered to client | Normalizes raw workflow `STATUS` into `PROCESSING_STATUS` / `REBATE_STATUS` buckets |
| `DimProduct` | Active, `FLAG_MEDI=true` products for client | `PRODUCT_BRAND` = "Product Family" |
| `DimProgram` | Distinct programs | `DESIGNATION` = MANDATORY/VOLUNTARY (federal vs. supplemental) |
| `DimErrorDisputeCode` | Distinct dispute/error codes | |
| `DimSubmissionType` | Distinct submission types | |
| `DimDates` | Calculated `CALENDAR`, rolling ~24 months | `PeriodYearQuarter` ("yyyy-Qn"), `IsCompleteQuarter` flag |
| `DimState` | Static `DATATABLE` of US states | |
| `DimRebDetWaterfallSteps` | Static 6-step waterfall labels for the (unused-here) rebate-variance chart | |
| `DimClientCustomer` | Client/manufacturer lookup | |
| `_Measures` | Hidden dummy table holding all 90+ DAX measures | |

No RLS roles defined. All partitions are Import mode (no DirectQuery).

## Relationships (verify before trusting downstream test logic)

Relevant ones for this report:
- `FactMedicaidDispute.'DimProduct.ProductID' → DimProduct.ProductID`
- `FactMedicaidDispute.'DimProgram.Program_ID' → DimProgram.Program_ID`
- `FactMedicaidDispute.'Invoice Date' → DimDates.Date`
- `FactMedicaidDispute.State → DimState.StateCode`
- `FactMedicaidDispute.'Dispute Codes' → DimErrorDisputeCode.DISPUTE_CODE`

⚠️ **Discrepancy to resolve, not silently pick a side on:** `relationships.tmdl`
in the `.pbip` I read shows `FactMedicaidDispute.'Invoice Date' → DimDates.Date`
as an active relationship (no `isActive: false` on it). But this project's
`mapping/measures.yaml` (known_issues #2, written earlier from `pbixray`
analysis of the raw `.pbix`) asserts **DimDates has NO relationship to
FactMedicaidDispute**, and that the `PeriodYearQuarter` slicer therefore only
changes dynamic titles (via `SELECTEDVALUE`) and does *not* filter the detail
table or win/loss chart — which is why `measures.yaml`'s reconciliation SQL
uses the full 5-year window instead of a quarter filter.

Possible explanations: the `.pbix` and `.pbip` are different snapshots in time
(pbip folder timestamps are ~20 min newer), or `pbixray` missed/mis-parsed the
relationship. **Before changing any test/reconciliation logic, open the report
live and confirm empirically** whether changing the "Invoice Quarter" slicer
actually filters the "Disputes Detail Analysis" table and the "Dispute Status
by Dispute Code" chart. If it does filter now, `mapping/measures.yaml`'s SQL
needs an added Invoice Quarter predicate to keep reconciling.

Two other known quirks already documented in `mapping/measures.yaml`:
- The detail table's "State" column comes from `DimState[StateCode]` (a static
  calculated table with no relationship to `FactMedicaidDispute`) rather than
  `FactMedicaidDispute`'s own `State` column.
- The detail table visual has leftover conditional-formatting `FillRule` rules
  referencing nonexistent entities (`'METRIC C&P Bugs Resolved'`, `'METRIC C&P
  All Tickets'`) — copy-paste residue from another report, harmless. Found via
  one-off `pbixray` static analysis (the `.pbix`-based lint that used to catch
  this as a standing check was removed 2026-08-28 — this project no longer
  relies on the local `.pbix`).

## Key DAX measures actually used on the visible page

All from the `_Measures` table, folder "Win Loss Open Dispute Calc", all
against `FactMedicaidDispute`:

```
TotalRebateAmount = SUM(FactMedicaidDispute[Disputed Amount])
WinRebateDollars  = CALCULATE([TotalRebateAmount], FactMedicaidDispute[Win Loss Status]="Win")
LossRebateDollars = CALCULATE([TotalRebateAmount], FactMedicaidDispute[Win Loss Status]="Loss")
OpenRebateDollars = CALCULATE([TotalRebateAmount], FactMedicaidDispute[Win Loss Status]="Open")
TotalDisputeCount = COUNTROWS(FactMedicaidDispute)
WinDisputeCount   = CALCULATE(COUNTROWS(FactMedicaidDispute), FactMedicaidDispute[Win Loss Status]="Win")
WinPercentage     = DIVIDE([WinDisputeCount], CALCULATE([TotalDisputeCount], ALL(FactMedicaidDispute[Win Loss Status])), 0)
LossDisputeCount  = CALCULATE(COUNTROWS(FactMedicaidDispute), FactMedicaidDispute[Win Loss Status]="Loss")
LossPercentage    = DIVIDE([LossDisputeCount], CALCULATE([TotalDisputeCount], ALL(FactMedicaidDispute[Win Loss Status])), 0)
OpenDisputeCount  = CALCULATE(COUNTROWS(FactMedicaidDispute), FactMedicaidDispute[Win Loss Status]="Open")
OpenPercentage    = DIVIDE([OpenDisputeCount], CALCULATE([TotalDisputeCount], ALL(FactMedicaidDispute[Win Loss Status])), 0)
```

Plus dynamic title measures (`DisputeWinLossTitle`, `'DisputeStatusByDisputeCode Title'`)
that interpolate `SelYQ = SELECTEDVALUE(DimDates[PeriodYearQuarter])` and
`FORMAT(TODAY(), "MM/dd/yyyy")` — purely cosmetic, not reconciliation targets.

The full measure inventory (ProgramTrending, RebateVariance waterfall,
DisputeAging, OutstandingDisputes, FedSupplemental, StateSummary,
PaymentOverview, ProgramSummary — ~70 more measures against `FactMedicaid`) is
**not wired to any visual on either page of this report file**. Treat them as
out of scope for this report's tests/automation unless a future page starts
using them; they likely belong to a larger sibling "Rebate/Payment/Program"
report this file doesn't include.

## Report pages

Page IDs match `mapping/reports.yaml`.

**"Dispute Outcomes Summary"** (`817f95ee577e6f4d4b17`, the only visible page)
Page-level filter: `Win Loss Status` is not null/blank.
- Slicers: Dispute Code, Invoice Quarter (default locked `2025-Q1`, single-select), Product Family/NDC11, Program/Program Name
- Table "Disputes Detail Analysis": Invoice Quarter, State, Program, Program Name, Product Family Name, NDC11, Dispute Code, Utilization Quarter, Invoice Amount, Disputed Amount, Rebate Amount Due, Status (red=Loss, blue=Open, green=Win)
- 100% stacked bar "Dispute Status by Dispute Code": category=Dispute Codes, series=Win Loss Status, value=TotalDisputeCount; colors fixed Open=`#0070C0`, Win=`#00B050`, Loss=`#FF0000`
- Bookmarks: "Top Product Families" (filters to non-blank dispute code), "Top States" (no captured filter)

**"Page 1"** (`c8d7d9d06205bd443aa0`, hidden in view mode but still reachable via deep link)
Scratch/staging page: 4 cards (Total Disputed $, Win/Loss/Open %), two pivot
tables (Dispute Code × Win/Loss/Open breakdown; Dispute Code × PeriodYearQuarter
matrix), two basic-list slicers hardcoded to `'QW'` / `'2023-Q1'`.

## Column crosswalk: report ↔ data extract ↔ view

Source: manually supplied column-mapping sheet for
`EXPORT_MEDICAID_DISPUTE_DETAIL_REPORT` (the view `FactMedicaidDispute` imports
from). Four names can exist for the same piece of data — report visuals and
DAX measures use the **Report Column Name**, but raw Snowflake SQL against the
view must use the **View Column Name**.

| Report Column Name | Technical Column Name (Data Extract) | Business Column Name (Data Extract) | View Column Name |
|---|---|---|---|
| Invoice Quarter | `invoice_quarter` | Invoice Quarter | `INVOICE_DATE` |
| State | `state` | State | `STATE` |
| Program Name | N/A | | Program Title |
| Dispute Code | | | `DISPUTE_CODE` |
| Utilization Quarter | `util_quarter` | Utilization Quarter | `UTIL_QTR` |
| Product Family Name | `prod_family` | Product Family | `PROD_FAMILY` |
| NDC11 | `ndc11` | NDC-11 | `NDC11` |
| Disputed Units | `units_disp` | Units Disputed | `UNITS_DISP` |
| Invoice Amount | `inv_amt` | Invoice Amount | `INV_AMT` |
| Disputed Amount | `amt_disputed` | Disputed Amount | `AMT_DISPUTED` |
| Rebate Amount Due | `packet_amt` | Packet Amount | `PACKET_AMT` |

✅ **Resolved (confirmed live, 2026-08-28) — explanation (a) above:** ran
`DESCRIBE VIEW platform_export_adapter.export_medicaid_dispute_detail_report`
against live Snowflake (RSA key-pair auth — see CLAUDE.md's Credentials
section and `utils/snowflake_client.py`). The
view's actual exposed columns ARE the spaced, mixed-case report-label form —
`"Disputed Amount"`, `"Win Loss Status"`, `"Invoice Date"`, etc. — exactly what
`mapping/measures.yaml`'s SQL already used. This crosswalk sheet's short
uppercase **View Column Name** column (`AMT_DISPUTED`, `INV_AMT`, `PACKET_AMT`)
does not describe this view; it most likely documents a different upstream
"Data Extract" artifact. Treat this sheet as background/lineage info, not as
literal identifiers to use in SQL against `EXPORT_MEDICAID_DISPUTE_DETAIL_REPORT`.

⚠️ **Real bug found while confirming the above** (see `mapping/measures.yaml`
known_issues #5): `"Invoice Date"` and `"Utilization Date"` are not stored
dates — `GET_DDL` on the view shows they're computed from
`medi_invoice.util_quarter` / `medi_submission.util_quarter` via
`REPLACE(lower(...), 'q1', '-03-31')` chains covering only q1–q4. A quarter
code outside that range anywhere in the underlying table (unrelated to
`"client" = 'ferring'`) leaves unreplaced text like `"2019q5"`, and comparing
`"Invoice Date"` directly against a `DATE` crashes the whole query — Snowflake
evaluates the cast during predicate pushdown, before the `"client"` filter
would exclude that row. Every measure's SQL now wraps the column in
`TRY_TO_DATE()` to avoid this (fixed in `mapping/measures.yaml`).

## Related files in this project

- `mapping/reports.yaml` — page IDs, report/group GUIDs, deep-link URLs
- `mapping/measures.yaml` — reconciliation SQL + DAX for the 6 measures actually
  under test, plus the known-issues list referenced above
- `features/powerbi_validation.feature`, `features/steps/` — BDD tests exercising this report
  (KPI reconciliation + visual QA only — the schema/contract check and `.pbix` integrity lint that
  used to live here were removed 2026-08-28, since this project no longer relies on the local `.pbix`)
- `scripts/generate_measure_from_jira.py` + `utils/claude_agent.generate_measure_from_ticket()` —
  drafts a candidate SQL/DAX measure pair from a Jira ticket's description, using this skill file
  as the schema/crosswalk context. Output is staged to `mapping/generated/` for human review, never
  written straight into `mapping/measures.yaml` — see the "Discrepancy to resolve" note above for why
  generated SQL can't be trusted as an oracle without checking it against live Snowflake first.
