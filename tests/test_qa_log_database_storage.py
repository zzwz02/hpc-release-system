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
    # Counted on the shared LLM meter, like every LLM feature.
    assert (result["llm"]["calls"], result["llm"]["finished"]) == (1, 1)
    assert result["llm"]["sent_chars"] > 0


def test_qa_job_snapshot_reports_elapsed_time_and_llm_totals() -> None:
    from app.services.qa_jobs import QaJobRegistry

    totals = {"calls": 1, "finished": 0, "received": 12}
    snapshot = QaJobRegistry._snapshot({
        "job_id": "j", "release_id": "r", "status": "completed", "started_at": 100.0,
        "finished_at": 142.4, "llm": totals,
    })

    assert snapshot["elapsed_seconds"] == 42
    assert snapshot["llm"] == totals


def test_a_running_analysis_is_shared_single_and_stoppable(temp_db_with_path, tmp_path, monkeypatch) -> None:
    import threading
    import time

    from app.integrations import llm
    from app.services import qa_jobs

    conn, db_path = temp_db_with_path
    release_id = seed_release(conn, tmp_path=db_path.parent)
    qa_service.upload_qa_log(
        conn, release_id, content_b64=base64.b64encode(b"run_make_test PASS").decode("ascii"),
        filename="qa.log", user="qa", role="QA",
    )
    conf = tmp_path / "release_system.conf"
    conf.write_text("[llm]\nLLM_BASE_URL = http://llm/v1\nLLM_MODEL = m\n", encoding="utf-8")
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(qa_jobs, "_registry", qa_jobs.QaJobRegistry())

    entered = threading.Event()

    def hanging_llm(_system: str, _user: str, cancel=None, **_kwargs) -> str:
        # Like a streamed reply that never ends unless its connection is cut.
        entered.set()
        cut = threading.Event()
        cancel.on_cancel(cut.set)
        if not cut.wait(30):
            raise AssertionError("the call was never cut")
        raise llm.LLMCancelled("LLM 调用已中止")

    monkeypatch.setattr(llm, "chat_json", hanging_llm)
    users = {
        "rm": {"username": "rm", "display_name": "rm", "role": "RM"},
        "qa": {"username": "qa", "display_name": "qa", "role": "QA"},
        "owner": {"username": "owner", "display_name": "owner", "role": "Owner"},
    }
    current = {"user": users["rm"]}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: current["user"]
    client = TestClient(app, raise_server_exceptions=False)

    job = client.post("/api/qa/analyze-log/start", json={"release_id": release_id}).json()
    assert entered.wait(5)

    # QA sees the analysis RM started, both directly and as the release's running one.
    current["user"] = users["qa"]
    assert client.get(f"/api/qa/analyze-log/status?job_id={job['job_id']}").json()["user"] == "rm"
    running = client.get(f"/api/qa/analyze-log/current?release_id={release_id}").json()["job"]
    assert (running["job_id"], running["status"], running["user"]) == (job["job_id"], "running", "rm")
    # Only one analysis per release at a time, whoever asks.
    refused = client.post("/api/qa/analyze-log/start", json={"release_id": release_id})
    assert refused.status_code == 400
    assert refused.json()["error"] == "该发布周期的 AI 分析正在进行中（rm 发起），可查看进度或中止"
    # Roles without QA editing can neither see nor stop it.
    current["user"] = users["owner"]
    assert client.post("/api/qa/analyze-log/cancel", json={"job_id": job["job_id"]}).status_code == 403
    assert client.get(f"/api/qa/analyze-log/current?release_id={release_id}").status_code == 403

    # QA stops RM's analysis; the hanging call is cut, not waited for.
    current["user"] = users["qa"]
    started = time.monotonic()
    stopping = client.post("/api/qa/analyze-log/cancel", json={"job_id": job["job_id"]}).json()
    assert stopping["stopping"] is True
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = client.get(f"/api/qa/analyze-log/status?job_id={job['job_id']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.02)
    assert (state["status"], state["message"]) == ("cancelled", "AI 分析已中止")
    assert time.monotonic() - started < 3
    assert client.get(f"/api/qa/analyze-log/current?release_id={release_id}").json() == {"job": None}
    assert client.post("/api/qa/analyze-log/cancel", json={"job_id": job["job_id"]}).json()["error"] == "AI 分析已经结束，无需中止"
    assert client.post("/api/qa/analyze-log/cancel", json={"job_id": "unknown"}).status_code == 404


def test_ai_suggestions_are_kept_until_reviewed_discarded_or_the_log_changes(temp_db_with_path, monkeypatch) -> None:
    conn, db_path = temp_db_with_path
    release_id = seed_release(conn, tmp_path=db_path.parent)
    app_id = next(iter(conn.execute("SELECT id FROM apps")))["id"]

    def upload(body: bytes) -> None:
        qa_service.upload_qa_log(
            conn, release_id, content_b64=base64.b64encode(body).decode("ascii"),
            filename="qa.log", user="qa", role="QA",
        )

    def analyse() -> None:
        analyze_qa_log(
            conn, release_id, analyzed_by="qa",
            llm_call=lambda _s, _p: json.dumps({"apps": [
                {"app_id": app_id, "qa_status": "has_issues", "qa_issue_note": "用例失败", "tests": []},
            ]}),
        )

    upload(b"run_make_test FAIL")
    analyse()
    saved = qa_service.get_ai_suggestions(conn, release_id)
    assert (saved["log_filename"], saved["analyzed_by"]) == ("qa.log", "qa")
    assert saved["result"]["apps"][0]["qa_issue_note"] == "用例失败"

    # A new log makes the old suggestions meaningless.
    upload(b"run_make_test PASS")
    assert qa_service.get_ai_suggestions(conn, release_id) is None

    # Saving QA statuses is the review: the suggestions are done with.
    analyse()
    qa_service.set_qa_status_batch(
        conn, release_id, [{"app_id": app_id, "status": "has_issues", "issue_note": "用例失败"}], user="qa", role="QA",
    )
    assert qa_service.get_ai_suggestions(conn, release_id) is None

    # Over the API: QA and RM read them, Owner does not; discarding is audited.
    analyse()
    monkeypatch.setattr(settings, "db_path", db_path)
    current = {"user": {"username": "rm", "display_name": "rm", "role": "RM"}}
    app = create_app()
    app.dependency_overrides[require_login] = lambda: current["user"]
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get(f"/api/qa/ai-suggestions?release_id={release_id}").json()["suggestions"]["analyzed_by"] == "qa"
    current["user"] = {"username": "o", "display_name": "o", "role": "Owner"}
    assert client.get(f"/api/qa/ai-suggestions?release_id={release_id}").status_code == 403
    assert client.post("/api/qa/ai-suggestions/discard", json={"release_id": release_id}).status_code == 403
    current["user"] = {"username": "qa", "display_name": "qa", "role": "QA"}
    assert client.post("/api/qa/ai-suggestions/discard", json={"release_id": release_id}).json() == {"ok": True}
    assert client.get(f"/api/qa/ai-suggestions?release_id={release_id}").json() == {"suggestions": None}
    events = [row["event"] for row in conn.execute("SELECT event FROM audit WHERE event = 'qa_discard_ai_suggestions'")]
    assert events == ["qa_discard_ai_suggestions"]
