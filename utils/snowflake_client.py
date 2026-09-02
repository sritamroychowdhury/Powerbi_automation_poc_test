"""Snowflake access for the reconciliation source-of-truth queries."""

import base64

import snowflake.connector
from cryptography.hazmat.primitives import serialization

from utils.secrets_manager import get_snowflake_secrets


def _decrypt_private_key(creds: dict) -> bytes:
    """Decode + decrypt the base64 PEM key into DER bytes the connector accepts."""
    pem_bytes = base64.b64decode(creds["private_key"])
    key = serialization.load_pem_private_key(
        pem_bytes,
        password=creds["private_key_passphrase"].encode(),
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_connection():
    creds = get_snowflake_secrets()
    return snowflake.connector.connect(
        account=creds["account"],
        user=creds["user"],
        private_key=_decrypt_private_key(creds),
        warehouse=creds["warehouse"],
        database=creds["database"],
        schema=creds["schema"],
        role=creds["role"],
    )


def run_scalar_query(sql: str) -> float:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        row = cur.fetchone()
        if row is None:
            raise ValueError("Query returned no rows")
        return float(row[0])
    finally:
        conn.close()


def run_query(sql: str) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor(snowflake.connector.DictCursor)
        cur.execute(sql)
        return cur.fetchall()
    finally:
        conn.close()
