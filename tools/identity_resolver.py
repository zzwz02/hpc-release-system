"""Identity resolver — thin re-export from app.identity (canonical implementation).

Phase 0 provided an independent implementation here.  Phase 1 replaces it:
all logic now lives in app/identity.py (the single canonical implementation).
This module re-exports the public API so that any external code that imported
from tools.identity_resolver continues to work unchanged.

Import directly from app.identity in new code.
"""
from __future__ import annotations

from app.identity import (  # noqa: F401
    MANIFEST_BRANCH,
    MANIFEST_REPO_URL,
    RESOLVED_REPO_BASE,
    clear_manifest_cache,
    normalize_git_url,
    repo_to_git_identity,
    resolve_manifest_url,
    same_identity,
)

__all__ = [
    "RESOLVED_REPO_BASE",
    "MANIFEST_REPO_URL",
    "MANIFEST_BRANCH",
    "normalize_git_url",
    "resolve_manifest_url",
    "repo_to_git_identity",
    "same_identity",
    "clear_manifest_cache",
]
