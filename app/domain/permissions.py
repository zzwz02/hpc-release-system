"""Role constants — plan §3.7 (Ruling-C values).

Pure data: no HTTP, no DB, unit-testable in isolation.
Also holds the pure LDAP group→role mapping (mirrors core.py:591-617).
"""
from __future__ import annotations

import fnmatch

# All valid roles in the system (mirrors core.py:491)
ROLES: frozenset[str] = frozenset({"RM", "Owner", "QA", "Admin", "SPD", "Guest"})

# --- CICD roles (Ruling-C §3.7) -----------------------------------------------
# Admin removed from both sets per Ruling-C.
CICD_CREATE_ROLES: frozenset[str] = frozenset({"Owner", "RM"})
CICD_APPROVER_ROLES: frozenset[str] = frozenset({"RM"})

# --- Delivery roles -----------------------------------------------------------
CICD_DELIVER_ROLES: frozenset[str] = frozenset({"SPD", "RM"})
CICD_DELIVERIES_VIEW_ROLES: frozenset[str] = frozenset({"SPD", "RM", "Owner"})

# --- General read / write role sets ------------------------------------------
# Used by routers and services as quick membership checks.
READ_ROLES: frozenset[str] = frozenset({"Owner", "RM", "Admin", "QA", "SPD", "Guest"})
WRITE_ROLES: frozenset[str] = frozenset({"Owner", "RM", "QA", "SPD"})

# Release lifecycle roles
RELEASE_WRITE_ROLES: frozenset[str] = frozenset({"RM"})

# App workbench roles (who can submit decisions / edit snapshots)
APP_DECISION_ROLES: frozenset[str] = frozenset({"Owner", "RM"})

# Admin-only actions
ADMIN_ROLES: frozenset[str] = frozenset({"Admin"})

# LDAP group patterns (mirrors core.py:492-494)
LDAP_OWNER_GROUP_PATTERNS: tuple[str, ...] = ("dl.pde_sc*", "dl.pde_sa*")
LDAP_QA_GROUP_PATTERNS: tuple[str, ...] = ("dl.sw_qa*",)
LDAP_SPD_GROUP_PATTERNS: tuple[str, ...] = ("dl.sw_spd*",)


def ldap_group_name(group: str) -> str:
    """Return the CN from a group DN, or the raw group value if it is not a DN."""
    value = str(group or "").strip()
    for part in value.split(","):
        key, sep, item = part.strip().partition("=")
        if sep and key.strip().lower() == "cn":
            return item.strip()
    return value


def ldap_group_matches(group: str, pattern: str) -> bool:
    name = ldap_group_name(group).lower()
    raw = str(group or "").strip().lower()
    pat = pattern.lower()
    return fnmatch.fnmatchcase(name, pat) or fnmatch.fnmatchcase(raw, pat)


def ldap_role_from_groups(groups: list[str] | tuple[str, ...] | None) -> str:
    """Map LDAP/AD memberOf groups to the initial local role for first login."""
    values = [str(group or "").strip() for group in (groups or []) if str(group or "").strip()]
    if any(ldap_group_matches(group, pat) for group in values for pat in LDAP_OWNER_GROUP_PATTERNS):
        return "Owner"
    if any(ldap_group_matches(group, pat) for group in values for pat in LDAP_QA_GROUP_PATTERNS):
        return "QA"
    if any(ldap_group_matches(group, pat) for group in values for pat in LDAP_SPD_GROUP_PATTERNS):
        return "SPD"
    return "Guest"
