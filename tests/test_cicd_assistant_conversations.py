from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.routers import cicd_agent
from app.db.assistant_connection import connect_assistant, reset_assistant_init_state
from app.deps import get_assistant_db, require_login
from app.main import create_app


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
            "tool_error": None,
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

        second = client.post(
            f"/api/cicd-agent/assistant/conversations/{conversation_id}/messages",
            json={"message": "second question"},
        )
        assert second.status_code == 200
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

        listed = client.get("/api/cicd-agent/assistant/conversations")
        assert listed.status_code == 200
        assert listed.json()["conversations"][0]["message_count"] == 4

        deleted = client.delete(f"/api/cicd-agent/assistant/conversations/{conversation_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        listed_after_delete = client.get("/api/cicd-agent/assistant/conversations")
        assert listed_after_delete.status_code == 200
        assert listed_after_delete.json()["conversations"] == []


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
