"""Attachment limits advertised to the browser and enforced on both write APIs."""
import base64

import pytest

from app.services import jira_agent_service as service
from tests.test_jira_agent import env, _detail, _done, _handover, _wait  # noqa: F401


def test_upload_limits_follow_backend_values(env, monkeypatch):
    expected = {"max_files": service.MAX_UPLOAD_FILES, "max_file_bytes": service.MAX_UPLOAD_BYTES}
    assert env.client.get("/api/jira-agent/issues/MC3-7672").json()["upload_limits"] == expected
    conversation = _handover(env)["conversation"]["id"]
    assert _detail(env, conversation)["upload_limits"] == expected
    monkeypatch.setattr(service, "MAX_UPLOAD_FILES", 3)
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 512)
    expected = {"max_files": 3, "max_file_bytes": 512}
    assert env.client.get("/api/jira-agent/issues/MC3-7672").json()["upload_limits"] == expected
    assert _detail(env, conversation)["upload_limits"] == expected


@pytest.mark.parametrize("followup", [False, True])
@pytest.mark.parametrize("count,size", [(11, 1), (2, 33)])
def test_invalid_uploads_do_not_create_turns_or_save_partial_files(env, monkeypatch, followup, count, size):
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 32)
    conversation = _handover(env)["conversation"]["id"] if followup else ""
    if followup:
        before = _wait(env, conversation, _done)
    files = [{"filename": f"file-{i}.txt", "content_base64": base64.b64encode(b"x" * (size if i else 1)).decode()}
             for i in range(count)]
    path = f"/api/jira-agent/conversations/{conversation}/messages" if followup else "/api/jira-agent/conversations"
    response = env.client.post(path, json={"issue_key": "MC3-7672", "text": "continue", "files": files})
    assert response.status_code == 400, response.text
    assert ("最多上传" if count > 10 else "file-1.txt") in response.text
    if followup:
        after = _detail(env, conversation)
        assert after["turns"] == before["turns"]
        assert after["files"] == before["files"]
    else:
        assert env.client.get("/api/jira-agent/conversations").json()["conversations"] == []


def test_decode_accepts_exact_size_and_count_limits():
    size = service.MAX_UPLOAD_BYTES
    files = [{"filename": f"file-{i}.txt", "content_base64": base64.b64encode(b"x" * (size if i == 0 else 1)).decode()}
             for i in range(service.MAX_UPLOAD_FILES)]
    decoded = service._decode_uploads(files)
    assert len(decoded) == service.MAX_UPLOAD_FILES
    assert len(decoded[0][1]) == size
    files[0]["content_base64"] = base64.b64encode(b"x" * (size + 1)).decode()
    with pytest.raises(ValueError, match="file-0.txt"):
        service._decode_uploads(files)
