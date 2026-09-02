"""Business-mapping lookup: measure name -> Snowflake SQL / Power BI DAX / tolerance.

This replaces hardcoded SQL/DAX in step definitions with a single YAML table,
so adding a new measure is a mapping edit, not a code change.
"""

from pathlib import Path

import yaml

MAPPING_PATH = Path(__file__).resolve().parent.parent / "mapping" / "measures.yaml"


def load_measure(measure_name: str) -> dict:
    with open(MAPPING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    measures = data.get("measures", {})
    if measure_name not in measures:
        raise KeyError(f"No mapping found for measure '{measure_name}' in {MAPPING_PATH}")
    return measures[measure_name]


def load_grouped_measure(measure_name: str) -> dict:
    """Chart/table-level measures: a group-by row set, not a single scalar."""
    with open(MAPPING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    measures = data.get("grouped_measures", {})
    if measure_name not in measures:
        raise KeyError(f"No grouped mapping found for measure '{measure_name}' in {MAPPING_PATH}")
    return measures[measure_name]


def load_consistency_check(check_name: str) -> dict:
    """Pure-DAX internal invariants (e.g. bucketed counts sum to a total) -- no Snowflake."""
    with open(MAPPING_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    checks = data.get("consistency_checks", {})
    if check_name not in checks:
        raise KeyError(f"No consistency check found for '{check_name}' in {MAPPING_PATH}")
    return checks[check_name]
