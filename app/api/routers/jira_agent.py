"""JIRA agent API: hand issues to a group's digital employee and talk to it.

The website only manages conversations, messages, files and records; the
agent itself runs on the group's Codex app-server (see docs/jira-agent.md).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.deps import require_tab_access
from app.services import jira_agent_runner, jira_agent_service as service

router = APIRouter(prefix="/api/jira-agent")

require_jira_agent_access = require_tab_access(
    "jira-agent",
    message="无权访问 JIRA agent",
)


class UploadBody(BaseModel):
    filename: str
    content_base64: str


class HandoverBody(BaseModel):
    issue_key: str
    note: str = ""
    files: list[UploadBody] = Field(default_factory=list)
    new_conversation: bool = False


class MessageBody(BaseModel):
    text: str = ""
    files: list[UploadBody] = Field(default_factory=list)


@router.get("/health")
async def health(user: dict = Depends(require_jira_agent_access)) -> dict:
    return {"groups": await jira_agent_runner.check_health()}


@router.get("/issues")
async def search_issues(q: str = "", user: dict = Depends(require_jira_agent_access)) -> dict:
    return await service.search_issues(user, q)


@router.get("/issues/{issue_key}")
async def preview_issue(issue_key: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await service.preview_issue(user, issue_key)


@router.get("/conversations")
async def list_conversations(user: dict = Depends(require_jira_agent_access)) -> dict:
    return service.list_conversations(user)


@router.post("/conversations")
async def handover(body: HandoverBody, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await service.handover(user, body.model_dump())


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return service.get_conversation_detail(user, conversation_id)


@router.get("/conversations/{conversation_id}/events")
async def list_events(
    conversation_id: str,
    after: int = 0,
    user: dict = Depends(require_jira_agent_access),
) -> dict:
    return service.list_events(user, conversation_id, after)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: MessageBody,
    user: dict = Depends(require_jira_agent_access),
) -> dict:
    return await service.send_message(user, conversation_id, body.model_dump())


@router.post("/conversations/{conversation_id}/cancel")
async def cancel(conversation_id: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await service.cancel(user, conversation_id)


@router.post("/turns/{turn_id}/comment/retry")
async def retry_comment(turn_id: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await service.retry_comment(user, turn_id)


@router.get("/conversations/{conversation_id}/files/{file_id}")
async def download_file(
    conversation_id: str,
    file_id: str,
    user: dict = Depends(require_jira_agent_access),
) -> FileResponse:
    path, name = service.file_for_download(user, conversation_id, file_id)
    return FileResponse(path, filename=name)
