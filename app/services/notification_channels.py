"""Per-kind DingTalk robots: settings for RM and best-effort sending.

Each notification kind (``shared/domain_metadata.json`` → notification_kinds)
has its own robot.  A kind that is not configured or not enabled sends
nothing.  Secrets are never returned to the browser; the settings page only
sees masks.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Any

from app.db.connection import transaction
from app.domain.shared_metadata import mapping_section
from app.integrations import dingtalk
from app.repositories import notification_channels_repo as channels_repo
from app.repositories.audit_repo import log_audit
from app.timeutil import beijing_timestamp

logger = logging.getLogger(__name__)

_KIND_METADATA = mapping_section("notification_kinds")
KINDS: tuple[str, ...] = tuple(
    kind for kind, _ in sorted(_KIND_METADATA.items(), key=lambda item: int(item[1]["order"]))
)


def _require_kind(kind: str) -> None:
    if kind not in _KIND_METADATA:
        raise ValueError(f"未知的通知类型：{kind}")


def _mask(value: str) -> str:
    value = value or ""
    if not value:
        return ""
    return ("*" * max(0, len(value) - 4))[:12] + value[-4:]


def list_settings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    channels = channels_repo.list_channels(conn)
    rows = []
    for kind in KINDS:
        channel = channels.get(kind) or {}
        rows.append(
            {
                "kind": kind,
                "label": _KIND_METADATA[kind]["label"],
                "enabled": bool(channel.get("enabled")),
                "configured": bool(channel.get("access_token") and channel.get("secret")),
                "access_token_mask": _mask(channel.get("access_token", "")),
                "secret_mask": _mask(channel.get("secret", "")),
                "at_user_ids": list(channel.get("at_user_ids") or []),
                "updated_at": channel.get("updated_at", ""),
                "updated_by": channel.get("updated_by", ""),
            }
        )
    return rows


def save_settings(
    conn: sqlite3.Connection,
    kind: str,
    *,
    enabled: bool,
    access_token: str | None,
    secret: str | None,
    at_user_ids: list[str],
    user: str,
    role: str,
) -> dict[str, Any]:
    """Save one kind's robot.  ``None`` token/secret keeps the stored value."""
    _require_kind(kind)
    current = channels_repo.get_channel(conn, kind) or {}
    token = current.get("access_token", "") if access_token is None else access_token.strip()
    key = current.get("secret", "") if secret is None else secret.strip()
    users = [str(item).strip() for item in at_user_ids if str(item).strip()]
    if enabled and (not token or not key):
        raise ValueError("启用前必须填写 ACCESS_TOKEN 和 SECRET")
    ts = beijing_timestamp()
    with transaction(conn):
        channels_repo.save_channel(
            conn,
            kind,
            enabled=enabled,
            access_token=token,
            secret=key,
            at_user_ids=users,
            updated_at=ts,
            updated_by=user,
        )
        log_audit(
            conn,
            f"更新通知设置：{_KIND_METADATA[kind]['label']}（{'启用' if enabled else '停用'}）",
            ts=ts,
            user=user,
            role=role,
            event="notification_settings",
            detail={"kind": kind, "enabled": enabled, "at_user_ids": users,
                    "token_changed": access_token is not None, "secret_changed": secret is not None},
        )
    return next(row for row in list_settings(conn) if row["kind"] == kind)


def send_test(conn: sqlite3.Connection, kind: str, *, user: str) -> None:
    _require_kind(kind)
    channel = channels_repo.get_channel(conn, kind)
    if not channel or not channel.get("access_token"):
        raise ValueError("该通知类型尚未配置机器人")
    dingtalk.send_text_sync(
        channel,
        f"【测试】HPC 发布系统通知：{_KIND_METADATA[kind]['label']}（由 {user} 发送）",
    )


def enabled_channel(conn: sqlite3.Connection, kind: str) -> dict[str, Any] | None:
    _require_kind(kind)
    channel = channels_repo.get_channel(conn, kind)
    if not channel or not channel.get("enabled") or not channel.get("access_token"):
        return None
    return channel


def notify(conn: sqlite3.Connection, kind: str, message: str) -> None:
    """Send *message* in a background thread if *kind* is enabled.

    Call after the triggering change is committed.  Failures are logged and
    never affect the caller.
    """
    channel = enabled_channel(conn, kind)
    if channel is None:
        return

    def _send() -> None:
        try:
            dingtalk.send_text_sync(channel, message)
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("通知发送失败（%s）：%s", kind, exc)

    threading.Thread(target=_send, name=f"notify-{kind}", daemon=True).start()
