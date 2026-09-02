"""Power BI REST API access: DAX/XMLA-style queries, dataset schema, refresh polling.

Auth is a service-principal client-credentials flow against Azure AD. Token
fetch and every REST call go through with_backoff() so transient 429/5xx
responses and connection resets don't fail a whole validation run.
"""

import time

import msal
import requests

from utils.retry import with_backoff
from utils.secrets_manager import get_powerbi_secrets

BASE_URL = "https://api.powerbi.com/v1.0/myorg"
SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]

_token_cache: dict = {"access_token": None, "expires_at": 0}


def _authority(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}"


@with_backoff(retry_statuses=())
def get_access_token() -> str:
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    creds = get_powerbi_secrets()
    app = msal.ConfidentialClientApplication(
        client_id=creds["client_id"],
        client_credential=creds["secret_value"],
        authority=_authority(creds["tenant_id"]),
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire Power BI token: {result.get('error_description')}")

    _token_cache["access_token"] = result["access_token"]
    _token_cache["expires_at"] = now + result.get("expires_in", 3600)
    return _token_cache["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}", "Content-Type": "application/json"}


@with_backoff()
def _post(url: str, payload: dict) -> dict:
    resp = requests.post(url, headers=_headers(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json() if resp.content else {}


@with_backoff()
def _get(url: str) -> dict:
    resp = requests.get(url, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def execute_dax_query(dataset_id: str, dax_query: str) -> list[dict]:
    url = f"{BASE_URL}/datasets/{dataset_id}/executeQueries"
    payload = {"queries": [{"query": dax_query}], "serializerSettings": {"includeNulls": True}}
    result = _post(url, payload)
    tables = result["results"][0]["tables"]
    return tables[0]["rows"] if tables else []


def execute_scalar_dax(dataset_id: str, dax_query: str) -> float:
    rows = execute_dax_query(dataset_id, dax_query)
    if not rows:
        raise ValueError("DAX query returned no rows")
    (value,) = rows[0].values()
    return float(value)


def trigger_dataset_refresh(dataset_id: str, notify_option: str = "NoNotification") -> None:
    _post(f"{BASE_URL}/datasets/{dataset_id}/refreshes", {"notifyOption": notify_option})


def poll_refresh_status(dataset_id: str, timeout_s: int = 600, poll_interval_s: int = 15) -> str:
    url = f"{BASE_URL}/datasets/{dataset_id}/refreshes?$top=1"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        refreshes = _get(url).get("value", [])
        if refreshes:
            status = refreshes[0].get("status")
            if status in ("Completed", "Failed", "Disabled", "Cancelled"):
                return status
        time.sleep(poll_interval_s)
    raise TimeoutError(f"Dataset {dataset_id} refresh did not complete within {timeout_s}s")
