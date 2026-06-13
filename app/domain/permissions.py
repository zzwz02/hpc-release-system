"""Role constants — plan §3.7 (Ruling-C values).

Pure data: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

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
