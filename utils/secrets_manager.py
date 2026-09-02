"""AWS Secrets Manager access for credentials that must not live in .env.

Each secret is stored as a JSON string in Secrets Manager. Non-secret config
(hostnames that aren't sensitive, feature flags, IDs like workspace/dataset)
can still live in .env; only actual credentials are fetched from here.
"""

import json
import os
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


@lru_cache(maxsize=None)
def _get_secret(secret_name: str) -> dict:
    client = boto3.client("secretsmanager", region_name=_AWS_REGION)
    try:
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as exc:
        raise RuntimeError(f"Failed to retrieve secret '{secret_name}' from AWS Secrets Manager") from exc

    secret_string = response.get("SecretString")
    if secret_string is None:
        raise RuntimeError(f"Secret '{secret_name}' has no SecretString payload")
    return json.loads(secret_string)


def get_snowflake_secrets() -> dict:
    """Fetch Snowflake connection credentials.

    Auth is RSA key-pair, not password: SNOWFLAKE_KEY is a base64-encoded,
    passphrase-encrypted PEM private key, and SNOWFLAKE_PASS -- despite the
    name -- is that key's decryption passphrase, not a login password
    (confirmed empirically: plain password auth gets rejected by Snowflake,
    but SNOWFLAKE_PASS successfully decrypts SNOWFLAKE_KEY). See
    utils/snowflake_client.py for where the key is decoded/decrypted.

    Expected secret JSON keys: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER,
    SNOWFLAKE_KEY, SNOWFLAKE_PASS, PLATFORM_WAREHOUSE, PLATFORM_DATABASE,
    PLATFORM_SCHEMA (optional), PLATFORM_ROLE (optional).
    """
    secret_name = os.environ.get("SNOWFLAKE_SECRET_NAME", "qa/platform/data-export-adapter")
    secret = _get_secret(secret_name)
    return {
        "account": secret["SNOWFLAKE_ACCOUNT"],
        "user": secret["SNOWFLAKE_USER"],
        "private_key": secret["SNOWFLAKE_KEY"],
        "private_key_passphrase": secret["SNOWFLAKE_PASS"],
        "warehouse": secret["PLATFORM_WAREHOUSE"],
        "database": secret["PLATFORM_DATABASE"],
        "schema": secret.get("PLATFORM_SCHEMA"),
        "role": secret.get("PLATFORM_ROLE") or None,
    }


def get_powerbi_secrets() -> dict:
    """Fetch the Power BI service-principal credentials.

    Expected secret JSON keys: tenant_id, client_id, secret_value.
    """
    secret_name = os.environ.get("PBI_SECRET_NAME", "qa/icyte-sparc/power-bi-automation")
    secret = _get_secret(secret_name)
    return {
        "tenant_id": secret["tenant_id"],
        "client_id": secret["client_id"],
        "secret_value": secret["secret_value"],
    }
