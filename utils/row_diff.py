"""Deterministic row-set diffing for grouped/chart-level reconciliation.

Row alignment (matching rows between two systems by a composite group key) is
plain data processing, not judgment -- so it happens here in code, not handed
to Claude. Claude's role (see claude_agent.reconcile_grouped_metric) is limited
to judging the precomputed diff against tolerance and explaining discrepancies,
the same division of labor as the scalar reconcile_metric: Claude judges, it
doesn't compute.
"""


def _group_key(row: dict, columns: list[str]) -> tuple:
    return tuple(row.get(c) for c in columns)


def _delta_pct(snowflake_value: float, powerbi_value: float) -> float:
    if snowflake_value == 0:
        return 0.0 if powerbi_value == 0 else float("inf")
    return abs(snowflake_value - powerbi_value) / abs(snowflake_value) * 100


def diff_grouped_rows(
    snowflake_rows: list[dict],
    powerbi_rows: list[dict],
    snowflake_group_columns: list[str],
    powerbi_group_columns: list[str],
    snowflake_value_column: str,
    powerbi_value_column: str,
) -> dict:
    """Align two row-sets by group key and compute per-group deltas.

    Group keys are rendered as a single readable string (not a tuple), so the
    result survives json.dumps() straightforwardly for the Claude prompt and
    Allure attachment.
    """
    sf_map = {_group_key(row, snowflake_group_columns): row[snowflake_value_column] for row in snowflake_rows}
    pbi_map = {_group_key(row, powerbi_group_columns): row[powerbi_value_column] for row in powerbi_rows}

    def render(key: tuple) -> str:
        return " | ".join(str(part) for part in key)

    matched = []
    for key in sf_map.keys() & pbi_map.keys():
        sf_value = float(sf_map[key])
        pbi_value = float(pbi_map[key])
        matched.append(
            {
                "group": render(key),
                "snowflake_value": sf_value,
                "powerbi_value": pbi_value,
                "delta_pct": _delta_pct(sf_value, pbi_value),
            }
        )
    matched.sort(key=lambda row: row["group"])

    return {
        "matched": matched,
        "missing_in_powerbi": sorted(render(key) for key in sf_map.keys() - pbi_map.keys()),
        "missing_in_snowflake": sorted(render(key) for key in pbi_map.keys() - sf_map.keys()),
    }
