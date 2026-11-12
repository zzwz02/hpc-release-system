"""Signup entries and QA scope requests.

  POST /api/entries/signup          — Owner/RM signs an App up for a cycle
  POST /api/entries/update          — plan, scope, actual delivery, known issues
  POST /api/entries/withdraw
  POST /api/scope-requests/decide   — QA approves or rejects a wider scope
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.deps import get_db, require_login
from app.services import cycle_service

router = APIRouter(tags=["entries"])


@router.post("/api/entries/signup")
def post_signup(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    entry = cycle_service.signup(
        conn,
        body["release_id"],
        body["app_id"],
        base_image_id=body.get("base_image_id"),
        target_version=body.get("target_version", ""),
        planned_features=body.get("planned_features", ""),
        excluded_tests=body.get("excluded_tests") or [],
        user=user["username"],
        role=user["role"],
    )
    return {"entry": entry}


@router.post("/api/entries/update")
def post_entry_update(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    entry = cycle_service.update_entry(
        conn, int(body["entry_id"]), body.get("changes") or {}, user=user["username"], role=user["role"]
    )
    return {"entry": entry}


@router.post("/api/entries/withdraw")
def post_entry_withdraw(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"entry": cycle_service.withdraw(conn, int(body["entry_id"]), user=user["username"], role=user["role"])}


@router.post("/api/scope-requests/decide")
def post_scope_decide(
    body: dict,
    user: dict = Depends(require_login),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    request = cycle_service.decide_scope_request(
        conn,
        int(body["request_id"]),
        approve=bool(body.get("approve")),
        note=body.get("note", ""),
        user=user["username"],
        role=user["role"],
    )
    return {"request": request}
