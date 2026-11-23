"""Single-worker notification polling with durable success receipts and retries."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from app import runtime_config
from app.config import settings
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_agent
from app.integrations import dingtalk
from app.repositories import releases_repo, users_repo
from app.timeutil import beijing_timestamp

logger = logging.getLogger(__name__)
# Background checks do not depend on an open browser. Keep upstream traffic low.
POLL_SECONDS = 60
# Daily aggregate thresholds requested by the team (strictly greater than).
THRESHOLDS = {"Code Owner": 20, "SPD": 30}
# Beijing-time hourly checks include both the 10:00 and 20:00 boundaries.
JENKINS_START_HOUR = 10
JENKINS_END_HOUR = 20
# Each release milestone uses the same Beijing-time calendar-day schedule.
RELEASE_REMINDER_HOUR = 10
RELEASE_REMINDER_DAYS = (5, 1)
RELEASE_MILESTONES = {
    "signup_deadline": "报名截止",
    "qa_deadline": "QA 截止",
    "release_date": "发布日",
}
POLLED_KINDS = ("jira_agent_turn", "jenkins_failures", "release_deadlines")


def jira_message(turn, submitter_name: str = "") -> str:
    name = " ".join(submitter_name.split()) or turn["created_by"]
    prefix = f'{turn["issue_key"]}（交单：{name}）jira agent'
    if turn["status"] in ("failed", "interrupted"):
        status = "失败" if turn["status"] == "failed" else "已中断"
        return prefix + status + "：" + jira_agent.failure_reason_label(turn["error"])
    if turn["delivery_status"] == "incomplete":
        return prefix + " 分析已完成，交付不完整"
    result = jira_agent.parse_stored_result(turn["result_json"])
    conclusion, action = jira_agent.result_state(result)
    label = jira_agent.CONCLUSIONS[conclusion]
    extra = jira_agent.ACTIONS_REQUIRED[action]
    if extra:
        label += " · " + extra
    return prefix + " " + label


def jenkins_counts(payload: dict) -> dict[str, int]:
    if payload.get("group_by") != "owner_role" or not isinstance(payload.get("groups"), list):
        raise ValueError("Jenkins 汇总响应格式错误")
    counts = dict.fromkeys(THRESHOLDS, 0)
    for group in payload["groups"]:
        role = group["group"]
        if role == "DevOps":
            role = "SPD"
        count = group["failure_count"]
        if type(count) is not int or count < 0:
            raise ValueError("Jenkins 错误数无效")
        if role in counts:
            counts[role] += count
    return counts


class NotificationPoller:
    def __init__(self):
        self.task = None
        self.errors: dict[str, str] = {}
        self.last_check = ""
        self._next_jenkins = None

    def status(self):
        channels = _poller_channels()
        return {"enabled": any(channels.values()), "last_check": self.last_check,
                "errors": dict(self.errors)}

    async def start(self):
        self.errors.clear()
        self.last_check = ""
        self._next_jenkins = None
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _run(self):
        while True:
            await self.tick()
            now = datetime.strptime(beijing_timestamp(), "%Y-%m-%d %H:%M:%S")
            boundary = now.replace(minute=0, second=0) + timedelta(hours=1)
            await asyncio.sleep(min(POLL_SECONDS, (boundary - now).total_seconds()))

    async def _send_once(self, conn, channel: dict, key: str, message: str):
        if conn.execute("SELECT 1 FROM notification_receipts WHERE event_key=?", (key,)).fetchone():
            return
        await dingtalk.send_text(channel, message)
        with conn:
            conn.execute("INSERT INTO notification_receipts VALUES (?, ?)", (key, beijing_timestamp()))

    async def _jira(self, conn, channel):
        # Persist activation time: no historical flood, but downtime is replayed.
        with conn:
            conn.execute("INSERT OR IGNORE INTO notification_receipts VALUES ('activation', ?)",
                         (beijing_timestamp(),))
        since = conn.execute("SELECT sent_at FROM notification_receipts WHERE event_key='activation'").fetchone()[0]
        turns = conn.execute("""
            SELECT t.*, c.issue_key FROM jira_agent_turns t
            JOIN jira_agent_conversations c ON c.id=t.conversation_id
            WHERE c.source_kind='jira' AND t.status IN ('completed', 'failed', 'interrupted') AND t.finished_at >= ?
            AND t.delivery_status != 'collecting'
            AND NOT EXISTS (SELECT 1 FROM notification_receipts n WHERE n.event_key='jira:' || t.id)
            ORDER BY t.finished_at
        """, (since,)).fetchall()
        if not turns:
            return
        # Read only the names needed for these notices, without initializing the business DB.
        usernames = list({turn["created_by"] for turn in turns})
        with closing(sqlite3.connect(Path(settings.db_path).resolve().as_uri() + "?mode=ro", uri=True)) as users:
            users.row_factory = sqlite3.Row
            names = users_repo.display_names_for(users, usernames)
        for turn in turns:
            name = (names.get(turn["created_by"]) or "").strip()
            await self._send_once(conn, channel, "jira:" + turn["id"], jira_message(turn, name))

    async def _jenkins(self, conn, channel):
        if not runtime_config.section("cicd_agent"):
            return
        # Reuse the exact gateway, auth and query contract used by the website.
        from app.api.routers.cicd_agent import _request_agent_payload
        today = beijing_timestamp()[:10]
        status, payload = await asyncio.to_thread(
            _request_agent_payload, "GET", "/api/v1/failures/summary",
            query=urlencode({"date_from": today, "date_to": today, "group_by": "owner_role"}))
        if status != 200:
            raise RuntimeError(f"Jenkins 查询失败（HTTP {status}）")
        counts = jenkins_counts(payload)
        if any(counts[role] > limit for role, limit in THRESHOLDS.items()):
            await self._send_once(conn, channel, f"jenkins:{today}",
                                  f"{today} Jenkins 当天错误：Code Owner {counts['Code Owner']} 个（阈值 20），"
                                  f"SPD / DevOps {counts['SPD']} 个（阈值 30）；已触发超限提醒")

    async def _release_deadlines(self, conn, channel):
        from app.db.connection import connect

        now = datetime.fromisoformat(beijing_timestamp())
        if now.hour < RELEASE_REMINDER_HOUR:
            return
        days_until = {
            (now.date() + timedelta(days=days)).isoformat(): days for days in RELEASE_REMINDER_DAYS
        }
        with closing(connect(settings.db_path)) as main:
            releases = releases_repo.list_release_rows(main)

        failure = None
        for release in releases:
            if release["released_locked"]:
                continue
            for field, label in RELEASE_MILESTONES.items():
                # Deadlines store Beijing dates with an optional local time.
                target_date = release[field].strip()[:10]
                days = days_until.get(target_date)
                if days is None:
                    continue
                key = f"release_deadlines:{release['id']}:{field}:{target_date}:{days}"
                message = f"【版本截止日期提醒】{release['name']} 版本距离 {label}还有 {days} 天（{target_date}）"
                try:
                    await self._send_once(conn, channel, key, message)
                except dingtalk.DingTalkError as exc:
                    # Other milestones may still succeed. Failed sends have no receipt.
                    failure = exc
        if failure is not None:
            raise failure

    async def tick(self):
        conn = None
        try:
            channels = _poller_channels()
            if not any(channels.values()):
                self.errors.clear()
                self._next_jenkins = None
                return
            conn = connect_jira_agent(settings.jira_agent_database_url)
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS notification_receipts "
                             "(event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
            jobs = []
            if channels["release_deadlines"]:
                jobs.append(("release_deadlines", self._release_deadlines, channels["release_deadlines"]))
            if channels["jira_agent_turn"]:
                jobs.append(("jira", self._jira, channels["jira_agent_turn"]))
            now = datetime.strptime(beijing_timestamp(), "%Y-%m-%d %H:%M:%S")
            hour = now.replace(minute=0, second=0)
            if self._next_jenkins is None:
                self._next_jenkins = hour if now == hour else hour + timedelta(hours=1)
            if now >= self._next_jenkins:
                if JENKINS_START_HOUR <= now.hour <= JENKINS_END_HOUR and channels["jenkins_failures"]:
                    jobs.append(("jenkins", self._jenkins, channels["jenkins_failures"]))
                self._next_jenkins = hour + timedelta(hours=1)
            for name, job, channel in jobs:
                try:
                    await job(conn, channel)
                    self.errors.pop(name, None)
                except Exception as exc:
                    # Only our sanitized errors/config diagnostics are surfaced.
                    self.errors[name] = str(exc) if isinstance(exc, (dingtalk.DingTalkError, runtime_config.ConfigError)) else f"{name} 通知检查失败"
                    logger.warning("%s", self.errors[name])
            self.last_check = beijing_timestamp()
        except Exception as exc:
            self.errors["config"] = str(exc) if isinstance(exc, runtime_config.ConfigError) else "通知服务初始化失败"
            logger.warning("%s", self.errors["config"])
        finally:
            if conn is not None:
                conn.close()


def _poller_channels() -> dict[str, dict | None]:
    """Enabled robots for the polled kinds, read from the main DB."""
    from app.db.connection import connect
    from app.services import notification_channels

    main = connect(settings.db_path)
    try:
        return {
            kind: notification_channels.enabled_channel(main, kind)
            for kind in POLLED_KINDS
        }
    finally:
        main.close()


poller = NotificationPoller()
