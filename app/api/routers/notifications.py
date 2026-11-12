"""Notification settings (one DingTalk robot per kind) and poller status."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.deps import get_db, require_capability
from app.services import notification_channels
from app.services.notification_service import poller

router = APIRouter(prefix="/api/notifications", tags=["notifications"])
require_manage = require_capability("notification.manage", message="RM role required")


@router.get("/status")
def notification_status(user: dict = Depends(require_manage)) -> dict:
    return poller.status()


@router.get("/channels")
def list_channels(
    user: dict = Depends(require_manage),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"channels": notification_channels.list_settings(conn)}


@router.post("/channels/save")
def save_channel(
    body: dict,
    user: dict = Depends(require_manage),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Body: {kind, enabled, access_token?, secret?, at_user_ids}.

    Omit (or send null for) access_token / secret to keep the stored value.
    """
    channel = notification_channels.save_settings(
        conn,
        body["kind"],
        enabled=bool(body.get("enabled")),
        access_token=body.get("access_token"),
        secret=body.get("secret"),
        at_user_ids=list(body.get("at_user_ids") or []),
        user=user["username"],
        role=user["role"],
    )
    return {"channel": channel}


@router.post("/channels/test")
def test_channel(
    body: dict,
    user: dict = Depends(require_manage),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    notification_channels.send_test(conn, body["kind"], user=user["username"])
    return {"ok": True}
