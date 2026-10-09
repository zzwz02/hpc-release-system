"""WebSocket JSON-RPC client for a remote Codex app-server.

Generic protocol client with no JIRA or website logic, so other modules can
reuse it.  The app-server host owns sandbox/approval policy, skills and
credentials; this client only drives threads, turns and files.

Protocol notes (codex app-server v2):
  - one ``initialize`` request + ``initialized`` notification per connection
  - notifications for a thread are delivered to the connection that started
    or resumed it, so keep the connection open for the whole turn
  - capability-token auth is an ``Authorization: Bearer`` header on upgrade
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any

import websockets

logger = logging.getLogger(__name__)

_MAX_MESSAGE_BYTES = 64 * 1024 * 1024
_METHOD_NOT_FOUND = -32601


class CodexAppServerError(RuntimeError):
    """Connection, protocol or RPC failure talking to the app-server."""


class CodexAppServerClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        client_name: str = "hpc_release_system",
        request_timeout: float = 60.0,
        open_timeout: float = 15.0,
    ) -> None:
        self.url = url
        self.token = token
        self.client_name = client_name
        self.request_timeout = request_timeout
        self.open_timeout = open_timeout
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue[dict | None] = asyncio.Queue()
        self._next_id = 0
        self._closed_reason = ""

    async def __aenter__(self) -> CodexAppServerClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    # ── connection ────────────────────────────────────────────

    async def connect(self) -> dict:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            self._ws = await websockets.connect(
                self.url,
                additional_headers=headers,
                max_size=_MAX_MESSAGE_BYTES,
                open_timeout=self.open_timeout,
            )
        except Exception as exc:  # InvalidStatus (401), OSError, TimeoutError
            raise CodexAppServerError(f"无法连接 Codex app-server：{exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())
        try:
            result = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": self.client_name,
                        "title": "HPC release system",
                        "version": "0.1.0",
                    }
                },
            )
            await self._send({"method": "initialized"})
        except BaseException:
            await self.close()
            raise
        return result

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # already broken
                pass
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)

    # ── JSON-RPC ──────────────────────────────────────────────

    async def request(
        self, method: str, params: dict, *, timeout: float | None = None
    ) -> dict:
        request_id, future = await self._send_request(method, params)
        try:
            message = await asyncio.wait_for(future, timeout or self.request_timeout)
        except asyncio.TimeoutError as exc:
            raise CodexAppServerError(f"Codex RPC {method} 超时") from exc
        finally:
            self._pending.pop(request_id, None)
        return self._unwrap(method, message)

    async def _send_request(self, method: str, params: dict) -> tuple[int, asyncio.Future]:
        if self._closed_reason:
            raise CodexAppServerError(self._closed_reason)
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
        except BaseException:
            self._pending.pop(request_id, None)
            raise
        return request_id, future

    @staticmethod
    def _unwrap(method: str, message: dict) -> dict:
        if "error" in message:
            error = message.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else error
            raise CodexAppServerError(f"Codex RPC {method} 失败：{detail}")
        return message.get("result") or {}

    async def next_event(self, timeout: float | None = None) -> dict | None:
        """Return the next server notification, or None on timeout.

        Raises CodexAppServerError once the connection has closed.
        """
        try:
            event = await asyncio.wait_for(self._events.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if event is None:
            self._events.put_nowait(None)  # keep signalling for later callers
            raise CodexAppServerError(self._closed_reason or "Codex app-server 连接已断开")
        return event

    async def _send(self, payload: dict) -> None:
        if self._ws is None:
            raise CodexAppServerError("Codex app-server 尚未连接")
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except websockets.ConnectionClosed as exc:
            raise CodexAppServerError(f"Codex app-server 连接中断：{exc}") from exc

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except ValueError:
                    logger.warning("ignoring non-JSON app-server frame")
                    continue
                if "method" not in message:
                    future = self._pending.get(message.get("id"))
                    if future is not None and not future.done():
                        future.set_result(message)
                elif "id" in message:
                    await self._reject_server_request(message)
                else:
                    self._events.put_nowait(message)
            self._closed_reason = "Codex app-server 连接已关闭"
        except websockets.ConnectionClosed as exc:
            self._closed_reason = f"Codex app-server 连接中断：{exc}"
        except Exception as exc:  # defensive: never leave waiters hanging
            logger.exception("app-server reader failed")
            self._closed_reason = f"Codex app-server 读取失败：{exc}"
        finally:
            if not self._closed_reason:
                self._closed_reason = "Codex app-server 连接已关闭"
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(CodexAppServerError(self._closed_reason))
            self._events.put_nowait(None)

    async def _reject_server_request(self, message: dict) -> None:
        """Approvals and client-side tools are not offered by this client.

        The server host is expected to run with approval_policy=never; if a
        request still arrives, decline it and surface it as an event.
        """
        method = message.get("method", "")
        self._events.put_nowait(
            {"method": "client/serverRequestRejected", "params": {"method": method}}
        )
        try:
            await self._send(
                {
                    "id": message["id"],
                    "error": {
                        "code": _METHOD_NOT_FOUND,
                        "message": f"client does not handle {method}",
                    },
                }
            )
        except CodexAppServerError:
            pass

    # ── threads and turns ─────────────────────────────────────

    async def start_thread(
        self,
        *,
        cwd: str,
        developer_instructions: str = "",
        model: str = "",
        service_name: str = "",
    ) -> dict:
        params: dict[str, Any] = {"cwd": cwd, "ephemeral": False}
        if developer_instructions:
            params["developerInstructions"] = developer_instructions
        if model:
            params["model"] = model
        if service_name:
            params["serviceName"] = service_name
        return (await self.request("thread/start", params))["thread"]

    async def resume_thread(self, thread_id: str, *, model: str = "") -> dict:
        params: dict[str, Any] = {"threadId": thread_id, "excludeTurns": True}
        if model:
            params["model"] = model
        return (await self.request("thread/resume", params))["thread"]

    async def list_turns(
        self, thread_id: str, *, limit: int = 1, items_view: str = "full"
    ) -> list[dict]:
        """Newest turns first, with their persisted items."""
        result = await self.request(
            "thread/turns/list",
            {"threadId": thread_id, "limit": limit, "itemsView": items_view},
            timeout=max(self.request_timeout, 300),
        )
        return result.get("data") or []

    async def archive_thread(self, thread_id: str) -> None:
        await self.request("thread/archive", {"threadId": thread_id})

    async def start_turn(
        self,
        thread_id: str,
        text: str,
        *,
        output_schema: dict | None = None,
        model: str = "",
    ) -> dict:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if output_schema is not None:
            params["outputSchema"] = output_schema
        if model:
            params["model"] = model
        return (await self.request("turn/start", params))["turn"]

    async def steer_turn(self, thread_id: str, expected_turn_id: str, text: str) -> dict:
        return await self.request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": expected_turn_id,
                "input": [{"type": "text", "text": text}],
            },
        )

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> None:
        await self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    # ── files on the app-server host ──────────────────────────

    async def create_directory(self, path: str) -> None:
        await self.request("fs/createDirectory", {"path": path, "recursive": True})

    async def write_file(self, path: str, data: bytes) -> None:
        await self.request(
            "fs/writeFile",
            {"path": path, "dataBase64": base64.b64encode(data).decode("ascii")},
            timeout=max(self.request_timeout, 300),
        )

    async def read_file(self, path: str) -> bytes:
        result = await self.request(
            "fs/readFile", {"path": path}, timeout=max(self.request_timeout, 300)
        )
        return base64.b64decode(result.get("dataBase64") or "")

    # ── commands on the app-server host ───────────────────────

    async def exec_command(self, argv: list[str], *, timeout_ms: int) -> dict:
        """Run a command to completion: {exitCode, stdout, stderr}."""
        return await self.request(
            "command/exec",
            {"command": argv, "timeoutMs": timeout_ms},
            timeout=timeout_ms / 1000 + 30,
        )

    async def start_process(
        self, process_id: str, argv: list[str], *, timeout_ms: int, rows: int = 24, cols: int = 120
    ) -> asyncio.Future:
        """Start a PTY process; the returned future resolves to its exit code.

        Output arrives as ``command/exec/outputDelta`` notifications and input
        goes through ``write_process``; process ids are scoped to this
        connection.  The app-server kills the process after ``timeout_ms``
        (exit code 124), even if this client is gone; its default (10 s in
        0.153) is too short for someone typing a password.
        """
        request_id, future = await self._send_request(
            "command/exec",
            {
                "command": argv,
                "processId": process_id,
                "tty": True,
                "timeoutMs": timeout_ms,
                "size": {"rows": rows, "cols": cols},
            },
        )

        async def exit_code() -> int:
            try:
                message = await future
            finally:
                self._pending.pop(request_id, None)
            return int(self._unwrap("command/exec", message).get("exitCode", -1))

        return asyncio.ensure_future(exit_code())

    async def write_process(self, process_id: str, data: bytes) -> None:
        await self.request(
            "command/exec/write",
            {"processId": process_id, "deltaBase64": base64.b64encode(data).decode("ascii")},
        )

    async def terminate_process(self, process_id: str) -> None:
        await self.request("command/exec/terminate", {"processId": process_id})
