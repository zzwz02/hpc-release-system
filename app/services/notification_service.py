"""Single-worker notification polling with durable success receipts and retries."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

from app import runtime_config
from app.config import settings
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_agent
from app.integrations import dingtalk
from app.timeutil import beijing_timestamp

logger = logging.getLogger(__name__)
# Background checks do not depend on an open browser. Keep upstream traffic low.
POLL_SECONDS = 60
# Daily aggregate thresholds requested by the team (strictly greater than).
THRESHOLDS = {"Code Owner": 20, "SPD": 30}
# Beijing-time hourly checks include both the 10:00 and 20:00 boundaries.
JENKINS_START_HOUR = 10
JENKINS_END_HOUR = 20


def jira_message(turn) -> str:
    prefix = f'{turn["issue_key"]} jira agent'
    if turn["status"] == "failed":
        return prefix + "失败"
    if turn["status"] == "interrupted":
        return prefix + "已中断"
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
        try:
            enabled = dingtalk.configuration() is not None
            config_error = ""
        except runtime_config.ConfigError as exc:
            enabled, config_error = False, str(exc)
        return {"enabled": enabled, "last_check": self.last_check,
                "errors": {**self.errors, **({"config": config_error} if config_error else {})}}

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

    async def _send_once(self, conn, key: str, message: str):
        if conn.execute("SELECT 1 FROM notification_receipts WHERE event_key=?", (key,)).fetchone():
            return
        if await dingtalk.send_text(message):
            with conn:
                conn.execute("INSERT INTO notification_receipts VALUES (?, ?)", (key, beijing_timestamp()))

    async def _jira(self, conn):
        # Persist activation time: no historical flood, but downtime is replayed.
        with conn:
            conn.execute("INSERT OR IGNORE INTO notification_receipts VALUES ('activation', ?)",
                         (beijing_timestamp(),))
        since = conn.execute("SELECT sent_at FROM notification_receipts WHERE event_key='activation'").fetchone()[0]
        turns = conn.execute("""
            SELECT t.*, c.issue_key FROM jira_agent_turns t
            JOIN jira_agent_conversations c ON c.id=t.conversation_id
            WHERE t.status IN ('completed', 'failed', 'interrupted') AND t.finished_at >= ?
            AND t.delivery_status != 'collecting'
            AND NOT EXISTS (SELECT 1 FROM notification_receipts n WHERE n.event_key='jira:' || t.id)
            ORDER BY t.finished_at
        """, (since,)).fetchall()
        for turn in turns:
            await self._send_once(conn, "jira:" + turn["id"], jira_message(turn))

    async def _jenkins(self, conn):
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
            await self._send_once(conn, f"jenkins:{today}",
                                  f"{today} Jenkins 当天错误：Code Owner {counts['Code Owner']} 个（阈值 20），"
                                  f"SPD / DevOps {counts['SPD']} 个（阈值 30）；已触发超限提醒")

    async def tick(self):
        conn = None
        try:
            if dingtalk.configuration() is None:
                self.errors.clear()
                self._next_jenkins = None
                return
            self.errors.pop("config", None)
            conn = connect_jira_agent(settings.jira_agent_database_url)
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS notification_receipts "
                             "(event_key TEXT PRIMARY KEY, sent_at TEXT NOT NULL)")
            jobs = [("jira", self._jira)]
            now = datetime.strptime(beijing_timestamp(), "%Y-%m-%d %H:%M:%S")
            hour = now.replace(minute=0, second=0)
            if self._next_jenkins is None:
                self._next_jenkins = hour if now == hour else hour + timedelta(hours=1)
            if now >= self._next_jenkins:
                if JENKINS_START_HOUR <= now.hour <= JENKINS_END_HOUR:
                    jobs.append(("jenkins", self._jenkins))
                self._next_jenkins = hour + timedelta(hours=1)
            for name, job in jobs:
                try:
                    await job(conn)
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


poller = NotificationPoller()
