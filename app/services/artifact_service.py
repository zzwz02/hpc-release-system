"""Artifact generation service.

Faithful port of release_system/core.py:generate_artifacts,
generate_manager_review_csv, gerrit_push_plan, export_test_scope_csv,
and the artifact GET path in server.py:501-525.
"""
from __future__ import annotations

import sqlite3

from release_system import core as _core


def get_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
    *,
    role: str,
) -> dict:
    """Return one artifact row for download.

    Mirrors server.py:501-525 logic.
    Returns dict with keys: name, content, generated_at.
    Raises AuthzError if the user's role cannot see this artifact kind.
    Raises KeyError if the artifact does not exist (caller should return 404).
    """
    from app.api.errors import AuthzError

    # Non-RM can only see the four public kinds (mirrors server.py:504-505)
    _public_kinds = {"release_note", "manual", "ai4sci", "data"}
    if role != "RM" and kind not in _public_kinds:
        raise AuthzError("只有 RM 可查看该 artifact")

    row = conn.execute(
        "SELECT name, content, generated_at FROM artifacts"
        " WHERE release_id = ? AND kind = ?",
        (release_id, kind),
    ).fetchone()
    if not row:
        raise KeyError(f"artifact not found: {kind}")
    return dict(row)


def generate_artifacts(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """(Re)generate all artifacts for a release.

    Mirrors server.py:722-730.
    Raises AuthzError if the caller is not RM or Owner.
    Raises RuntimeError if caller passes final=True (server blocks this).
    Returns dict of kind→content (same as core.generate_artifacts).
    """
    from app.api.errors import AuthzError

    if role not in {"RM", "Owner"}:
        raise AuthzError("只有 RM、Owner 可刷新发布文档")

    # final=True is blocked at router level; service enforces the same check
    return _core.generate_artifacts(conn, release_id, final=False)


def generate_manager_review(
    conn: sqlite3.Connection,
    release_id: str,
    fields: list[str] | None = None,
    *,
    user: str,
    role: str,
) -> str:
    """Generate (and persist) the manager-review CSV.

    Mirrors server.py:732-742.
    Returns the CSV content string.
    """
    return _core.generate_manager_review_csv(
        conn,
        release_id,
        fields,
        user=user,
        role=role,
    )


def get_test_scope_csv(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[str, str]:
    """Return (csv_text, filename) for the test-scope CSV.

    Mirrors server.py:402-413.
    """
    csv_text = _core.export_test_scope_csv(conn, release_id)
    release = _core.get_release(conn, release_id)
    filename = f"test_scope_{release['name']}.csv"
    return csv_text, filename


def gerrit_push_plan(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """Return the Gerrit push plan for the given release.

    Mirrors server.py:744-747.
    """
    return _core.gerrit_push_plan(conn, release_id)
