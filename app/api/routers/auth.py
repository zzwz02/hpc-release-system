"""Auth router — /api/login, /api/login/ldap, /api/logout, /api/me, /api/ldap/status.

Faithful port of server.py do_GET:327-335 and do_POST:540-555.
Response shapes match the old server byte-for-byte.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import JSONResponse

from app.deps import get_db
from app.integrations import ldap as ldap_integration
from app.services import auth_service

router = APIRouter(prefix="/api", tags=["auth"])


# ---------------------------------------------------------------------------
# GET /api/me
# ---------------------------------------------------------------------------

@router.get("/me")
def get_me(
    hpc_session: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Return user info for the current session, or null if not logged in.

    Public endpoint — no auth required (mirrors server.py:327-329).
    Response: {"user": {username, role, display_name}} or {"user": null}
    """
    user = auth_service.whoami(conn, hpc_session)
    if user:
        # Match the old server's response shape: username, role, display_name only
        return JSONResponse({"user": {
            "username": user["username"],
            "role": user["role"],
            "display_name": user.get("display_name", ""),
        }})
    return JSONResponse({"user": None})


# ---------------------------------------------------------------------------
# GET /api/ldap/status
# ---------------------------------------------------------------------------

@router.get("/ldap/status")
def get_ldap_status(request: Request) -> JSONResponse:
    """Return LDAP enabled flag and URI.

    Public endpoint — no auth required (mirrors server.py:331-334).
    Response: {"enabled": bool, "uri": str}
    """
    cfg: dict = getattr(request.app.state, "ldap_config", {"enabled": False, "uri": ""})
    return JSONResponse({
        "enabled": bool(cfg.get("enabled")),
        "uri": cfg.get("uri", ""),
    })


# ---------------------------------------------------------------------------
# POST /api/login
# ---------------------------------------------------------------------------

@router.post("/login")
async def post_login(request: Request, conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:
    """Local username/password login.

    Sets hpc_session cookie on success.
    Raises PermissionError (→ 401) on bad credentials.

    Mirrors server.py:540-546:
        token = core.authenticate(...)
        if not token: raise PermissionError("Invalid username or password")
        send_json({"ok": True}, cookies=[f"hpc_session={token}; ..."])
    """
    body = await request.json()
    result = auth_service.login(
        conn,
        username=body.get("username", ""),
        password=body.get("password", ""),
    )
    if not result.get("ok"):
        raise PermissionError("Invalid username or password")
    token = result["token"]
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "hpc_session",
        token,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# POST /api/login/ldap
# ---------------------------------------------------------------------------

@router.post("/login/ldap")
async def post_login_ldap(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """LDAP username/password login.

    Sets hpc_session cookie on success.
    Raises PermissionError (→ 401) or RuntimeError (→ 400) on failure.

    Mirrors server.py:547-551:
        uname, display, groups = ldap_authenticate(...)
        token = core.ldap_login_or_create(...)
        send_json({"ok": True}, cookies=[...])
    """
    body = await request.json()
    ldap_config: dict = getattr(request.app.state, "ldap_config", {"enabled": False})
    uname, display, groups = ldap_integration.authenticate(
        body.get("username", ""),
        body.get("password", ""),
        ldap_config=ldap_config,
    )
    token = auth_service.login_ldap(conn, uname, display, groups)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "hpc_session",
        token,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


# ---------------------------------------------------------------------------
# POST /api/logout
# ---------------------------------------------------------------------------

@router.post("/logout")
async def post_logout(
    hpc_session: str | None = Cookie(default=None),
    conn: sqlite3.Connection = Depends(get_db),
) -> JSONResponse:
    """Invalidate the current session.

    Clears hpc_session cookie regardless of whether session existed.

    Mirrors server.py:553-555:
        core.logout_session(self.conn(), self.session_token())
        send_json({"ok": True}, cookies=["hpc_session=; ...; Max-Age=0"])
    """
    if hpc_session:
        auth_service.logout(conn, hpc_session)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "hpc_session",
        "",
        httponly=True,
        samesite="strict",
        path="/",
        max_age=0,
    )
    return response
