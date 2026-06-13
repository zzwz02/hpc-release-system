"""Gerrit integration — git archive fetch and plan push.

Uses subprocess (blocking); Starlette puts plain `def` handlers in a thread
pool automatically, so this does not block the event loop.

# TODO Phase 2 — refactor from server.py:1428-1480
"""
from __future__ import annotations


def gerrit_remote_url(git_url: str, *, hpc_gerrit_prefix: str, hpc_gerrit_root: str) -> str:
    """Resolve a (possibly short) git_url to a full Gerrit remote URL.

    # TODO Phase 2
    """
    raise NotImplementedError


def fetch_app_info(git_url: str, branch: str, *, project_root: str) -> tuple[str, str]:
    """Fetch app_info.json from Gerrit; return (raw_json, commit_id).

    # TODO Phase 2
    """
    raise NotImplementedError
