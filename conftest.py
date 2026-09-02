import os

import pytest
from dotenv import load_dotenv

load_dotenv()

from utils import jira_client


@pytest.fixture
def test_context():
    """Plain-dict fixture threading state between Given/When/Then steps.

    Deliberately not a step class with self. attributes -- pytest-bdd doesn't
    require one, and a dict is trivially printable mid-run for debugging.
    """
    return {}


@pytest.fixture(scope="session")
def playwright_context():
    from pathlib import Path

    from playwright.sync_api import sync_playwright

    storage_state = os.environ.get("PLAYWRIGHT_STORAGE_STATE")
    storage_state_path = storage_state if storage_state and Path(storage_state).exists() else None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state_path, viewport={"width": 1600, "height": 900})
        yield context
        context.close()
        browser.close()


def pytest_runtest_makereport(item, call):
    if call.when == "call" and call.excinfo is not None:
        try:
            jira_client.create_ticket(
                summary=f"PBI validation failure: {item.name}",
                description=str(call.excinfo.getrepr()),
            )
        except Exception:
            pass
