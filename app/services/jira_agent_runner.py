"""Queue scheduling and Codex turn execution for JIRA agent conversations.

Runs inside the website process (single uvicorn worker).  The database queue
is authoritative; this module keeps the live app-server connections of the
turns currently running so that steer / interrupt can reach them.

Per group (one app-server host) at most ``MAX_CONCURRENT`` turns run at once;
queued turns start in FIFO order.  Machine-level resource locks (GPUs on
C/D/E) are a convention of the group's knowledge pack on the host, not here.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import posixpath
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent
from app.domain import jira_agent as domain
from app.integrations import jira
from app.integrations.codex_app_server import CodexAppServerClient, CodexAppServerError
from app.repositories import jira_agent_repo as repo
from app.repositories.base import dumps_json
from app.timeutil import BEIJING_TZ, beijing_now, beijing_timestamp

logger = logging.getLogger(__name__)

_OUTPUT_LIMIT = 20_000
_ARTIFACT_MAX_BYTES = 10 * 1024 * 1024
_INTERRUPT_GRACE_SECONDS = 60
_SCAN_SECONDS = 5.0

_STOP_TEXT = {
    "user": "已由用户取消",
    "closed": "对话已关闭，本轮已中断",
}


def open_db():
    return connect_jira_agent(settings.jira_agent_database_url)


def load_groups() -> dict[str, domain.AgentGroup]:
    return domain.load_groups(settings.jira_agent_conf_path)


def conversation_url(conversation_id: str) -> str:
    base = settings.jira_agent_public_base_url.rstrip("/")
    return f"{base}/jira-agent?conversation={conversation_id}" if base else ""


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n…（已截断，共 {len(text)} 字符）"


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        return f"HTTP {exc.code} {exc.reason} {body}".strip()
    return str(exc) or type(exc).__name__


def _parse_timestamp(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _deadline(turn: dict, group: domain.AgentGroup) -> float:
    """Monotonic deadline of a turn; the limit counts from dequeue, also across restarts."""
    started = _parse_timestamp(turn["started_at"])
    elapsed = max(0.0, (beijing_now() - started).total_seconds()) if started else 0.0
    return time.monotonic() + group.turn_timeout_seconds - elapsed


def _event(conn, conversation_id: str, turn_id: str, kind: str, payload: dict, item_id: str = "") -> None:
    with transaction(conn):
        repo.add_event(
            conn,
            conversation_id=conversation_id,
            turn_id=turn_id,
            kind=kind,
            payload=payload,
            item_id=item_id,
        )


@dataclass
class ActiveTurn:
    turn_id: str
    conversation_id: str
    group: str
    workspace: str
    # preparing → starting → running → finishing; after a restart: recovering → running → finishing
    phase: str = "preparing"
    client: CodexAppServerClient | None = None
    thread_id: str = ""
    codex_turn_id: str = ""
    stop_reason: str = ""  # "", "user", "closed", "timeout"
    task: asyncio.Task | None = None
    # machines this turn may use, and the one its commands show it on
    machine_targets: tuple[str, ...] = ()
    machine_used: str = ""


async def post_turn_comment(conn, turn_id: str) -> dict:
    """Publish (or re-publish) a turn's rendered comment to JIRA."""
    turn = repo.get_turn(conn, turn_id)
    conversation = repo.get_conversation(conn, turn["conversation_id"])
    try:
        comment_id = await asyncio.to_thread(
            jira.add_comment, conversation["issue_key"], turn["comment_body"]
        )
    except Exception as exc:
        error = _error_text(exc)
        logger.warning("JIRA comment for %s failed: %s", conversation["issue_key"], error)
        with transaction(conn):
            repo.update_turn(conn, turn_id, comment_status="failed", comment_error=error)
            repo.add_event(
                conn, conversation_id=conversation["id"], turn_id=turn_id,
                kind="jira_comment", payload={"status": "failed", "error": error},
            )
    else:
        with transaction(conn):
            repo.update_turn(
                conn, turn_id, comment_status="posted", comment_id=comment_id, comment_error=""
            )
            repo.add_event(
                conn, conversation_id=conversation["id"], turn_id=turn_id,
                kind="jira_comment", payload={"status": "posted", "comment_id": comment_id},
            )
    return repo.get_turn(conn, turn_id)  # type: ignore[return-value]


