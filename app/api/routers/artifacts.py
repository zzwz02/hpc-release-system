"""Artifacts router — release artifact generation and download.

Endpoints (faithful port of server.py):
  GET  /api/artifacts/{kind}           — download one artifact (plain text)
  GET  /api/test-scope.csv             — download test-scope CSV (plain text)
  POST /api/artifacts/generate         — regenerate draft artifacts
  POST /api/artifacts/manager-review   — generate manager-review CSV
  POST /api/gerrit/plan                — return Gerrit push plan JSON
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response

from app.deps import get_db, require_login, require_roles
from app.services import artifact_service

router = APIRouter(tags=["artifacts"])


# ---------------------------------------------------------------------------
# GET /api/artifacts/{kind}
# ---------------------------------------------------------------------------

def get_artifact(
    kind: str,
    release_id: str = "",
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Download one artifact as plain text (or CSV).

    Mirrors server.py:501-525.
    """
    try:
        row = artifact_service.get_artifact(
            conn, release_id, kind, role=user["role"]
        )
    except KeyError:
        return Response(content="artifact not found", status_code=404)

    name: str = row["name"]
    content: str = row["content"]
    generated_at: str = row["generated_at"]

    if name.lower().endswith(".csv"):
        media_type = "text/csv; charset=utf-8-sig"
        # BOM + content, matching server.py:521-523
        body = "﻿".encode() + content.encode()
    else:
        media_type = "text/plain; charset=utf-8"
        body = content.encode()

    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Artifact-Name": name,
        "X-Artifact-Generated-At": generated_at,
    }
    return Response(content=body, media_type=media_type, headers=headers)


router.add_api_route(
    "/api/artifacts/{kind}",
    get_artifact,
    methods=["GET"],
)


# ---------------------------------------------------------------------------
# GET /api/test-scope.csv
# ---------------------------------------------------------------------------

def get_test_scope_csv(
    release_id: str = "",
    _user: dict = Depends(require_roles("RM", message="RM role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Download the test-scope CSV for a release.

    Mirrors server.py:402-413.
    """
    if not release_id:
        raise ValueError("release_id is required")
    csv_text, filename = artifact_service.get_test_scope_csv(conn, release_id)
    # BOM + content as bytes (mirrors server.py:413)
    body = "﻿".encode() + csv_text.encode()
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8-sig",
        headers=headers,
    )


router.add_api_route(
    "/api/test-scope.csv",
    get_test_scope_csv,
    methods=["GET"],
)


# ---------------------------------------------------------------------------
# POST /api/artifacts/generate
# ---------------------------------------------------------------------------

async def post_generate_artifacts(
    request: Request,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Regenerate draft artifacts for a release.

    Mirrors server.py:722-730.
    """
    body = await request.json()
    if body.get("final"):
        raise RuntimeError("Final artifacts 只能通过最终 lock 生成")
    artifacts = artifact_service.generate_artifacts(
        conn,
        body["release_id"],
        user=user["username"],
        role=user["role"],
    )
    return {"artifacts": list(artifacts)}


router.add_api_route(
    "/api/artifacts/generate",
    post_generate_artifacts,
    methods=["POST"],
)


# ---------------------------------------------------------------------------
# POST /api/artifacts/manager-review
# ---------------------------------------------------------------------------

async def post_manager_review(
    request: Request,
    user: dict = Depends(require_roles("RM", message="RM role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Generate the manager-review CSV and persist it.

    Mirrors server.py:732-742.
    """
    body = await request.json()
    content = artifact_service.generate_manager_review(
        conn,
        body["release_id"],
        body.get("fields") or None,
        user=user["username"],
        role=user["role"],
    )
    return {"artifact": "manager_review", "bytes": len(content.encode())}


router.add_api_route(
    "/api/artifacts/manager-review",
    post_manager_review,
    methods=["POST"],
)


# ---------------------------------------------------------------------------
# POST /api/gerrit/plan
# ---------------------------------------------------------------------------

async def post_gerrit_plan(
    request: Request,
    user: dict = Depends(require_roles("RM", message="RM role required")),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return the Gerrit push plan for a locked release.

    Mirrors server.py:744-747.
    """
    body = await request.json()
    return artifact_service.gerrit_push_plan(
        conn,
        body["release_id"],
        user=user["username"],
        role=user["role"],
    )


router.add_api_route(
    "/api/gerrit/plan",
    post_gerrit_plan,
    methods=["POST"],
)
