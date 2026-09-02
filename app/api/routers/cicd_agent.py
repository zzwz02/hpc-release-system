"""CICD Agent proxy routes.

The browser stays same-origin with hpc_release_system while this router talks to
the standalone CICD_Agent backend configured by settings.cicd_agent_base_url.
"""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
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

    return {
        "conversation": updated_conversation,
        "messages": [user_message, assistant_message],
        "assistant": assistant,
    }


@router.post("/cicd-assistant")
async def cicd_assistant(
    request: Request,
    user: dict = Depends(require_assistant_access),
) -> JSONResponse:
    body = await request.json()
    body["user_id"] = user.get("username") or user.get("display_name") or "frontend"
    return _request_agent("POST", "/api/v1/cicd-assistant", body=body)
