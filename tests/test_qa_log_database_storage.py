from __future__ import annotations

import base64
import json
import sqlite3

from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import connect, reset_init_state
from app.deps import get_db, require_login
from app.main import create_app
from app.repositories import qa_repo
from app.services import qa_service
from app.services.qa_analysis_service import analyze_qa_log
from tests.conftest import seed_release


def test_upload_download_and_replace_use_database_blob(temp_db_with_path):
    conn, db_path = temp_db_with_path
    release_id = seed_release(conn, tmp_path=db_path.parent)

    first = qa_service.upload_qa_log(
        conn,
        release_id,
        content_b64=base64.b64encode(b"first log").decode("ascii"),
        filename="qa result.log",
        user="qa",
        role="QA",
    )

    assert first["filename"] == "qa_result.log"
    assert first["size_bytes"] == len(b"first log")
    assert "storage_path" not in first
    row = conn.execute(
        "SELECT content, storage_path FROM qa_logs WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    assert bytes(row["content"]) == b"first log"
    assert row["storage_path"] == ""
    assert not (db_path.parent / "qa_logs").exists()
    assert qa_service.get_qa_log_download(conn, release_id) == (
        b"first log",
        "qa_result.log",
    )

    qa_service.upload_qa_log(
        conn,
        release_id,
        content_b64=base64.b64encode(b"second log").decode("ascii"),
        filename="second.log",
        user="rm",
        role="RM",
    )
    assert qa_service.get_qa_log_download(conn, release_id) == (
        b"second log",
        "second.log",
    )


def test_http_upload_state_and_download_round_trip(temp_db_with_path, monkeypatch):
    conn, db_path = temp_db_with_path
    release_id = seed_release(conn, tmp_path=db_path.parent)
    monkeypatch.setattr(settings, "db_path", db_path)
    app = create_app()

    def override_db():
        request_conn = connect(db_path)
        try:
            yield request_conn
        finally:
            request_conn.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: {
        "username": "qa",
        "role": "QA",
        "display_name": "QA",
    }
    content = b"api round trip"
    client = TestClient(app, raise_server_exceptions=False)
    try:
        uploaded = client.post(
            "/api/qa/upload-log",
            json={
                "release_id": release_id,
                "filename": "api.log",
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
        )
        assert uploaded.status_code == 200
        assert uploaded.json()["size_bytes"] == len(content)
        assert "storage_path" not in uploaded.json()

        state = client.get("/api/state", params={"release_id": release_id})
        assert state.status_code == 200
        assert state.json()["qa_log"]["filename"] == "api.log"
        assert state.json()["qa_log"]["size_bytes"] == len(content)

        downloaded = client.get(
            "/api/qa-log/download",
            params={"release_id": release_id},
        )
        assert downloaded.status_code == 200
        assert downloaded.content == content
        assert 'filename="api.log"' in downloaded.headers["content-disposition"]
    finally:
        client.close()


def test_startup_imports_legacy_qa_log_file_without_deleting_it(tmp_path):
    db_path = tmp_path / "legacy.db"
    log_path = tmp_path / "qa_logs" / "r1__legacy.log"
    log_path.parent.mkdir()
    log_path.write_bytes(b"legacy file body")

    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE releases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            maca_version TEXT NOT NULL DEFAULT '',
            app_freeze_deadline TEXT NOT NULL DEFAULT '',
            doc_deadline TEXT NOT NULL DEFAULT '',
            released_locked INTEGER NOT NULL DEFAULT 0,
            released_locked_at TEXT NOT NULL DEFAULT '',
            released_locked_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cloned_from TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE qa_logs (
            release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT NOT NULL
        );
        INSERT INTO releases(id, name, created_at, source)
        VALUES ('r1', 'legacy', '2026-01-01 00:00:00', 'manual');
        """
    )
    legacy.execute(
        "INSERT INTO qa_logs VALUES (?, ?, ?, ?, ?)",
        ("r1", "legacy.log", str(log_path), "2026-01-02 03:04:05", "qa"),
    )
    legacy.commit()
    legacy.close()

    reset_init_state()
    conn = connect(db_path)
    try:
        assert qa_repo.get_qa_log_content(conn, "r1") == (
            b"legacy file body",
            "legacy.log",
        )
        assert qa_service.get_qa_log_download(conn, "r1") == (
            b"legacy file body",
            "legacy.log",
        )
        assert log_path.exists()
    finally:
        conn.close()
        reset_init_state()


def test_ai_analysis_reads_log_body_from_database(temp_db_with_path):
    conn, db_path = temp_db_with_path
    release_id = seed_release(conn, tmp_path=db_path.parent)
    app_id = next(iter(conn.execute("SELECT id FROM apps")))["id"]
    qa_service.upload_qa_log(
        conn,
        release_id,
        content_b64=base64.b64encode(b"run_make_test PASS").decode("ascii"),
        filename="qa.log",
        user="qa",
        role="QA",
    )

    def fake_llm(_system: str, payload: str) -> str:
        assert "run_make_test PASS" in json.loads(payload)["log"]
        return json.dumps(
            {
                "apps": [
                    {
                        "app_id": app_id,
                        "qa_status": "qa_passed",
                        "qa_issue_note": "",
                        "tests": [],
                    }
                ]
            }
        )

    result = analyze_qa_log(conn, release_id, llm_call=fake_llm)

    assert result["apps"][0]["qa_status"] == "qa_passed"
    assert result["log_chars"] == len(b"run_make_test PASS")
