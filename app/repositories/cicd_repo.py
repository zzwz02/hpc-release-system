"""CICD repository — cicd_tasks and cicd_task_requests table CRUD.

# TODO Phase 1 — implement
"""
from __future__ import annotations

import sqlite3


def get_task(conn: sqlite3.Connection, task_id: str) -> dict | None:
    """Return a cicd_task row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def tasks_for_app(conn: sqlite3.Connection, app_id: str) -> list[dict]:
    """Return all cicd_tasks associated with an app (should be 0 or 1 under 1:1).

    # TODO Phase 1
    """
    raise NotImplementedError


def list_tasks(conn: sqlite3.Connection) -> list[dict]:
    """Return all cicd_tasks.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_task(conn: sqlite3.Connection, *, task_id: str, **fields) -> None:
    """Insert a new cicd_task row.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_task(conn: sqlite3.Connection, task_id: str, **fields) -> None:
    """Update fields on an existing cicd_task.

    # TODO Phase 1
    """
    raise NotImplementedError


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    """Delete a cicd_task row.

    # TODO Phase 1
    """
    raise NotImplementedError


def insert_request(conn: sqlite3.Connection, **fields) -> int:
    """Insert a new cicd_task_request row; return its id.

    # TODO Phase 1
    """
    raise NotImplementedError


def get_request(conn: sqlite3.Connection, request_id: int) -> dict | None:
    """Return a cicd_task_request row or None.

    # TODO Phase 1
    """
    raise NotImplementedError


def list_requests(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    """Return cicd_task_request rows, optionally filtered.

    # TODO Phase 1
    """
    raise NotImplementedError


def update_request(conn: sqlite3.Connection, request_id: int, **fields) -> None:
    """Update fields on an existing cicd_task_request.

    # TODO Phase 1
    """
    raise NotImplementedError
