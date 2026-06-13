"""Jira integration — issue creation for CICD dispatch.

Called after transaction commit (in thread pool, failure does not roll back).
Wraps release_system/jira_client.py with no behavioural changes.
"""
from __future__ import annotations

import logging
import sqlite3

from release_system import jira_client

logger = logging.getLogger(__name__)


def load_config(conf_path: str | None = None) -> dict | None:
    """Load jira.conf.  Returns None if file missing or required keys absent."""
    return jira_client.load_config(conf_path)


def compute_title(
    conn: sqlite3.Connection,
    request_type: str,
    payload: dict,
    task_id: str | None,
) -> str:
    """Compute the Jira issue title for a CICD request."""
    return jira_client.compute_title(conn, request_type, payload, task_id)


def build_description(
    *,
    request_id: int | None,
    request_type: str,
    payload: dict,
    task_id: str | None,
    submitter: str,
    title: str,
    review_note: str = "",
) -> str:
    """Build a Jira wiki-markup description string from a CICD request."""
    return jira_client.build_description(
        request_id=request_id,
        request_type=request_type,
        payload=payload,
        task_id=task_id,
        submitter=submitter,
        title=title,
        review_note=review_note,
    )


def create_issue(
    title: str,
    description: str,
    *,
    jira_config: dict,
) -> str:
    """Create a Jira issue and return its key/ID (e.g. 'SPD-456').

    Raises on failure — callers must catch and log; do NOT let Jira errors
    roll back a DB transaction.
    """
    return jira_client.create_issue(jira_config, title, description=description)
