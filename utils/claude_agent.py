"""Claude-backed reasoning steps for the Power BI validation framework.

Every call shells out to the `claude` CLI in headless mode (`claude -p ...`)
rather than using the `anthropic` SDK. This authenticates with your existing
Claude Code login (OAuth/subscription) -- no ANTHROPIC_API_KEY needed. Do not
add `--bare`: that mode forces ANTHROPIC_API_KEY/apiKeyHelper auth and never
reads the OAuth/keychain login, which defeats the point of this module.

Structured checks (reconciliation, schema validation, visual comparison) pass
`--json-schema` so the CLI validates the model's output and returns it in the
`structured_output` field of the run's JSON payload -- the CLI-mode equivalent
of the Anthropic API's `output_config.format`, so `result["status"]` stays a
controlled enum and pytest assertions stay deterministic. `root_cause_analysis`
is a plain prose call (no schema) -- the point there is prose a human reads.

Cost/latency note: each invocation is a fresh `claude` process with no session
to resume, so every call re-sends the full Claude Code system prompt/context.
Anthropic's server-side prompt cache (~5min TTL, sometimes longer) means calls
made back-to-back (e.g. a pytest run across several measures) mostly hit cache
after the first one, but a call made in isolation pays full price -- notably
more expensive per call than a lean SDK call with no Claude Code context.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
RCA_MODEL = os.environ.get("ANTHROPIC_RCA_MODEL", MODEL)
DEFAULT_EFFORT = os.environ.get("ANTHROPIC_EFFORT", "low")
RCA_EFFORT = os.environ.get("ANTHROPIC_RCA_EFFORT", "medium")
CLI_TIMEOUT_S = int(os.environ.get("CLAUDE_CLI_TIMEOUT_S", "180"))

RECONCILIATION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail"]},
        "delta_pct": {"type": "number"},
        "within_tolerance": {"type": "boolean"},
        "likely_cause": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["status", "delta_pct", "within_tolerance", "likely_cause", "explanation"],
    "additionalProperties": False,
}

MEASURE_GENERATION_SCHEMA = {
    "type": "object",
    "properties": {
        "measure_name": {"type": "string"},
        "description": {"type": "string"},
        "snowflake_sql": {"type": "string"},
        "powerbi_dax": {"type": "string"},
        "tolerance_pct": {"type": "number"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "measure_name",
        "description",
        "snowflake_sql",
        "powerbi_dax",
        "tolerance_pct",
        "confidence",
        "assumptions",
        "open_questions",
    ],
    "additionalProperties": False,
}

GROUPED_RECONCILIATION_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail"]},
        "out_of_tolerance_groups": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["status", "out_of_tolerance_groups", "explanation"],
    "additionalProperties": False,
}

VISUAL_COMPARISON_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["pass", "fail", "warn"]},
        "severity": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "differences": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
    "required": ["status", "severity", "differences", "explanation"],
    "additionalProperties": False,
}


_claude_exe: str | None = None


def _claude_executable() -> str:
    """Resolve the `claude` CLI to a full path.

    On Windows, `claude` is typically an npm-installed `.cmd` shim, not a bare
    `claude.exe`. `subprocess.run(["claude", ...])` without `shell=True` calls
    Windows' CreateProcess directly, which -- unlike a real shell -- does NOT
    search PATHEXT extensions (.CMD/.BAT/...) for a bare name, so it fails with
    FileNotFoundError even though `claude` works fine when typed in a terminal.
    `shutil.which()` performs the same PATHEXT-aware search a shell would, so
    resolving to a full path here (once, cached) fixes that without needing
    `shell=True` (which would reintroduce shell-quoting/injection concerns for
    the prompt/schema arguments passed alongside it).

    `shutil.which()` still depends on the `PATH` the *current process* inherited,
    which isn't always the same PATH your interactive terminal has (an IDE run
    configuration, a service, or a differently-launched shell can all inherit a
    stripped-down PATH that never saw an installer's PATH update). If PATH lookup
    fails, fall back to the actual locations the Claude Code installer uses, and
    finally to an explicit `CLAUDE_CLI_PATH` override for anything nonstandard.
    """
    global _claude_exe
    if _claude_exe is not None:
        return _claude_exe

    override = os.environ.get("CLAUDE_CLI_PATH")
    if override:
        if not Path(override).is_file():
            raise RuntimeError(f"CLAUDE_CLI_PATH is set to '{override}' but that file doesn't exist")
        _claude_exe = override
        return _claude_exe

    resolved = shutil.which("claude")
    if resolved is not None:
        _claude_exe = resolved
        return _claude_exe

    candidates = [
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            _claude_exe = str(candidate)
            return _claude_exe

    raise RuntimeError(
        "`claude` CLI not found on PATH or in known install locations -- is Claude Code "
        "installed? If it's installed somewhere nonstandard, set CLAUDE_CLI_PATH to its full path."
    )


def _run_claude(
    prompt: str,
    system: str,
    model: str,
    effort: str,
    schema: dict | None = None,
    allowed_tools: list[str] | None = None,
    extra_dirs: list[str] | None = None,
) -> dict:
    """Invoke the `claude` CLI headlessly and return its parsed result payload."""
    cmd = [
        _claude_executable(),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        "--effort",
        effort,
        "--no-session-persistence",
        "--system-prompt",
        system,
    ]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema)]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools), "--permission-mode", "bypassPermissions"]
    else:
        cmd += ["--tools", ""]
    for d in extra_dirs or []:
        cmd += ["--add-dir", d]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_S)
    except FileNotFoundError as exc:
        raise RuntimeError("`claude` CLI not found on PATH -- is Claude Code installed?") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"`claude` CLI timed out after {CLI_TIMEOUT_S}s") from exc

    if proc.returncode != 0:
        raise RuntimeError(f"`claude` CLI exited {proc.returncode}: {proc.stderr.strip()}")

    payload = json.loads(proc.stdout)
    if payload.get("is_error"):
        raise RuntimeError(f"`claude` CLI reported an error: {payload.get('result')}")
    return payload


def _json_call(
    system: str,
    prompt: str,
    schema: dict,
    effort: str = DEFAULT_EFFORT,
    model: str = MODEL,
    allowed_tools: list[str] | None = None,
    extra_dirs: list[str] | None = None,
) -> dict:
    payload = _run_claude(
        prompt,
        system=system,
        model=model,
        effort=effort,
        schema=schema,
        allowed_tools=allowed_tools,
        extra_dirs=extra_dirs,
    )
    if "structured_output" in payload:
        return payload["structured_output"]
    return json.loads(payload["result"])


def reconcile_metric(
    measure_name: str,
    snowflake_value: float,
    powerbi_value: float,
    tolerance_pct: float,
    context: str = "",
) -> dict:
    prompt = (
        f"Measure: {measure_name}\n"
        f"Snowflake (source of truth) value: {snowflake_value}\n"
        f"Power BI (reported) value: {powerbi_value}\n"
        f"Allowed tolerance: {tolerance_pct}%\n"
        f"Additional context: {context or 'none'}\n\n"
        "Determine whether the Power BI value reconciles with the Snowflake value within "
        "tolerance. Compute the percentage delta. If out of tolerance, name the most likely "
        "category of discrepancy (e.g. refresh lag, filter mismatch, currency conversion, "
        "aggregation grain mismatch)."
    )
    return _json_call(
        system="You are a data reconciliation analyst validating Power BI reports against Snowflake.",
        prompt=prompt,
        schema=RECONCILIATION_SCHEMA,
    )


def reconcile_grouped_metric(measure_name: str, diff: dict, tolerance_pct: float) -> dict:
    """Judge a precomputed row-set diff (see utils.row_diff.diff_grouped_rows) against
    tolerance -- for chart/table data where a single scalar total isn't enough to catch
    a miscounted or swapped category.

    Row alignment is already done deterministically before this call (see `diff`'s
    `matched`/`missing_in_powerbi`/`missing_in_snowflake` keys); Claude's job here is the
    same as reconcile_metric's -- judge tolerance and explain discrepancies, not
    recompute the comparison itself.
    """
    prompt = (
        f"Measure (grouped chart/table data): {measure_name}\n"
        f"Allowed per-group tolerance: {tolerance_pct}%\n\n"
        f"Precomputed row-level diff between Snowflake (source of truth) and Power BI, "
        f"already matched by group key with deltas already computed:\n"
        f"{json.dumps(diff, indent=2, default=str)}\n\n"
        "Any group listed under missing_in_powerbi or missing_in_snowflake is an automatic "
        "fail regardless of tolerance. For matched groups, fail only if a group's delta_pct "
        "exceeds the allowed tolerance. List every group (by its 'group' string, or the "
        "missing-group string) that causes the fail, and explain the likely cause."
    )
    return _json_call(
        system=(
            "You are a data reconciliation analyst validating a Power BI chart's or table's "
            "grouped/breakdown data against Snowflake."
        ),
        prompt=prompt,
        schema=GROUPED_RECONCILIATION_SCHEMA,
    )


def compare_screenshots(baseline_path: str, current_path: str, measure_name: str = "") -> dict:
    baseline_abs = str(Path(baseline_path).resolve())
    current_abs = str(Path(current_path).resolve())
    prompt = (
        f"Read the BASELINE screenshot at {baseline_abs} (expected, known-good state) and the "
        f"CURRENT screenshot at {current_abs} (latest render, to be checked) for the "
        f"'{measure_name}' Power BI report. Compare them: flag layout shifts, missing/changed "
        "visuals, broken charts, error banners, or materially different values. Ignore trivial "
        "rendering noise (anti-aliasing, cursor position, timestamp text)."
    )
    return _json_call(
        system="You are a meticulous visual QA reviewer for Power BI dashboards.",
        prompt=prompt,
        schema=VISUAL_COMPARISON_SCHEMA,
        allowed_tools=["Read"],
        extra_dirs=list({str(Path(baseline_path).resolve().parent), str(Path(current_path).resolve().parent)}),
    )


def generate_measure_from_ticket(
    ticket: dict,
    schema_context: str,
    example_measures: str,
    effort: str = DEFAULT_EFFORT,
) -> dict:
    """Draft a candidate Snowflake SQL / Power BI DAX measure pair from a Jira ticket.

    `schema_context` is the report's data-model writeup (e.g. skill.md content) --
    table/column names, relationships, and known quirks -- without it Claude has
    no grounding for real identifiers and will hallucinate plausible-looking SQL.
    `example_measures` is a few existing entries from mapping/measures.yaml, used
    as few-shot style/tolerance examples, not as facts to reconcile against.

    The output is a draft, not a verified oracle: low confidence or non-empty
    open_questions means a human should resolve the ambiguity in the ticket (or
    check the SQL against live Snowflake) before it's trusted as ground truth.
    """
    ticket_text = (
        f"Ticket: {ticket['key']}\n"
        f"Summary: {ticket.get('summary', '')}\n\n"
        f"Description:\n{ticket.get('description', '(none)')}\n\n"
        + (
            "Comments:\n" + "\n---\n".join(ticket.get("comments", []))
            if ticket.get("comments")
            else "Comments: (none)"
        )
    )
    prompt = (
        f"{ticket_text}\n\n"
        f"--- Report data model / crosswalk (source of truth for table & column names) ---\n"
        f"{schema_context}\n\n"
        f"--- Existing measures in this project, for SQL/DAX style and tolerance conventions ---\n"
        f"{example_measures}\n\n"
        "Task: draft a new reconciliation measure that tests the calculation described in the "
        "ticket. Produce a Snowflake SQL query (scalar aggregate, matching the style of the "
        "existing measures) and an equivalent Power BI DAX query (EVALUATE ROW(...), matching the "
        "existing measures) that should return the same value. Only use table/column names that "
        "appear in the schema context above -- never invent one. If the ticket is ambiguous about "
        "grain, filters, or which columns apply, state your interpretation in `assumptions` and "
        "list anything that still needs human clarification in `open_questions`. Set `confidence` "
        "to 'low' if you had to guess at a column name or business rule not explicitly present in "
        "the schema context."
    )
    return _json_call(
        system=(
            "You are a QA engineer drafting a Power BI reconciliation test from a Jira ticket. "
            "You are grounded strictly by the provided schema context -- you do not have access "
            "to live Snowflake or Power BI, so you cannot verify the SQL executes or the DAX "
            "returns the right value. Be conservative: prefer flagging an open question over "
            "guessing a column name or business rule."
        ),
        prompt=prompt,
        schema=MEASURE_GENERATION_SCHEMA,
        effort=effort,
    )


def root_cause_analysis(failure_context: dict) -> str:
    payload = _run_claude(
        json.dumps(failure_context, default=str, indent=2),
        system=(
            "You are a data platform reliability engineer performing root cause analysis on a "
            "failed Power BI validation check. Be specific and reference the data provided. "
            "Suggest the most likely cause and a concrete next diagnostic step."
        ),
        model=RCA_MODEL,
        effort=RCA_EFFORT,
    )
    return payload.get("result", "")
