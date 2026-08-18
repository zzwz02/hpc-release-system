"""Shared role, tab, and capability policy plus LDAP role mapping.

``shared/access_control.json`` is the only static role matrix. Contextual
authorization (ownership, phase, lock, request status) composes these base
capabilities in services/domain helpers instead of copying role sets.
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_POLICY_PATH = _PROJECT_ROOT / "shared" / "access_control.json"


def _role_mapping(
    raw: object,
    *,
    section: str,
    known_roles: frozenset[str],
) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"access_control.{section} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for name, roles in raw.items():
        if not isinstance(name, str) or not isinstance(roles, list) or not roles:
            raise RuntimeError(f"Each {section} entry must have a non-empty role list")
        if any(not isinstance(role, str) for role in roles):
            raise RuntimeError(f"{section}.{name} contains a non-string role")
        unknown = set(roles) - known_roles
        if unknown:
            raise RuntimeError(f"{section}.{name} has unknown roles: {sorted(unknown)}")
        if len(roles) != len(set(roles)):
            raise RuntimeError(f"{section}.{name} contains duplicate roles")
        result[name] = tuple(roles)
    return MappingProxyType(result)


def _load_policy() -> tuple[
    tuple[str, ...],
    Mapping[str, tuple[str, ...]],
    Mapping[str, tuple[str, ...]],
]:
    raw = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"roles", "tabs", "capabilities"}:
        raise RuntimeError(
            "shared/access_control.json must contain roles, tabs, and capabilities"
        )
    roles = raw["roles"]
    if (
        not isinstance(roles, list)
        or not roles
        or any(not isinstance(role, str) for role in roles)
        or len(roles) != len(set(roles))
    ):
        raise RuntimeError("access_control.roles must be a non-empty unique string list")
    ordered_roles = tuple(roles)
    known_roles = frozenset(ordered_roles)
    return (
        ordered_roles,
        _role_mapping(raw["tabs"], section="tabs", known_roles=known_roles),
        _role_mapping(
            raw["capabilities"],
            section="capabilities",
            known_roles=known_roles,
        ),
    )


ALL_ROLES, TAB_PERMISSIONS, CAPABILITY_PERMISSIONS = _load_policy()
ROLES: frozenset[str] = frozenset(ALL_ROLES)


def _roles_for(
    mapping: Mapping[str, tuple[str, ...]],
    name: str,
    *,
    kind: str,
) -> tuple[str, ...]:
    try:
        return mapping[name]
    except KeyError as exc:
        raise RuntimeError(f"Missing shared {kind} permissions for {name!r}") from exc


def roles_for_tab(view: str) -> tuple[str, ...]:
    return _roles_for(TAB_PERMISSIONS, view, kind="tab")


def roles_for_capability(capability: str) -> tuple[str, ...]:
    return _roles_for(CAPABILITY_PERMISSIONS, capability, kind="capability")


def has_capability(role: str, capability: str) -> bool:
    return role in roles_for_capability(capability)


def capabilities_for_role(role: str) -> tuple[str, ...]:
    if role not in ROLES:
        return ()
    return tuple(
        capability
        for capability, roles in CAPABILITY_PERMISSIONS.items()
        if role in roles
    )


# Compatibility names remain derived aliases, never independent policy.
CICD_CREATE_ROLES = frozenset(roles_for_capability("cicd.request.submit"))
CICD_APPROVER_ROLES = frozenset(roles_for_capability("cicd.request.approve"))
CICD_DELIVER_ROLES = frozenset(roles_for_capability("cicd.delivery.confirm"))
CICD_DELIVERIES_VIEW_ROLES = frozenset(roles_for_capability("cicd.delivery.view"))
RELEASE_WRITE_ROLES = frozenset(roles_for_capability("release.manage"))
APP_DECISION_ROLES = frozenset(
    roles_for_capability("app.edit.any") + roles_for_capability("app.edit.owned")
)
ADMIN_ROLES = frozenset(roles_for_capability("admin.manage"))

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
