"""CICD Agent proxy routes.

The browser stays same-origin with hpc_release_system while this router talks to
the standalone CICD_Agent backend configured by settings.cicd_agent_base_url.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.db.connection import transaction
from app.deps import get_assistant_db, require_tab_access
from app.repositories import assistant_repo
from app.timeutil import beijing_timestamp

router = APIRouter(prefix="/api/cicd-agent", tags=["cicd-agent"])

require_jenkins_access = require_tab_access(
    "jenkins-failures",
    message="无权访问 Jenkins 失败查询",
)
require_assistant_access = require_tab_access(
    "cicd-assistant",
    message="无权访问 CICD 助手",
)


class AssistantConversationCreate(BaseModel):
    title: str | None = None


class AssistantConversationMessageCreate(BaseModel):
    message: str = Field(..., min_length=1)


_ASSISTANT_SLOT_KEYS = {
    "intent",
    "app_name",
    "app_version",
    "dockerfile_path",
    "os",
    "arch",
    "sdk",
    "sdkversion",
    "supported_chip",
    "image_aliases",
    "test_cases",
    "last_query_summary",
}


def _agent_url(path: str, query: str = "") -> str:
    base = settings.cicd_agent_base_url.rstrip("/")
    url = f"{base}{path}"
    return f"{url}?{query}" if query else url


def _decode_json(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"ok": False, "error": text or "CICD Agent returned a non-JSON response"}


def _encode_ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def _request_agent_payload(
    method: str,
    path: str,
    *,
    query: str = "",
    body: Any = None,
) -> tuple[int, Any]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        _agent_url(path, query),
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=settings.cicd_agent_timeout_seconds) as response:
            payload = _decode_json(response.read())
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = _decode_json(exc.read())
        return exc.code, payload
    except urllib.error.URLError as exc:
        return 502, {"ok": False, "error": f"CICD Agent 不可用：{exc.reason}"}


def _request_agent(method: str, path: str, *, query: str = "", body: Any = None) -> JSONResponse:
    status_code, payload = _request_agent_payload(method, path, query=query, body=body)
    return JSONResponse(status_code=status_code, content=payload)


async def _request_agent_stream_events(path: str, *, body: Any) -> AsyncIterator[dict[str, Any]]:
    timeout = httpx.Timeout(settings.cicd_agent_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            async with client.stream(
                "POST",
                _agent_url(path),
                json=body,
                headers={
                    "Accept": "application/x-ndjson",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code >= 400:
                    payload = _decode_json(await response.aread())
                    error = payload.get("error") if isinstance(payload, dict) else None
                    yield {
                        "type": "error",
                        "error": error or f"CICD Agent HTTP {response.status_code}",
                        "agent_status_code": response.status_code,
                    }
                    return

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        yield {
                            "type": "error",
                            "error": "CICD Agent 返回了无效的 NDJSON 流",
                            "agent_status_code": response.status_code,
                        }
                        return
                    if isinstance(event, dict):
                        yield event
                    else:
                        yield {
                            "type": "error",
                            "error": "CICD Agent 返回的流事件不是 JSON object",
                            "agent_status_code": response.status_code,
                        }
                        return
    except httpx.RequestError as exc:
        yield {
            "type": "error",
            "error": f"CICD Agent 不可用：{exc}",
            "agent_status_code": 502,
        }


def _interrupted_answer(answer: str) -> str:
    text = answer.strip()
    if text:
        return f"{text}\n\n（已停止生成）"
    return "已停止生成，未产生可展示内容。"


def _assistant_user_id(user: dict) -> str:
    return str(user.get("username") or user.get("display_name") or "frontend")


def _title_from_message(message: str) -> str:
    title = " ".join(message.strip().split())
    if not title:
        return "新会话"
    return title[:36]


def _agent_history(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            history.append({"role": str(role), "content": content})
    return history


def _agent_error(status_code: int, payload: Any) -> str | None:
    if isinstance(payload, dict):
        raw_error = payload.get("error")
        if raw_error:
            return str(raw_error)
        if payload.get("ok") is False:
            return f"CICD Agent HTTP {status_code}"
    if status_code >= 400:
        return f"CICD Agent HTTP {status_code}"
    return None


def _assistant_result(
    *,
    conversation_id: str,
    status_code: int,
    payload: Any,
) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    agent_error = _agent_error(status_code, data)
    answer = str(data.get("answer") or "").strip()
    if not answer and agent_error:
        answer = f"CICD助手调用失败：{agent_error}"
    if not answer:
        answer = "CICD助手没有返回可展示的回答。"
    return {
        "answer": answer,
        "conversation_id": str(data.get("conversation_id") or conversation_id),
        "provider": str(data.get("provider") or ""),
        "model": str(data.get("model") or ""),
        "tools": data.get("tools") if isinstance(data.get("tools"), list) else [],
        "available_tools": (
            data.get("available_tools") if isinstance(data.get("available_tools"), list) else []
        ),
        "tool_error": data.get("tool_error") or None,
        "agent_error": agent_error,
        "agent_status_code": status_code,
        "state_delta": (
            data.get("state_delta") if isinstance(data.get("state_delta"), dict) else {}
        ),
    }


def _assistant_context(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "rolling_summary": state.get("rolling_summary") or "",
        "slots": state.get("slots") if isinstance(state.get("slots"), dict) else {},
    }


def _clean_state_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text[:500] if text else None
    if isinstance(value, list):
        result = []
        for item in value:
            clean = _clean_state_value(item)
            if clean is not None and clean not in result:
                result.append(clean)
        return result or None
    if isinstance(value, dict):
        result = {
            str(key): clean
            for key, item in value.items()
            if (clean := _clean_state_value(item)) is not None
        }
        return result or None
    if isinstance(value, bool | int | float):
        return value
    return str(value).strip()[:500] or None


def _merge_slots(current: dict[str, Any], state_delta: Any) -> dict[str, Any]:
    merged = dict(current)
    if not isinstance(state_delta, dict):
        return merged
    for key, value in state_delta.items():
        if key not in _ASSISTANT_SLOT_KEYS:
            continue
        clean = _clean_state_value(value)
        if clean is not None:
            merged[key] = clean
    return merged


def _summary_line(message: dict[str, Any]) -> str:
    role = "用户" if message.get("role") == "user" else "助手"
    content = " ".join(str(message.get("content") or "").split())
    if len(content) > 320:
        content = f"{content[:320]}..."
    return f"- {role}: {content}"


def _append_rolling_summary(
    existing: str,
    *,
    messages: list[dict[str, Any]],
    through_sequence: int,
) -> str:
    if not messages:
        return existing
    lines = [_summary_line(message) for message in messages]
    section = f"[截至第 {through_sequence} 条消息]\n" + "\n".join(lines)
    combined = f"{existing.rstrip()}\n\n{section}".strip() if existing else section
    max_chars = max(settings.assistant_summary_max_chars, 500)
    if len(combined) <= max_chars:
        return combined
    return "（前序摘要已截断）\n" + combined[-max_chars:]


def _refresh_assistant_state(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    state: dict[str, Any],
    state_delta: Any,
    message_count: int,
    updated_at: str,
) -> dict[str, Any]:
    slots = _merge_slots(
        state.get("slots") if isinstance(state.get("slots"), dict) else {},
        state_delta,
    )
    rolling_summary = str(state.get("rolling_summary") or "")
    summarized_until = int(state.get("summarized_until_sequence") or 0)

    trigger = max(settings.assistant_summary_trigger_messages, 0)
    keep_messages = max(settings.assistant_summary_keep_messages, 0)
    if trigger and message_count > trigger:
        target_sequence = max(message_count - keep_messages, summarized_until)
        if target_sequence > summarized_until:
            messages = assistant_repo.messages_in_sequence_range(
                conn,
                conversation_id=conversation_id,
                after_sequence=summarized_until,
                through_sequence=target_sequence,
            )
            rolling_summary = _append_rolling_summary(
                rolling_summary,
                messages=messages,
                through_sequence=target_sequence,
            )
            summarized_until = target_sequence

    return assistant_repo.update_state(
        conn,
        conversation_id=conversation_id,
        rolling_summary=rolling_summary,
        slots=slots,
        summarized_until_sequence=summarized_until,
        updated_at=updated_at,
    )


def _persist_stream_assistant_result(
    conn: sqlite3.Connection,
    *,
    conversation_id: str,
    user_id: str,
    state: dict[str, Any],
    agent_event: dict[str, Any],
    fallback_answer: str,
) -> dict[str, Any]:
    event = dict(agent_event)
    if not str(event.get("answer") or "").strip():
        event["answer"] = fallback_answer or "CICD助手没有返回可展示的回答。"
    status_code = int(event.get("agent_status_code") or 200)
    assistant = _assistant_result(
        conversation_id=conversation_id,
        status_code=status_code,
        payload=event,
    )
    with transaction(conn):
        assistant_message = assistant_repo.add_message(
            conn,
            conversation_id=conversation_id,
            role="assistant",
            content=assistant["answer"],
            created_at=beijing_timestamp(),
            metadata={
                "streaming": True,
                "provider": assistant["provider"],
                "model": assistant["model"],
                "tools": assistant["tools"],
                "available_tools": assistant["available_tools"],
                "tool_error": assistant["tool_error"],
                "agent_error": assistant["agent_error"],
                "agent_status_code": assistant["agent_status_code"],
                "agent_conversation_id": assistant["conversation_id"],
            },
        )
        updated_conversation = assistant_repo.get_conversation(
            conn,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        updated_state = _refresh_assistant_state(
            conn,
            conversation_id=conversation_id,
            state=state,
            state_delta=assistant["state_delta"],
            message_count=int(updated_conversation["message_count"]) if updated_conversation else 0,
            updated_at=beijing_timestamp(),
        )
    return {
        "conversation": updated_conversation,
        "assistant_message": assistant_message,
        "assistant": assistant,
        "state": updated_state,
    }


@router.get("/failures")
def list_failures(
    request: Request,
    _user: dict = Depends(require_jenkins_access),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures", query=request.url.query)


@router.get("/failures/summary")
def summarize_failures(
    request: Request,
    _user: dict = Depends(require_jenkins_access),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures/summary", query=request.url.query)


@router.get("/failures/filter-options")
def failure_filter_options(
    request: Request,
    _user: dict = Depends(require_jenkins_access),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures/filter-options", query=request.url.query)


@router.get("/failures/{record_id}")
def failure_detail(
    record_id: int,
    _user: dict = Depends(require_jenkins_access),
) -> JSONResponse:
    return _request_agent("GET", f"/api/v1/failures/{record_id}")


@router.post("/failure-chat")
async def failure_chat(
    request: Request,
    _user: dict = Depends(require_assistant_access),
) -> JSONResponse:
    body = await request.json()
    return _request_agent("POST", "/api/v1/failure-chat", body=body)


@router.get("/assistant/conversations")
def list_assistant_conversations(
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> dict:
    conversations = assistant_repo.list_conversations(conn, user_id=_assistant_user_id(user))
    return {"conversations": conversations}


@router.post("/assistant/conversations")
def create_assistant_conversation(
    payload: AssistantConversationCreate,
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> dict:
    now = beijing_timestamp()
    with transaction(conn):
        conversation = assistant_repo.create_conversation(
            conn,
            user_id=_assistant_user_id(user),
            title=payload.title or "新会话",
            created_at=now,
        )
    return {"conversation": conversation}


@router.get("/assistant/conversations/{conversation_id}")
def get_assistant_conversation(
    conversation_id: str,
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> dict:
    user_id = _assistant_user_id(user)
    conversation = assistant_repo.get_conversation(
        conn,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return {
        "conversation": conversation,
        "messages": assistant_repo.list_messages(conn, conversation_id=conversation_id),
        "state": assistant_repo.get_state(conn, conversation_id=conversation_id),
    }


@router.delete("/assistant/conversations/{conversation_id}")
def delete_assistant_conversation(
    conversation_id: str,
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> dict:
    with transaction(conn):
        deleted = assistant_repo.soft_delete_conversation(
            conn,
            conversation_id=conversation_id,
            user_id=_assistant_user_id(user),
            deleted_at=beijing_timestamp(),
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return {"ok": True}


@router.post("/assistant/conversations/{conversation_id}/messages")
def send_assistant_conversation_message(
    conversation_id: str,
    payload: AssistantConversationMessageCreate,
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> dict:
    user_id = _assistant_user_id(user)
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="消息不能为空")

    conversation = assistant_repo.get_conversation(
        conn,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    history = assistant_repo.recent_messages(
        conn,
        conversation_id=conversation_id,
        limit=max(settings.assistant_history_limit, 0),
    )
    state = assistant_repo.get_state(conn, conversation_id=conversation_id)
    now = beijing_timestamp()
    with transaction(conn):
        user_message = assistant_repo.add_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            content=message_text,
            created_at=now,
        )
        if not conversation["message_count"]:
            assistant_repo.update_title(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                title=_title_from_message(message_text),
                updated_at=now,
            )

    status_code, agent_payload = _request_agent_payload(
        "POST",
        "/api/v1/cicd-assistant",
        body={
            "message": message_text,
            "conversation_id": conversation_id,
            "history": _agent_history(history),
            "context": _assistant_context(state),
            "user_id": user_id,
        },
    )
    assistant = _assistant_result(
        conversation_id=conversation_id,
        status_code=status_code,
        payload=agent_payload,
    )

    with transaction(conn):
        assistant_message = assistant_repo.add_message(
            conn,
            conversation_id=conversation_id,
            role="assistant",
            content=assistant["answer"],
            created_at=beijing_timestamp(),
            metadata={
                "provider": assistant["provider"],
                "model": assistant["model"],
                "tools": assistant["tools"],
                "available_tools": assistant["available_tools"],
                "tool_error": assistant["tool_error"],
                "agent_error": assistant["agent_error"],
                "agent_status_code": assistant["agent_status_code"],
                "agent_conversation_id": assistant["conversation_id"],
            },
        )
        updated_conversation = assistant_repo.get_conversation(
            conn,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        updated_state = _refresh_assistant_state(
            conn,
            conversation_id=conversation_id,
            state=state,
            state_delta=assistant["state_delta"],
            message_count=int(updated_conversation["message_count"]) if updated_conversation else 0,
            updated_at=beijing_timestamp(),
        )

    return {
        "conversation": updated_conversation,
        "messages": [user_message, assistant_message],
        "assistant": assistant,
        "state": updated_state,
    }


@router.post("/assistant/conversations/{conversation_id}/messages/stream")
async def stream_assistant_conversation_message(
    conversation_id: str,
    payload: AssistantConversationMessageCreate,
    request: Request,
    user: dict = Depends(require_assistant_access),
    conn: sqlite3.Connection = Depends(get_assistant_db),
) -> StreamingResponse:
    user_id = _assistant_user_id(user)
    message_text = payload.message.strip()
    if not message_text:
        raise HTTPException(status_code=422, detail="消息不能为空")

    conversation = assistant_repo.get_conversation(
        conn,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")

    history = assistant_repo.recent_messages(
        conn,
        conversation_id=conversation_id,
        limit=max(settings.assistant_history_limit, 0),
    )
    state = assistant_repo.get_state(conn, conversation_id=conversation_id)
    now = beijing_timestamp()
    with transaction(conn):
        user_message = assistant_repo.add_message(
            conn,
            conversation_id=conversation_id,
            role="user",
            content=message_text,
            created_at=now,
        )
        if not conversation["message_count"]:
            assistant_repo.update_title(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                title=_title_from_message(message_text),
                updated_at=now,
            )
        started_conversation = assistant_repo.get_conversation(
            conn,
            conversation_id=conversation_id,
            user_id=user_id,
        )

    agent_body = {
        "message": message_text,
        "conversation_id": conversation_id,
        "history": _agent_history(history),
        "context": _assistant_context(state),
        "user_id": user_id,
    }

    async def generate() -> AsyncIterator[str]:
        finalized = False
        yield _encode_ndjson(
            {
                "type": "start",
                "conversation": started_conversation,
                "user_message": user_message,
            }
        )
        answer_parts: list[str] = []
        stream_metadata: dict[str, Any] = {}
        used_tools: list[str] = []

        def persist_interrupted() -> None:
            nonlocal finalized
            if finalized:
                return
            final_event = {
                **stream_metadata,
                "type": "error",
                "answer": _interrupted_answer("".join(answer_parts)),
                "error": "用户停止了生成",
                "agent_status_code": 499,
                "tools": used_tools or stream_metadata.get("tools", []),
                "state_delta": stream_metadata.get("state_delta", {}),
            }
            _persist_stream_assistant_result(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                state=state,
                agent_event=final_event,
                fallback_answer=_interrupted_answer("".join(answer_parts)),
            )
            finalized = True

        try:
            async for event in _request_agent_stream_events(
                "/api/v1/cicd-assistant/stream",
                body=agent_body,
            ):
                if await request.is_disconnected():
                    persist_interrupted()
                    return

                event_type = event.get("type")
                if event_type == "start":
                    stream_metadata = {
                        "conversation_id": event.get("conversation_id") or conversation_id,
                        "provider": event.get("provider") or "",
                        "model": event.get("model") or "",
                        "tools": event.get("tools") if isinstance(event.get("tools"), list) else [],
                        "available_tools": (
                            event.get("available_tools")
                            if isinstance(event.get("available_tools"), list)
                            else []
                        ),
                        "tool_error": event.get("tool_error") or None,
                        "state_delta": (
                            event.get("state_delta")
                            if isinstance(event.get("state_delta"), dict)
                            else {}
                        ),
                    }
                    yield _encode_ndjson({"type": "metadata", **stream_metadata})
                    continue
                if event_type == "token":
                    content = str(event.get("content") or "")
                    if content:
                        answer_parts.append(content)
                    yield _encode_ndjson(event)
                    continue
                if event_type == "tool":
                    tool_name = str(event.get("name") or "").strip()
                    if tool_name and tool_name not in used_tools:
                        used_tools.append(tool_name)
                    yield _encode_ndjson(event)
                    continue
                if event_type in {"done", "error"}:
                    final_event = {**stream_metadata, **event}
                    if event_type == "error" and not final_event.get("agent_status_code"):
                        final_event["agent_status_code"] = 502
                    result = _persist_stream_assistant_result(
                        conn,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        state=state,
                        agent_event=final_event,
                        fallback_answer="".join(answer_parts).strip(),
                    )
                    finalized = True
                    yield _encode_ndjson({"type": event_type, **result})
                    return
                yield _encode_ndjson({"type": "metadata", "event": event})

            result = _persist_stream_assistant_result(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                state=state,
                agent_event={
                    **stream_metadata,
                    "type": "error",
                    "error": "CICD Agent 流式响应提前结束",
                    "agent_status_code": 502,
                    "state_delta": stream_metadata.get("state_delta", {}),
                },
                fallback_answer="".join(answer_parts).strip(),
            )
            finalized = True
            yield _encode_ndjson({"type": "error", **result})
        except asyncio.CancelledError:
            persist_interrupted()
            raise

    return StreamingResponse(
        generate(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/cicd-assistant")
async def cicd_assistant(
    request: Request,
    user: dict = Depends(require_assistant_access),
) -> JSONResponse:
    body = await request.json()
    body["user_id"] = user.get("username") or user.get("display_name") or "frontend"
    return _request_agent("POST", "/api/v1/cicd-assistant", body=body)
