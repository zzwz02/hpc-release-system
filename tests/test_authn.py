from __future__ import annotations

import hashlib

from app.domain import authn
from app.services import auth_service


def test_password_hash_uses_the_authoritative_iteration_count() -> None:
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        b"secret",
        b"fixed-salt",
        authn.PBKDF2_ITERATIONS,
    ).hex()
    assert authn.hash_password("secret", "fixed-salt") == f"fixed-salt${expected}"


def test_password_verification_accepts_only_the_matching_password() -> None:
    encoded = authn.hash_password("secret", "fixed-salt")
    assert authn.verify_password("secret", encoded)
    assert not authn.verify_password("wrong", encoded)


def test_auth_service_compatibility_exports_share_domain_definitions() -> None:
    assert auth_service.hash_password is authn.hash_password
    assert auth_service.verify_password is authn.verify_password
    assert auth_service.DEFAULT_USERS is authn.DEFAULT_USERS


def test_default_user_catalog_is_complete() -> None:
    assert authn.DEFAULT_USERS == (
        ("rm", "rm", "RM"),
        ("owner_test", "owner_test", "Owner"),
        ("qa", "qa", "QA"),
        ("spd_test", "spd_test", "SPD"),
        ("guest", "guest", "Guest"),
    )
