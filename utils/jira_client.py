"""Jira access: file tickets on validation failure, and read tickets to drive
measure generation.

Ticket-filing is disabled by default (JIRA_ENABLED=false) so the framework
runs standalone without Jira credentials. Enable per-environment via .env.
Reading a ticket (get_ticket) is a separate, always-available capability used
by scripts/generate_measure_from_jira.py -- it needs the same JIRA_BASE_URL /
JIRA_EMAIL / JIRA_API_TOKEN credentials but isn't gated by JIRA_ENABLED, since
that flag only concerns the failure-reporting feature.
"""

import os

import requests

JIRA_ENABLED = os.environ.get("JIRA_ENABLED", "false").lower() == "true"


def create_ticket(summary: str, description: str, labels: list[str] | None = None) -> str | None:
    if not JIRA_ENABLED:
        return None

    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    project_key = os.environ["JIRA_PROJECT_KEY"]
    auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    payload = {
        "fields": {
            "project": {"key": project_key},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description}]}],
            },
            "issuetype": {"name": os.environ.get("JIRA_ISSUE_TYPE", "Bug")},
            "labels": labels or ["pbi-validation"],
        }
    }
    resp = requests.post(f"{base_url}/rest/api/3/issue", json=payload, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()["key"]


def _adf_to_text(node) -> str:
    """Flatten Atlassian Document Format (Jira Cloud's rich-text JSON) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    parts = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            parts.append(node.get("text", ""))
        for child in node.get("content", []) or []:
            parts.append(_adf_to_text(child))
        if node.get("type") in ("paragraph", "heading", "listItem"):
            parts.append("\n")
    elif isinstance(node, list):
        for child in node:
            parts.append(_adf_to_text(child))
    return "".join(parts)


def get_ticket(ticket_key: str) -> dict:
    """Fetch a Jira issue's summary, description, and comments as plain text.

    Used by scripts/generate_measure_from_jira.py to source the business
    definition of a measure to test. Requires JIRA_BASE_URL / JIRA_EMAIL /
    JIRA_API_TOKEN regardless of JIRA_ENABLED, since reading a ticket isn't
    part of the optional failure-reporting feature that flag gates.
    """
    base_url = os.environ["JIRA_BASE_URL"].rstrip("/")
    auth = (os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])
    url = f"{base_url}/rest/api/3/issue/{ticket_key}"
    resp = requests.get(url, params={"fields": "summary,description,comment"}, auth=auth, timeout=30)
    resp.raise_for_status()
    fields = resp.json()["fields"]

    comments = [
        _adf_to_text(c.get("body")).strip()
        for c in (fields.get("comment") or {}).get("comments", [])
    ]

    return {
        "key": ticket_key,
        "summary": fields.get("summary", ""),
        "description": _adf_to_text(fields.get("description")).strip(),
        "comments": [c for c in comments if c],
    }
