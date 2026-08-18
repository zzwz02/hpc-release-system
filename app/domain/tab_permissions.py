"""Load the shared top-level tab role matrix.

Tab visibility/direct-route access and APIs that belong exclusively to one tab
must consume this module instead of copying role lists. Operation-level API
permissions remain separate when they are intentionally narrower than a tab.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from app.domain.permissions import ROLES as _KNOWN_ROLES

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PERMISSIONS_PATH = _PROJECT_ROOT / "shared" / "tab_permissions.json"


def _load_tab_permissions() -> Mapping[str, tuple[str, ...]]:
    raw = json.loads(_PERMISSIONS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("shared/tab_permissions.json must contain an object")

    permissions: dict[str, tuple[str, ...]] = {}
    for view, roles in raw.items():
        if not isinstance(view, str) or not isinstance(roles, list):
            raise RuntimeError("Each tab permission must map a view to a role list")
        if not roles or any(not isinstance(role, str) for role in roles):
            raise RuntimeError(f"Tab {view!r} must have a non-empty role list")
        unknown = set(roles) - _KNOWN_ROLES
        if unknown:
            raise RuntimeError(f"Tab {view!r} has unknown roles: {sorted(unknown)}")
        if len(roles) != len(set(roles)):
            raise RuntimeError(f"Tab {view!r} contains duplicate roles")
        permissions[view] = tuple(roles)
    return MappingProxyType(permissions)


TAB_PERMISSIONS = _load_tab_permissions()
ALL_ROLES = tuple(sorted(_KNOWN_ROLES))


def roles_for_tab(view: str) -> tuple[str, ...]:
    try:
        return TAB_PERMISSIONS[view]
    except KeyError as exc:
        raise RuntimeError(f"Missing shared tab permissions for {view!r}") from exc
