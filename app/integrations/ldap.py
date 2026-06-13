"""LDAP integration — authentication and group membership.

Wraps server.py LDAP logic for use as an injectable integration.

# TODO Phase 2 — refactor from server.py; keep same two-step bind flow
"""
from __future__ import annotations


def authenticate(
    username: str,
    password: str,
    *,
    ldap_config: dict,
) -> tuple[str, str, list[str]]:
    """Verify username/password against LDAP.

    Returns (username, display_name, groups).
    Raises PermissionError for wrong credentials, RuntimeError for config/connectivity.

    # TODO Phase 2
    """
    raise NotImplementedError


def groups_for_user(username: str, *, ldap_config: dict) -> list[str]:
    """Return group memberships for a user.

    # TODO Phase 2
    """
    raise NotImplementedError
