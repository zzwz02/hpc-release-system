"""Pure local-authentication rules shared by bootstrap and services."""
from __future__ import annotations

import hashlib
import secrets

PBKDF2_ITERATIONS = 120_000

# Test/bootstrap accounts recreated by clear_business_data.
DEFAULT_USERS: tuple[tuple[str, str, str], ...] = (
    ("rm", "rm", "RM"),
    ("owner_test", "owner_test", "Owner"),
    ("qa", "qa", "QA"),
    ("spd_test", "spd_test", "SPD"),
    ("guest", "guest", "Guest"),
)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = encoded.split("$", 1)
    actual = hash_password(password, salt).split("$", 1)[1]
    return secrets.compare_digest(actual, expected)
