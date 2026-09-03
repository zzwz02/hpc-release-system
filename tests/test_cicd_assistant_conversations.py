from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routers import cicd_agent
from app.db.assistant_connection import connect_assistant, reset_assistant_init_state
from app.db.connection import transaction
from app.deps import get_assistant_db, require_login
from app.main import create_app
from app.repositories import assistant_repo
from app.timeutil import beijing_timestamp


def _client(
    *,
    conn: sqlite3.Connection,
    username: str,
    role: str = "Owner",
) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {
        "username": username,
        "display_name": username,
        "role": role,
    }

    def _assistant_db() -> Iterator[sqlite3.Connection]:
        yield conn

    app.dependency_overrides[get_assistant_db] = _assistant_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def assistant_conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    reset_assistant_init_state()
    conn = connect_assistant(f"sqlite:///{(tmp_path / 'assistant.db').as_posix()}")
    try:
        yield conn
    finally:
        conn.close()
        reset_assistant_init_state()


def test_assistant_conversation_lifecycle_persists_messages(
    assistant_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_bodies: list[dict[str, Any]] = []

    def fake_request_agent_payload(method: str, path: str, **kwargs: Any) -> tuple[int, dict]:
        assert method == "POST"
        assert path == "/api/v1/cicd-assistant"
        body = kwargs["body"]
        captured_bodies.append(body)
        return 200, {
            "answer": f"answer {len(captured_bodies)}",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "tools": ["query_images"],
            "available_tools": ["query_images"],
            "route": "agent_query_tools",
            "timings": {"total_ms": 1234},
            "tool_error": None,
            "state_delta": {"app_name": "hpcg", "intent": "query_image"},
        }

    monkeypatch.setattr(cicd_agent, "_request_agent_payload", fake_request_agent_payload)

    with _client(conn=assistant_conn, username="owner1") as client:
        created = client.post("/api/cicd-agent/assistant/conversations", json={"title": ""})
        assert created.status_code == 200
        conversation_id = created.json()["conversation"]["id"]

        first = client.post(
            f"/api/cicd-agent/assistant/conversations/{conversation_id}/messages",
            json={"message": "first question"},
        )
        assert first.status_code == 200
        first_data = first.json()
        assert first_data["conversation"]["title"] == "first question"
        assert [message["role"] for message in first_data["messages"]] == ["user", "assistant"]
        assert captured_bodies[0]["user_id"] == "owner1"
        assert captured_bodies[0]["history"] == []
        assert captured_bodies[0]["context"] == {"rolling_summary": "", "slots": {}}

        second = client.post(
            f"/api/cicd-agent/assistant/conversations/{conversation_id}/messages",
            json={"message": "second question"},
        )
        assert second.status_code == 200
        assert captured_bodies[1]["context"]["slots"] == {
            "app_name": "hpcg",
            "intent": "query_image",
        }
        assert captured_bodies[1]["history"] == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "answer 1"},
        ]

        detail = client.get(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert [message["content"] for message in detail.json()["messages"]] == [
            "first question",
            "answer 1",
            "second question",
            "answer 2",
        ]
        assert detail.json()["state"]["slots"]["app_name"] == "hpcg"

        listed = client.get("/api/cicd-agent/assistant/conversations")
        assert listed.status_code == 200
        assert listed.json()["conversations"][0]["message_count"] == 4

        deleted = client.delete(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        listed_after_delete = client.get("/api/cicd-agent/assistant/conversations")
        assert listed_after_delete.status_code == 200
        assert listed_after_delete.json()["conversations"] == []


def test_assistant_conversation_title_can_be_renamed_by_owner(
    assistant_conn: sqlite3.Connection,
) -> None:
    with _client(conn=assistant_conn, username="owner1") as client:
        created = client.post("/api/cicd-agent/assistant/conversations", json={"title": "旧标题"})
        assert created.status_code == 200
        conversation_id = created.json()["conversation"]["id"]

        renamed = client.patch(
            f"/api/cicd-agent/assistant/conversations/{conversation_id}",
            json={"title": "  amber   查询  "},
        )
        assert renamed.status_code == 200
        assert renamed.json()["conversation"]["title"] == "amber 查询"

        detail = client.get(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert detail.status_code == 200
        assert detail.json()["conversation"]["title"] == "amber 查询"


def test_assistant_conversation_title_cannot_be_renamed_by_other_user(
    assistant_conn: sqlite3.Connection,
) -> None:
    with _client(conn=assistant_conn, username="owner1") as owner_client:
        created = owner_client.post("/api/cicd-agent/assistant/conversations", json={})
        conversation_id = created.json()["conversation"]["id"]

    with _client(conn=assistant_conn, username="owner2") as other_client:
        renamed = other_client.patch(
            f"/api/cicd-agent/assistant/conversations/{conversation_id}",
            json={"title": "不该成功"},
        )
        assert renamed.status_code == 404


def test_assistant_state_rolls_older_messages_into_summary(
    assistant_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_trigger = cicd_agent.settings.assistant_summary_trigger_messages
    original_keep = cicd_agent.settings.assistant_summary_keep_messages
    monkeypatch.setattr(cicd_agent.settings, "assistant_summary_trigger_messages", 4)
    monkeypatch.setattr(cicd_agent.settings, "assistant_summary_keep_messages", 2)

    def fake_request_agent_payload(_method: str, _path: str, **kwargs: Any) -> tuple[int, dict]:
        body = kwargs["body"]
        return 200, {
            "answer": f"reply to {body['message']}",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "tools": [],
            "available_tools": [],
            "route": "plain_model_publish",
            "timings": {"total_ms": 800},
            "state_delta": {"app_version": "1.0"},
        }

    monkeypatch.setattr(cicd_agent, "_request_agent_payload", fake_request_agent_payload)

    try:
        with _client(conn=assistant_conn, username="owner1") as client:
            created = client.post("/api/cicd-agent/assistant/conversations", json={})
            conversation_id = created.json()["conversation"]["id"]

            for index in range(3):
                response = client.post(
                    f"/api/cicd-agent/assistant/conversations/{conversation_id}/messages",
                    json={"message": f"question {index + 1}"},
                )
                assert response.status_code == 200

            detail = client.get(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
            state = detail.json()["state"]
            assert state["slots"]["app_version"] == "1.0"
            assert state["summarized_until_sequence"] == 4
            assert "question 1" in state["rolling_summary"]
            assert "reply to question 1" in state["rolling_summary"]
            assert "question 3" not in state["rolling_summary"]
    finally:
        monkeypatch.setattr(
            cicd_agent.settings,
            "assistant_summary_trigger_messages",
            original_trigger,
        )
        monkeypatch.setattr(cicd_agent.settings, "assistant_summary_keep_messages", original_keep)


def test_assistant_stream_persists_final_message_and_state(
    assistant_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_body: dict[str, Any] = {}

    async def fake_stream_events(path: str, *, body: dict[str, Any]):
        assert path == "/api/v1/cicd-assistant/stream"
        captured_body.update(body)
        yield {
            "type": "start",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "available_tools": ["query_images"],
            "route": "agent_query_tools",
            "timings": {"routing_ms": 1},
            "state_delta": {"app_name": "hpcg"},
        }
        yield {
            "type": "status",
            "stage": "loading_tools",
            "message": "正在加载 hpc-query 查询工具",
            "route": "agent_query_tools",
            "timings": {"routing_ms": 1, "mcp_load_ms": 50},
        }
        yield {"type": "token", "content": "hello "}
        yield {"type": "tool", "name": "query_images"}
        yield {"type": "token", "content": "world"}
        yield {
            "type": "done",
            "answer": "hello world",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "tools": ["query_images"],
            "available_tools": ["query_images"],
            "route": "agent_query_tools",
            "timings": {"routing_ms": 1, "mcp_load_ms": 50, "total_ms": 1200},
            "state_delta": {"app_name": "hpcg", "intent": "query_images"},
        }

    monkeypatch.setattr(cicd_agent, "_request_agent_stream_events", fake_stream_events)

    with _client(conn=assistant_conn, username="owner1") as client:
        created = client.post("/api/cicd-agent/assistant/conversations", json={})
        conversation_id = created.json()["conversation"]["id"]

        with client.stream(
            "POST",
            f"/api/cicd-agent/assistant/conversations/{conversation_id}/messages/stream",
            json={"message": "帮我查询 hpcg app 最近发布的镜像"},
        ) as response:
            assert response.status_code == 200
            events = [json.loads(line) for line in response.iter_lines() if line]

        assert [event["type"] for event in events] == [
            "start",
            "metadata",
            "status",
            "token",
            "tool",
            "token",
            "done",
        ]
        assert captured_body["context"] == {"rolling_summary": "", "slots": {}}
        assert captured_body["history"] == []
        assert events[2]["stage"] == "loading_tools"
        assert events[3]["content"] == "hello "
        assert events[-1]["assistant_message"]["content"] == "hello world"
        assert events[-1]["assistant_message"]["metadata"]["route"] == "agent_query_tools"
        assert events[-1]["assistant_message"]["metadata"]["timings"]["total_ms"] == 1200
        assert events[-1]["state"]["slots"]["app_name"] == "hpcg"

        detail = client.get(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert [message["content"] for message in detail.json()["messages"]] == [
            "帮我查询 hpcg app 最近发布的镜像",
            "hello world",
        ]


def test_interrupted_answer_marks_partial_content() -> None:
    assert cicd_agent._interrupted_answer("hello world") == "hello world\n\n（已停止生成）"
    assert cicd_agent._interrupted_answer("") == "已停止生成，未产生可展示内容。"


def test_agent_stream_error_messages_are_human_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cicd_agent.settings, "cicd_agent_timeout_seconds", 7)

    assert cicd_agent._agent_stream_timeout_error() == "CICD Agent 请求超时（超过 7 秒）"
    assert (
        cicd_agent._agent_request_error(httpx.ConnectError("", request=httpx.Request("POST", "http://agent")))
        == "CICD Agent 不可用：ConnectError"
    )


def test_assistant_regenerate_appends_answer_without_duplicate_user(
    assistant_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_body: dict[str, Any] = {}
    now = beijing_timestamp()
    with transaction(assistant_conn):
        conversation = assistant_repo.create_conversation(
            assistant_conn,
            user_id="owner1",
            title="regen",
            created_at=now,
        )
        user_message = assistant_repo.add_message(
            assistant_conn,
            conversation_id=conversation["id"],
            role="user",
            content="帮我查询 hpcg app 最近发布的镜像",
            created_at=now,
        )
        assistant_message = assistant_repo.add_message(
            assistant_conn,
            conversation_id=conversation["id"],
            role="assistant",
            content="old answer",
            created_at=now,
        )

    async def fake_stream_events(path: str, *, body: dict[str, Any]):
        assert path == "/api/v1/cicd-assistant/stream"
        captured_body.update(body)
        yield {
            "type": "start",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "available_tools": ["query_images"],
        }
        yield {"type": "token", "content": "new answer"}
        yield {
            "type": "done",
            "answer": "new answer",
            "conversation_id": body["conversation_id"],
            "provider": "deepseek",
            "model": "deepseek-chat",
            "tools": ["query_images"],
            "available_tools": ["query_images"],
            "state_delta": {"last_query_summary": "new answer"},
        }

    monkeypatch.setattr(cicd_agent, "_request_agent_stream_events", fake_stream_events)

    with _client(conn=assistant_conn, username="owner1") as client:
        with client.stream(
            "POST",
            (
                "/api/cicd-agent/assistant/conversations/"
                f"{conversation['id']}/messages/{assistant_message['id']}/regenerate/stream"
            ),
            json={},
        ) as response:
            assert response.status_code == 200
            events = [json.loads(line) for line in response.iter_lines() if line]

        assert events[0]["source_user_message"]["id"] == user_message["id"]
        assert events[-1]["assistant_message"]["content"] == "new answer"
        assert captured_body["message"] == "帮我查询 hpcg app 最近发布的镜像"
        assert captured_body["history"] == []

        detail = client.get(f"/api/cicd-agent/assistant/conversations/{conversation['id']}")
        assert [message["content"] for message in detail.json()["messages"]] == [
            "帮我查询 hpcg app 最近发布的镜像",
            "old answer",
            "new answer",
        ]


def test_assistant_conversation_is_owned_by_user(
    assistant_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cicd_agent,
        "_request_agent_payload",
        lambda *_args, **kwargs: (
            200,
            {
                "answer": "ok",
                "conversation_id": kwargs["body"]["conversation_id"],
                "provider": "deepseek",
                "model": "deepseek-chat",
                "tools": [],
                "available_tools": [],
            },
        ),
    )

    with _client(conn=assistant_conn, username="owner1") as owner_client:
        created = owner_client.post("/api/cicd-agent/assistant/conversations", json={})
        conversation_id = created.json()["conversation"]["id"]

    with _client(conn=assistant_conn, username="owner2") as other_client:
        response = other_client.get(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert response.status_code == 404
        listed = other_client.get("/api/cicd-agent/assistant/conversations")
        assert listed.status_code == 200
        assert listed.json()["conversations"] == []
