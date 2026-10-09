"""Put server B's SSH public key on a user-given user@host, from server B.

A password terminal for machines that are not in the system list: the website
relays keystrokes to ``ssh-copy-id`` running in a PTY on the group's
app-server (``command/exec``), then checks key login from B with BatchMode.
B keeps running only codex app-server.  What the user types is forwarded to
the PTY and never stored or logged.

Every hand-over to a user-given machine needs a fresh upload: a succeeded
session is a one-time pass that the hand-over claims (``claim``), so nothing
about the machine is remembered between hand-overs.

The app-server enforces the time limit (``PROCESS_TIMEOUT_MS``), so a
session cannot outlive it even if the website stops.  Sessions live in this
process (the website runs a single worker).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from dataclasses import dataclass, field

from app.api.errors import ApiError
from app.domain import jira_agent as domain
from app.integrations.codex_app_server import CodexAppServerClient, CodexAppServerError
from app.repositories.base import new_id
from app.services.jira_agent_runner import load_groups

logger = logging.getLogger(__name__)

OUTPUT_LIMIT = 64 * 1024
MAX_INPUT_CHARS = 1024
PROCESS_TIMEOUT_MS = 60_000
VERIFY_TIMEOUT_MS = 20_000
KEEP_SECONDS = 600.0
_POLL_SECONDS = 0.5
_TIMEOUT_EXIT_CODE = 124  # what the app-server reports for a timed-out command

_LIVE = ("running", "verifying")


@dataclass
class KeySession:
    id: str
    username: str
    group: domain.AgentGroup
    target: str
    key_path: str
    fingerprint: str
    client: CodexAppServerClient
    exit: asyncio.Future
    process_id: str = "ssh-copy-id"
    output: bytearray = field(default_factory=bytearray)
    dropped: int = 0  # bytes cut from the front of output
    # running → verifying → succeeded | failed; closed when the user stops it
    status: str = "running"
    message: str = ""
    exit_code: int | None = None
    closed_by_user: bool = False
    claimed: bool = False  # a succeeded session admits exactly one hand-over
    finished: float = 0.0
    task: asyncio.Task | None = None

    def append(self, data: bytes) -> None:
        self.output += data
        overflow = len(self.output) - OUTPUT_LIMIT
        if overflow > 0:
            del self.output[:overflow]
            self.dropped += overflow

    def view(self, after: int = 0) -> dict:
        start = max(after, self.dropped) - self.dropped
        return {
            "id": self.id,
            "agent_group": self.group.name,
            "target": self.target,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "message": self.message,
            "exit_code": self.exit_code,
            "output": bytes(self.output[start:]).decode("utf-8", "replace"),
            "offset": self.dropped + len(self.output),
        }


def _group(agent_group: str) -> domain.AgentGroup:
    group = load_groups().get(agent_group)
    if group is None:
        raise ApiError(404, f"未配置数字员工组 {agent_group}")
    return group


async def public_key(group: domain.AgentGroup) -> dict:
    """Server B's public key (read from B): type, fingerprint, comment, path."""
    if not group.ssh_key_path:
        raise ApiError(409, f"{group.display_name} 未配置 SSH_KEY_PATH（release_system.conf）")
    path = f"{group.ssh_key_path}.pub"
    try:
        async with CodexAppServerClient(
            group.ws_url, group.ws_token, open_timeout=5, request_timeout=15
        ) as client:
            data = await client.read_file(path)
        info = domain.ssh_public_key_info(data.decode("utf-8", "replace"))
    except CodexAppServerError as exc:
        raise ApiError(502, f"无法从服务器 B 读取 SSH 公钥 {path}：{exc}") from exc
    except ValueError as exc:
        raise ApiError(502, f"服务器 B 上的 {path} 不是有效的 SSH 公钥") from exc
    return {**info, "path": path}


async def key_info(agent_group: str) -> dict:
    group = _group(agent_group)
    key = await public_key(group)
    return {
        "agent_group": group.name,
        "display_name": group.display_name,
        "fingerprint": key["fingerprint"],
        "comment": key["comment"],
        "path": key["path"],
    }


class KeySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, KeySession] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        for session_id, session in list(self._sessions.items()):
            if session.finished and now - session.finished > KEEP_SECONDS:
                self._sessions.pop(session_id, None)

    def _own(self, user: dict, session_id: str) -> KeySession:
        self._prune()
        session = self._sessions.get(session_id)
        if session is None or session.username.casefold() != user["username"].casefold():
            raise ApiError(404, "上传会话不存在或已过期")
        return session

    async def start(self, user: dict, agent_group: str, target: str) -> dict:
        group = _group(agent_group)
        target = domain.parse_ssh_target(target)
        self._prune()
        if any(
            s.username.casefold() == user["username"].casefold() and s.status in _LIVE
            for s in self._sessions.values()
        ):
            raise ApiError(409, "你已有一个进行中的上传会话，请先关闭它")
        key = await public_key(group)
        client = CodexAppServerClient(group.ws_url, group.ws_token, open_timeout=5, request_timeout=30)
        try:
            await client.connect()
            session = KeySession(
                id=new_id("jak"), username=user["username"], group=group, target=target,
                key_path=group.ssh_key_path, fingerprint=key["fingerprint"], client=client,
                exit=asyncio.get_running_loop().create_future(),
            )
            session.exit = await client.start_process(
                session.process_id,
                ["ssh-copy-id", "-i", key["path"], "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=10", target],
                timeout_ms=PROCESS_TIMEOUT_MS,
            )
        except CodexAppServerError as exc:
            await client.close()
            raise ApiError(502, f"无法在服务器 B 上启动 ssh-copy-id：{exc}") from exc
        self._sessions[session.id] = session
        session.task = asyncio.create_task(self._run(session))
        return session.view()

    def get(self, user: dict, session_id: str, after: int) -> dict:
        return self._own(user, session_id).view(after)

    def claim(self, user: dict, session_id: str, group: domain.AgentGroup, target: str) -> None:
        """Spend this user's succeeded upload of ``target`` on one hand-over."""
        self._prune()
        session = self._sessions.get(session_id)
        if (
            session is None
            or session.username.casefold() != user["username"].casefold()
            or session.group.name != group.name
            or session.target != target
            or session.status != "succeeded"
            or session.claimed
        ):
            raise ApiError(409, f"每次交给自填机器前都要上传 SSH 公钥到 {target} 并通过连接测试")
        session.claimed = True

    async def write(self, user: dict, session_id: str, data: str) -> dict:
        session = self._own(user, session_id)
        if session.status != "running" or session.closed_by_user:
            raise ApiError(409, "ssh-copy-id 已结束，不能再输入")
        if len(data) > MAX_INPUT_CHARS:
            raise ValueError("输入过长")
        try:
            await session.client.write_process(session.process_id, data.encode("utf-8"))
        except CodexAppServerError as exc:
            raise ApiError(409, f"输入未送达服务器 B：{exc}") from exc
        return {"ok": True}

    async def close(self, user: dict, session_id: str) -> dict:
        session = self._own(user, session_id)
        if session.status == "running" and not session.closed_by_user:
            session.closed_by_user = True
            await self._terminate(session)
        return session.view(session.dropped + len(session.output))

    async def stop(self) -> None:
        tasks = [s.task for s in self._sessions.values() if s.task and not s.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()

    @staticmethod
    async def _terminate(session: KeySession) -> None:
        try:
            await session.client.terminate_process(session.process_id)
        except CodexAppServerError as exc:
            logger.warning("terminate of ssh-copy-id session %s failed: %s", session.id, exc)

    def _take_output(self, session: KeySession, message: dict | None) -> None:
        if not message or message.get("method") != "command/exec/outputDelta":
            return
        params = message.get("params") or {}
        if params.get("processId") != session.process_id:
            return
        try:
            data = base64.b64decode(params.get("deltaBase64") or "", validate=True)
        except (binascii.Error, ValueError):
            return
        session.append(data)

    async def _run(self, session: KeySession) -> None:
        client = session.client
        try:
            # Ends when the process exits: by itself, terminated by the user,
            # killed by the app-server timeout, or the connection drops.
            while not session.exit.done():
                self._take_output(session, await client.next_event(timeout=_POLL_SECONDS))
            session.exit_code = await session.exit
            while True:  # output that arrived together with the exit
                message = await client.next_event(timeout=0.05)
                if message is None:
                    break
                self._take_output(session, message)

            if session.closed_by_user:
                session.status, session.message = "closed", "已关闭"
            elif session.exit_code == _TIMEOUT_EXIT_CODE:
                session.status = "failed"
                session.message = f"超过 {PROCESS_TIMEOUT_MS // 1000} 秒未完成，已结束，请重新上传"
            elif session.exit_code != 0:
                session.status = "failed"
                session.message = f"ssh-copy-id 失败（退出码 {session.exit_code}），请查看上面的输出"
            else:
                session.status = "verifying"
                result = await client.exec_command(
                    ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                     "-o", "StrictHostKeyChecking=accept-new", "-i", session.key_path,
                     session.target, "true"],
                    timeout_ms=VERIFY_TIMEOUT_MS,
                )
                if result.get("exitCode") == 0:
                    session.status = "succeeded"
                    session.message = f"连接测试通过：服务器 B 可以免密登录 {session.target}"
                else:
                    detail = (result.get("stderr") or result.get("stdout") or "").strip()[:500]
                    session.status = "failed"
                    session.message = (
                        f"公钥已上传，但从服务器 B 免密登录 {session.target} 失败"
                        f"（退出码 {result.get('exitCode')}）{'：' + detail if detail else ''}"
                    )
        except CodexAppServerError as exc:
            session.status, session.message = "failed", f"与服务器 B 的连接出错：{exc}"
        except asyncio.CancelledError:
            session.status, session.message = "closed", "网站停止，会话已结束"
            if not session.exit.done():
                await self._terminate(session)
            raise
        finally:
            await client.close()
            session.finished = time.monotonic()


sessions = KeySessionManager()
