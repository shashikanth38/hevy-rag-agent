# src/db/snowflake_client.py
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from src.config import (
    SNOWFLAKE_ACCOUNT,
    SNOWFLAKE_USER,
    SNOWFLAKE_PRIVATE_KEY_PATH,
    SNOWFLAKE_WAREHOUSE,
    SNOWFLAKE_DATABASE,
    SNOWFLAKE_SCHEMA,
    SNOWFLAKE_ROLE,
)


def get_private_key_bytes() -> bytes:
    with open(SNOWFLAKE_PRIVATE_KEY_PATH, "rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(),
            password=None,
            backend=default_backend(),
        )
    return private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def get_conn() -> snowflake.connector.SnowflakeConnection:
    return snowflake.connector.connect(
        account     = SNOWFLAKE_ACCOUNT,
        user        = SNOWFLAKE_USER,
        private_key = get_private_key_bytes(),
        warehouse   = SNOWFLAKE_WAREHOUSE,
        database    = SNOWFLAKE_DATABASE,
        schema      = SNOWFLAKE_SCHEMA,
        role        = SNOWFLAKE_ROLE,
    )