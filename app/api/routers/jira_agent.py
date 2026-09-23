"""JIRA agent API: hand issues to a group's digital employee and talk to it.

The website only manages conversations, messages, files and records; the
agent itself runs on the group's Codex app-server (see docs/jira-agent.md).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.deps import require_tab_access
from app.services import jira_agent_runner, jira_agent_ssh_key, jira_agent_service as service

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
    # "" = the agent picks from the system machine list; else user@host
    machine: str = ""
    # the succeeded SSH key upload for a user@host machine; one per hand-over
    ssh_key_session_id: str = ""
    # False = the result stays on the website; nothing is posted to JIRA
    post_comment: bool = True


class MessageBody(BaseModel):
    text: str = ""
    files: list[UploadBody] = Field(default_factory=list)
    # applies to the turn this message starts or joins; the latest message wins
    post_comment: bool = True


class MachineBody(BaseModel):
    agent_group: str = ""
    ssh_target: str
    description: str = ""


class KeySessionBody(BaseModel):
    agent_group: str
    target: str


class KeyInputBody(BaseModel):
    data: str


@router.get("/health")
async def health(user: dict = Depends(require_jira_agent_access)) -> dict:
    return {"groups": await jira_agent_runner.check_health()}


@router.get("/issues")
async def search_issues(
    q: str = "",
    scope: str = "",
    user: dict = Depends(require_jira_agent_access),
) -> dict:
    if scope == "handled":
        return await service.list_handled_issues(user)
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


# ── execution machines ────────────────────────────────────────

@router.get("/machines")
async def list_machines(group: str = "", user: dict = Depends(require_jira_agent_access)) -> dict:
    return service.list_machines(user, group)


@router.post("/machines")
async def create_machine(body: MachineBody, user: dict = Depends(require_jira_agent_access)) -> dict:
    return service.create_machine(user, body.model_dump())


@router.put("/machines/{machine_id}")
async def update_machine(
    machine_id: str, body: MachineBody, user: dict = Depends(require_jira_agent_access),
) -> dict:
    return service.update_machine(user, machine_id, body.model_dump())


@router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return service.delete_machine(user, machine_id)


@router.get("/ssh-key-info")
async def ssh_key_info(group: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await jira_agent_ssh_key.key_info(group)


@router.post("/ssh-key-sessions")
async def start_key_session(body: KeySessionBody, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await jira_agent_ssh_key.sessions.start(user, body.agent_group, body.target)


@router.get("/ssh-key-sessions/{session_id}")
async def get_key_session(
    session_id: str, after: int = 0, user: dict = Depends(require_jira_agent_access),
) -> dict:
    return jira_agent_ssh_key.sessions.get(user, session_id, after)


@router.post("/ssh-key-sessions/{session_id}/input")
async def key_session_input(
    session_id: str, body: KeyInputBody, user: dict = Depends(require_jira_agent_access),
) -> dict:
    return await jira_agent_ssh_key.sessions.write(user, session_id, body.data)


@router.post("/ssh-key-sessions/{session_id}/close")
async def close_key_session(session_id: str, user: dict = Depends(require_jira_agent_access)) -> dict:
    return await jira_agent_ssh_key.sessions.close(user, session_id)
