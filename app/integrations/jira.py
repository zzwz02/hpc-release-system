"""Jira integration — issue creation for CICD dispatch.

Called after transaction commit (in thread pool, failure does not roll back).

# TODO Phase 2 — wrap release_system/jira_client.py
"""
from __future__ import annotations


def create_issue(
    title: str,
    description: str,
    *,
    jira_config: dict,
) -> str:
    """Create a Jira issue and return its key/ID.

    # TODO Phase 2
    """
    raise NotImplementedError