class JiraAgentRunner:
    def __init__(self) -> None:
        self._active: dict[str, ActiveTurn] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wakeup: asyncio.Event | None = None
        self._loop_task: asyncio.Task | None = None
        self._background: set[asyncio.Task] = set()

    # ── lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wakeup = asyncio.Event()
        groups = load_groups()
        conn = open_db()
        try:
            for turn in repo.running_turns(conn):
                self._recover(conn, turn, groups)
            pending_comments = [turn["id"] for turn in repo.pending_comment_turns(conn)]
        finally:
            conn.close()
        for turn_id in pending_comments:
            self._spawn(self._post_pending_comment(turn_id))
        self._loop_task = asyncio.create_task(self._schedule_loop())

    def _recover(self, conn, turn: dict, groups: dict[str, domain.AgentGroup]) -> None:
        """Pick up a turn a previous process left running (see _reattach)."""
        conversation = repo.get_conversation(conn, turn["conversation_id"])
        cid, tid = conversation["id"], turn["id"]
        group = groups.get(conversation["agent_group"])
        closed = conversation["status"] != "open"
        if group is None or (closed and not conversation["thread_id"]):
            if group is None:
                status = "interrupted"
                error = f"网站重启后找不到数字员工组 {conversation['agent_group']}（jira_agent.conf）"
            else:  # closed before any turn could reach the app-server
                status, error = "cancelled", _STOP_TEXT["closed"]
            with transaction(conn):
                repo.update_turn(
                    conn, tid, status=status, error=error, finished_at=beijing_timestamp()
                )
                repo.add_event(
                    conn, conversation_id=cid, turn_id=tid,
                    kind="status", payload={"status": status, "text": error},
                )
            return
        if not conversation["thread_id"]:
            # No thread yet, so no turn can have reached the app-server.
            text = "网站重启，本轮尚未开始，已重新排队"
            with transaction(conn):
                if repo.requeue_turn(conn, tid):
                    repo.add_event(
                        conn, conversation_id=cid, turn_id=tid,
                        kind="status", payload={"status": "queued", "text": text},
                    )
            return
        active = ActiveTurn(
            turn_id=tid, conversation_id=cid, group=group.name,
            workspace=conversation["workspace"], phase="recovering",
        )
        if closed:
            active.stop_reason = "closed"
        self._active[tid] = active
        active.task = asyncio.create_task(self._run_turn(active, group, recover=True))

    async def _post_pending_comment(self, turn_id: str) -> None:
        """A previous process stopped between finishing a turn and commenting."""
        conn = open_db()
        try:
            await post_turn_comment(conn, turn_id)
        finally:
            conn.close()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def stop(self) -> None:
        tasks = [t for t in (self._loop_task, *(a.task for a in self._active.values())) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *self._background, return_exceptions=True)
        self._loop_task = None
        self._loop = None

    def wake(self) -> None:
        if self._loop is None or self._wakeup is None:
            return
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is self._loop:
            self._wakeup.set()
        else:
            self._loop.call_soon_threadsafe(self._wakeup.set)

    # ── scheduling ────────────────────────────────────────────

    async def _schedule_loop(self) -> None:
        assert self._wakeup is not None
        while True:
            self._wakeup.clear()
            try:
                self._schedule_once()
            except Exception:
                logger.exception("JIRA agent scheduling failed")
            try:
                await asyncio.wait_for(self._wakeup.wait(), _SCAN_SECONDS)
            except asyncio.TimeoutError:
                pass

    def running_count(self, group: str) -> int:
        return sum(1 for active in self._active.values() if active.group == group)

    def _schedule_once(self) -> None:
        groups = load_groups()
        conn = open_db()
        try:
            for group_name in repo.queued_groups(conn):
                group = groups.get(group_name)
                if group is None:
                    for turn in repo.queued_turns(conn, group_name):
                        error = f"未配置数字员工组 {group_name}（jira_agent.conf）"
                        with transaction(conn):
                            if repo.cancel_queued_turn(conn, turn["id"], error):
                                repo.update_turn(conn, turn["id"], status="failed")
                                repo.add_event(
                                    conn, conversation_id=turn["conversation_id"], turn_id=turn["id"],
                                    kind="error", payload={"status": "failed", "text": error},
                                )
                    continue
                running = self.running_count(group_name)
                for turn in repo.queued_turns(conn, group_name):
                    if running >= group.max_concurrent:
                        break
                    conversation = repo.get_conversation(conn, turn["conversation_id"])
                    if conversation["status"] != "open":  # closing cancels queued turns; stay safe
                        with transaction(conn):
                            if repo.cancel_queued_turn(conn, turn["id"], _STOP_TEXT["closed"]):
                                repo.add_event(
                                    conn, conversation_id=conversation["id"], turn_id=turn["id"],
                                    kind="status",
                                    payload={"status": "cancelled", "text": _STOP_TEXT["closed"]},
                                )
                        continue
                    with transaction(conn):
                        claimed = repo.claim_turn(conn, turn["id"])
                    if not claimed:
                        continue
                    active = ActiveTurn(
                        turn_id=turn["id"],
                        conversation_id=turn["conversation_id"],
                        group=group_name,
                        workspace=conversation["workspace"],
                    )
                    self._active[turn["id"]] = active
                    active.task = asyncio.create_task(self._run_turn(active, group))
                    running += 1
        finally:
            conn.close()

    # ── control from the service layer ───────────────────────

    def active_for_conversation(self, conversation_id: str) -> ActiveTurn | None:
        for active in self._active.values():
            if active.conversation_id == conversation_id:
                return active
        return None

    async def steer(self, active: ActiveTurn, text: str) -> None:
        if active.phase != "running" or active.client is None or active.stop_reason:
            raise CodexAppServerError("本轮不在可接收补充信息的状态")
        await active.client.steer_turn(active.thread_id, active.codex_turn_id, text)

    async def upload_to_active(self, active: ActiveTurn, remote_path: str, data: bytes) -> None:
        if active.client is None:
            raise CodexAppServerError("本轮尚未连接 Codex app-server")
        await active.client.write_file(remote_path, data)

    async def stop_turn(self, turn_id: str, reason: str) -> bool:
        """Interrupt a running turn; False if this process is not running it."""
        active = self._active.get(turn_id)
        if active is None:
            return False
        if active.stop_reason:
            return True
        active.stop_reason = reason
        if active.phase == "running" and active.client is not None:
            await self._interrupt(active)
        elif active.phase == "preparing" and active.task is not None:
            # Nothing is running on the app-server yet; cancelling is safe.
            active.task.cancel()
        # "starting" / "recovering": a turn may already be running on the
        # app-server, so only mark the stop; _run_turn interrupts as soon as
        # the turn id is known.  "finishing": the Codex turn is already over.
        return True

    async def _interrupt(self, active: ActiveTurn) -> None:
        try:
            await active.client.interrupt_turn(active.thread_id, active.codex_turn_id)  # type: ignore[union-attr]
        except CodexAppServerError as exc:
            logger.warning("interrupt of %s failed: %s", active.turn_id, exc)

    def schedule_archive(self, conversation_id: str) -> None:
        if self._loop is None:
            return
        self._spawn(self._archive_thread(conversation_id))

    async def _archive_thread(self, conversation_id: str) -> None:
        """Best effort: archive a closed conversation's thread on the host."""
        active = self.active_for_conversation(conversation_id)
        if active is not None and active.task is not None:
            await asyncio.wait([active.task], timeout=_INTERRUPT_GRACE_SECONDS + 30)
        conn = open_db()
        try:
            conversation = repo.get_conversation(conn, conversation_id)
            if not conversation or not conversation["thread_id"] or conversation["thread_archived"]:
                return
            group = load_groups().get(conversation["agent_group"])
            if group is None:
                return
            try:
                async with CodexAppServerClient(group.ws_url, group.ws_token) as client:
                    await client.archive_thread(conversation["thread_id"])
            except CodexAppServerError as exc:
                logger.warning("archive thread of %s failed: %s", conversation_id, exc)
                return
            with transaction(conn):
                repo.mark_thread_archived(conn, conversation_id)
        finally:
            conn.close()

    # ── one turn ──────────────────────────────────────────────

    async def _run_turn(
        self, active: ActiveTurn, group: domain.AgentGroup, *, recover: bool = False
    ) -> None:
        conn = open_db()
        final_status, error = "failed", ""
        cancelled = False
        cid, tid = active.conversation_id, active.turn_id
        try:
            conversation = repo.get_conversation(conn, cid)
            active.machine_targets = (
                (conversation["machine"],) if conversation["machine"]
                else tuple(m["ssh_target"] for m in repo.list_machines(conn, group.name))
            )
            active.machine_used = conversation["machine_used"]
            deadline = _deadline(repo.get_turn(conn, tid), group)
            if recover:
                text, run = "网站已重启，正在重新连接 Codex 查看本轮", self._reattach
            else:
                text, run = f"开始执行（{group.display_name}）", self._start
            _event(conn, cid, tid, "status", {"status": "running", "text": text})
            active.client = CodexAppServerClient(group.ws_url, group.ws_token)
            await active.client.connect()
            status, turn_error, final_text = await run(conn, active, group, conversation, deadline)
            turn = repo.get_turn(conn, tid)
            active.phase = "finishing"
            if status == "requeue" and active.stop_reason:
                # Never started, but cancelled or closed meanwhile: do not run it.
                final_status, error = "cancelled", _STOP_TEXT.get(active.stop_reason, "已中断")
            elif status == "requeue":
                final_status, error = "queued", turn_error
            elif status == "completed":
                result = domain.parse_result(final_text)
                await self._finish_completed(conn, active, group, conversation, turn, result)
                final_status = "completed"
            elif status == "interrupted":
                if active.stop_reason == "timeout":
                    final_status, error = "failed", f"超过 {group.turn_timeout_seconds} 秒限时，已中断"
                elif active.stop_reason:
                    final_status, error = "cancelled", _STOP_TEXT.get(active.stop_reason, "已中断")
                elif recover:
                    final_status = "interrupted"
                    error = (
                        "网站重启期间 Codex 本轮已中断（app-server 可能重启过）；"
                        "发送消息可继续处理"
                    )
                else:
                    final_status, error = "interrupted", "Codex 本轮被中断"
            else:
                error = turn_error or "Codex 本轮执行失败"
        except asyncio.CancelledError:
            cancelled = True
            if active.stop_reason:
                final_status, error = "cancelled", _STOP_TEXT.get(active.stop_reason, "已中断")
            elif active.phase == "preparing":
                final_status, error = "queued", "网站停止，本轮尚未开始，重启后重新排队"
            else:
                # The turn keeps running on the app-server without this
                # connection; the next start re-attaches to it.
                final_status, error = "running", "网站停止；本轮仍在 Codex 上运行，重启后重新接管"
        except Exception as exc:
            logger.exception("JIRA agent turn %s failed", tid)
            error = _error_text(exc)
            if active.stop_reason in _STOP_TEXT:
                final_status = "cancelled"
                error = f"{_STOP_TEXT[active.stop_reason]}（{error}）"
            elif active.phase == "recovering":
                final_status = "interrupted"
                error = f"网站重启后未能重新接管本轮（{error}）；发送消息可继续处理"
        finally:
            if active.client is not None:
                await active.client.close()
            try:
                if final_status == "queued":
                    with transaction(conn):
                        if repo.requeue_turn(conn, tid):
                            repo.add_event(conn, conversation_id=cid, turn_id=tid, kind="status",
                                           payload={"status": "queued", "text": error})
                elif final_status == "running":
                    if repo.get_turn(conn, tid)["status"] == "running":
                        _event(conn, cid, tid, "status", {"status": "running", "text": error})
                elif final_status != "completed":
                    with transaction(conn):
                        repo.update_turn(
                            conn, tid, status=final_status, error=error, finished_at=beijing_timestamp()
                        )
                        repo.add_event(
                            conn, conversation_id=cid, turn_id=tid,
                            kind="error" if final_status == "failed" else "status",
                            payload={"status": final_status, "text": error},
                        )
            finally:
                conn.close()
                self._active.pop(tid, None)
                self.wake()
        if cancelled:
            raise asyncio.CancelledError

    async def _start(
        self, conn, active: ActiveTurn, group: domain.AgentGroup, conversation: dict,
        deadline: float,
    ) -> tuple[str, str, str]:
        """Sync inputs, start or resume the thread, start the turn and consume it."""
        cid, tid = active.conversation_id, active.turn_id
        issue = await asyncio.to_thread(jira.get_issue, conversation["issue_key"])
        uploaded = await self._sync_inputs(conn, active, conversation, issue)

        if conversation["thread_id"]:
            active.thread_id = conversation["thread_id"]
            thread = await active.client.resume_thread(active.thread_id, model=group.model)  # type: ignore[union-attr]
            await self._stop_leftover_turn(conn, active, thread)
        else:
            thread = await active.client.start_thread(  # type: ignore[union-attr]
                cwd=conversation["workspace"],
                developer_instructions=domain.developer_instructions(
                    group, conversation["issue_key"], conversation["workspace"]
                ),
                model=group.model,
                service_name="release-system-jira-agent",
            )
            active.thread_id = thread["id"]
            with transaction(conn):
                repo.set_thread_id(conn, cid, thread["id"])

        active.phase = "starting"
        turn = repo.get_turn(conn, tid)  # re-read: messages may have been merged
        machine = conversation["machine"]
        prompt = domain.build_turn_prompt(
            trigger=turn["trigger"],
            seq=turn["seq"],
            issue=issue,
            owner=conversation["owner"],
            created_by=turn["created_by"],
            input_text=turn["input_text"],
            uploaded=uploaded,
            machine_text=domain.machine_instructions(
                machine, [] if machine else repo.list_machines(conn, group.name)
            ),
        )
        codex_turn = await active.client.start_turn(  # type: ignore[union-attr]
            active.thread_id, prompt, output_schema=domain.RESULT_SCHEMA, model=group.model
        )
        active.codex_turn_id = codex_turn["id"]
        active.phase = "running"
        with transaction(conn):
            repo.update_turn(conn, tid, codex_turn_id=codex_turn["id"])
        if active.stop_reason:
            await self._interrupt(active)
        return await self._consume(conn, active, group, deadline)

    async def _reattach(
        self, conn, active: ActiveTurn, group: domain.AgentGroup, conversation: dict,
        deadline: float,
    ) -> tuple[str, str, str]:
        """Resume the thread of a turn a previous process left running.

        Verified against codex app-server 0.153: a turn keeps running when its
        client disconnects, and a connection that resumes the thread receives
        the rest of its notifications.  If the app-server itself restarted, the
        turn is reported as interrupted.  Returns (status, error, final text);
        status "requeue" means the turn never reached the app-server.
        """
        cid, tid = active.conversation_id, active.turn_id
        turn = repo.get_turn(conn, tid)
        active.thread_id = conversation["thread_id"]
        thread = await active.client.resume_thread(active.thread_id, model=group.model)  # type: ignore[union-attr]
        latest = await active.client.list_turns(active.thread_id)  # type: ignore[union-attr]
        target = latest[0] if latest and self._is_this_turn(latest[0], turn) else None
        if target is None:
            return "requeue", "网站重启，本轮尚未在 Codex 上开始，已重新排队", ""
        active.codex_turn_id = target["id"]
        if turn["codex_turn_id"] != target["id"]:
            with transaction(conn):
                repo.update_turn(conn, tid, codex_turn_id=target["id"])

        final_text = ""
        for item in target.get("items") or []:  # backfill what happened while the site was down
            text = self._record_item(conn, active, item, True)
            if text is not None:
                final_text = text
        status = target.get("status")
        thread_active = (thread.get("status") or {}).get("type") == "active"
        if status != "inProgress" or not thread_active:
            text = "本轮已在网站重启期间结束，正在收尾"
            _event(conn, cid, tid, "status", {"status": "running", "text": text})
            if status not in ("completed", "failed", "interrupted"):
                status = "interrupted"  # left inProgress by an app-server that restarted
            return status, ((target.get("error") or {}).get("message") or ""), final_text

        active.phase = "running"
        text = "已重新接管 Codex 上仍在运行的本轮"
        _event(conn, cid, tid, "status", {"status": "running", "text": text})
        if active.stop_reason:
            await self._interrupt(active)
        return await self._consume(conn, active, group, deadline, final_text)

    @staticmethod
    def _is_this_turn(codex_turn: dict, turn: dict) -> bool:
        if turn["codex_turn_id"]:
            return codex_turn.get("id") == turn["codex_turn_id"]
        # Stopped while turn/start was in flight: the turn is ours if the
        # app-server started it after this turn left the queue.
        claimed = _parse_timestamp(turn["started_at"])
        started_at = codex_turn.get("startedAt")
        if not (claimed and started_at):
            return False
        return started_at >= claimed.replace(tzinfo=BEIJING_TZ).timestamp()

    async def _stop_leftover_turn(self, conn, active: ActiveTurn, thread: dict) -> None:
        """Interrupt a turn still running on the thread from an abandoned run.

        turn/start on an active thread does not start a new turn: the
        app-server merges the input into the running one.
        """
        if (thread.get("status") or {}).get("type") != "active":
            return
        client, cid, tid = active.client, active.conversation_id, active.turn_id
        turns = await client.list_turns(active.thread_id, items_view="notLoaded")  # type: ignore[union-attr]
        leftover = next((t for t in turns if t.get("status") == "inProgress"), None)
        if leftover is None:
            return
        text = "Codex 上本对话还有未结束的上一轮，先中断它"
        _event(conn, cid, tid, "status", {"status": "running", "text": text})
        try:
            await client.interrupt_turn(active.thread_id, leftover["id"])  # type: ignore[union-attr]
        except CodexAppServerError as exc:  # it may have just finished
            logger.warning("interrupt of leftover turn %s failed: %s", leftover["id"], exc)
        deadline = time.monotonic() + _INTERRUPT_GRACE_SECONDS
        while time.monotonic() < deadline:
            message = await client.next_event(timeout=_SCAN_SECONDS)  # type: ignore[union-attr]
            if message and message.get("method") == "turn/completed":
                if ((message.get("params") or {}).get("turn") or {}).get("id") == leftover["id"]:
                    return
        raise CodexAppServerError("Codex 上的上一轮未能在限时内中断，请稍后再发送")

    async def _sync_inputs(self, conn, active: ActiveTurn, conversation: dict, issue: dict) -> list[str]:
        """Write issue.md, new JIRA attachments and pending uploads to the workspace."""
        client, cid, tid = active.client, conversation["id"], active.turn_id
        workspace = conversation["workspace"]
        for sub in ("attachments", "uploads", "work", "artifacts"):
            await client.create_directory(f"{workspace}/{sub}")  # type: ignore[union-attr]

        attachment_paths = repo.jira_attachment_paths(conn, cid)
        used = repo.remote_paths(conn, cid)
        for att in issue.get("attachments") or []:
            if att["id"] in attachment_paths:
                continue
            rel = self._unique_rel("attachments", domain.safe_filename(att["filename"]), used, att["id"])
            try:
                data = await asyncio.to_thread(jira.download_attachment, att["content_url"])
            except Exception as exc:
                attachment_paths[att["id"]] = f"下载失败：{_error_text(exc)}"
                _event(conn, cid, tid, "error", {"text": f"附件 {att['filename']} 下载失败：{_error_text(exc)}"})
                continue
            await client.write_file(f"{workspace}/{rel}", data)  # type: ignore[union-attr]
            with transaction(conn):
                repo.add_file(
                    conn, conversation_id=cid, turn_id=tid, direction="input", source="jira",
                    source_ref=att["id"], name=att["filename"], size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(), remote_path=rel,
                )
            attachment_paths[att["id"]] = rel
        await client.write_file(  # type: ignore[union-attr]
            f"{workspace}/issue.md",
            domain.render_issue_markdown(issue, attachment_paths).encode("utf-8"),
        )

        uploaded: list[str] = []
        for record in repo.pending_uploads(conn, cid):
            data = Path(record["local_path"]).read_bytes()
            rel = self._unique_rel("uploads", domain.safe_filename(record["name"]), used, record["id"])
            await client.write_file(f"{workspace}/{rel}", data)  # type: ignore[union-attr]
            with transaction(conn):
                repo.set_file_remote_path(conn, record["id"], rel)
            uploaded.append(rel)
        _event(conn, cid, tid, "status", {
            "status": "running",
            "text": f"已同步工单快照、{len(attachment_paths)} 个附件、{len(uploaded)} 个上传文件到 {workspace}",
        })
        return uploaded

    @staticmethod
    def _unique_rel(folder: str, name: str, used: set[str], salt: str) -> str:
        rel = f"{folder}/{name}"
        if rel in used:
            rel = f"{folder}/{salt}-{name}"
        used.add(rel)
        return rel

    async def _consume(
        self, conn, active: ActiveTurn, group: domain.AgentGroup, deadline: float,
        final_text: str = "",
    ) -> tuple[str, str, str]:
        """Record notifications until turn/completed; returns (status, error, final text)."""
        grace_deadline: float | None = None
        cid, tid = active.conversation_id, active.turn_id
        while True:
            now = time.monotonic()
            if not active.stop_reason and now >= deadline:
                active.stop_reason = "timeout"
                _event(conn, cid, tid, "status", {"status": "running", "text": "已到本轮限时，正在中断"})
                await self._interrupt(active)
            if active.stop_reason and grace_deadline is None:
                grace_deadline = now + _INTERRUPT_GRACE_SECONDS
            if grace_deadline is not None and now >= grace_deadline:
                return "interrupted", "", final_text

            message = await active.client.next_event(timeout=_SCAN_SECONDS)  # type: ignore[union-attr]
            if message is None:
                continue
            method = message.get("method", "")
            params = message.get("params") or {}
            if method in ("item/started", "item/completed"):
                if params.get("turnId") and params["turnId"] != active.codex_turn_id:
                    continue
                text = self._record_item(conn, active, params.get("item") or {}, method == "item/completed")
                if text is not None:
                    final_text = text
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("id") != active.codex_turn_id:
                    continue
                return turn.get("status") or "failed", ((turn.get("error") or {}).get("message") or ""), final_text
            elif method == "error" and not params.get("willRetry"):
                _event(conn, cid, tid, "error", {"text": (params.get("error") or {}).get("message", "Codex 错误")})
            elif method == "client/serverRequestRejected":
                _event(conn, cid, tid, "error", {
                    "text": f"app-server 请求了网站不处理的交互 {params.get('method')}，请检查 B 的 approval_policy",
                })

    def _record_item(self, conn, active: ActiveTurn, item: dict, completed: bool) -> str | None:
        """Store one thread item as a timeline event; returns agent message text."""
        cid, tid = active.conversation_id, active.turn_id
        kind, item_id = item.get("type", ""), item.get("id", "")
        if kind == "agentMessage":
            if not completed:
                return None
            text = item.get("text") or ""
            try:
                domain.parse_result(text)
            except ValueError:
                _event(conn, cid, tid, "agent_message", {"text": text, "phase": item.get("phase") or ""}, item_id)
            return text
        if kind == "commandExecution":
            _event(conn, cid, tid, "command", {
                "command": item.get("command") or "",
                "cwd": item.get("cwd") if isinstance(item.get("cwd"), str) else "",
                "status": item.get("status") or "",
                "exit_code": item.get("exitCode"),
                "duration_ms": item.get("durationMs"),
                "output": _clip(item.get("aggregatedOutput") or "", _OUTPUT_LIMIT),
            }, item_id)
            self._note_machine(conn, active, domain.detect_machine(
                item.get("command") or "", active.machine_targets,
            ))
        elif kind == "fileChange" and completed:
            _event(conn, cid, tid, "file_change", {
                "status": item.get("status") or "",
                "changes": [
                    {
                        "path": change.get("path") or "",
                        "kind": (change.get("kind") or {}).get("type", ""),
                        "diff": _clip(change.get("diff") or "", _OUTPUT_LIMIT),
                    }
                    for change in item.get("changes") or []
                ],
            }, item_id)
        elif kind == "reasoning" and completed:
            parts = [
                part if isinstance(part, str) else str(part.get("text", ""))
                for part in item.get("summary") or []
            ]
            summary = "\n".join(p for p in parts if p).strip()
            if summary:
                _event(conn, cid, tid, "reasoning", {"text": summary}, item_id)
        elif completed and kind not in ("userMessage", "hookPrompt", "contextCompaction"):
            detail = {k: v for k, v in item.items() if k not in ("id", "type")}
            _event(conn, cid, tid, "tool", {
                "type": kind,
                "detail": _clip(json.dumps(detail, ensure_ascii=False), 2000),
            }, item_id)
        return None

    @staticmethod
    def _note_machine(conn, active: ActiveTurn, machine: str) -> None:
        """Show the machine on the conversation while the turn is still running."""
        if not machine or machine == active.machine_used:
            return
        active.machine_used = machine
        with transaction(conn):
            repo.set_machine_used(conn, active.conversation_id, machine)

    async def _finish_completed(
        self, conn, active: ActiveTurn, group: domain.AgentGroup,
        conversation: dict, turn: dict, result: dict,
    ) -> None:
        artifacts = await self._pull_artifacts(conn, active, conversation, result)
        patches = [
            (record["name"], Path(record["local_path"]).read_text("utf-8", errors="replace"))
            for record in artifacts
            if record["name"].endswith((".patch", ".diff"))
        ]
        body = domain.render_jira_comment(
            group=group,
            result=result,
            turn_seq=turn["seq"],
            owner=conversation["owner"],
            conversation_url=conversation_url(conversation["id"]),
            patches=patches,
        )
        # re-read: a message sent while the turn ran may have changed the choice
        post = bool(repo.get_turn(conn, turn["id"])["post_comment"])
        with transaction(conn):
            repo.update_turn(
                conn, turn["id"], status="completed", result_json=dumps_json(result),
                conclusion=result["conclusion"], comment_status="pending" if post else "",
                comment_body=body, finished_at=beijing_timestamp(),
            )
            repo.add_event(
                conn, conversation_id=conversation["id"], turn_id=turn["id"],
                kind="result", payload=result,
            )
            if not post:
                repo.add_event(
                    conn, conversation_id=conversation["id"], turn_id=turn["id"],
                    kind="jira_comment", payload={"status": "skipped"},
                )
        reported = (result.get("machine") or "").strip()
        if reported and reported != active.machine_used:
            self._note_machine(conn, active, reported)
        if post:
            await post_turn_comment(conn, turn["id"])

    async def _pull_artifacts(self, conn, active: ActiveTurn, conversation: dict, result: dict) -> list[dict]:
        cid, tid, workspace = conversation["id"], active.turn_id, conversation["workspace"]
        local_dir = Path(settings.jira_agent_data_dir) / cid / tid
        records: list[dict] = []
        seen: set[str] = set()
        pulled = {  # by a previous process that stopped while finishing
            record["remote_path"]: record
            for record in repo.list_files(conn, cid)
            if record["turn_id"] == tid and record["source"] == "artifact"
        }
        for path in result.get("artifacts") or []:
            remote = domain.resolve_workspace_path(workspace, path)
            if remote is None:
                _event(conn, cid, tid, "error", {"text": f"产物路径不在工作目录内，已忽略：{path}"})
                continue
            if remote in seen:
                continue
            seen.add(remote)
            if posixpath.relpath(remote, workspace) in pulled:
                records.append(pulled[posixpath.relpath(remote, workspace)])
                continue
            try:
                data = await active.client.read_file(remote)  # type: ignore[union-attr]
            except CodexAppServerError as exc:
                _event(conn, cid, tid, "error", {"text": f"产物读取失败 {path}：{exc}"})
                continue
            if len(data) > _ARTIFACT_MAX_BYTES:
                _event(conn, cid, tid, "error", {"text": f"产物 {path} 超过 10MB，未拉回网站"})
                continue
            local_dir.mkdir(parents=True, exist_ok=True)
            name = domain.safe_filename(posixpath.basename(remote))
            local = local_dir / name
            index = 1
            while local.exists():
                local = local_dir / f"{index}-{name}"
                index += 1
            local.write_bytes(data)
            with transaction(conn):
                records.append(repo.add_file(
                    conn, conversation_id=cid, turn_id=tid, direction="output", source="artifact",
                    name=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    local_path=str(local), remote_path=posixpath.relpath(remote, workspace),
                ))
        return records


runner = JiraAgentRunner()


async def check_health() -> list[dict]:
    """Connect + initialize against every configured group's app-server."""
    report = []
    for group in load_groups().values():
        entry = {
            "group": group.name,
            "display_name": group.display_name,
            "ws_url": group.ws_url,
            "max_concurrent": group.max_concurrent,
            "running": runner.running_count(group.name),
            "ok": False,
            "error": "",
        }
        try:
            async with CodexAppServerClient(
                group.ws_url, group.ws_token, open_timeout=5, request_timeout=10
            ):
                entry["ok"] = True
        except CodexAppServerError as exc:
            entry["error"] = str(exc)
        report.append(entry)
    return report
