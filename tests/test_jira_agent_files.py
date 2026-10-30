"""Artifact preview/download endpoints with isolated HTTP fixtures."""
import io
from zipfile import ZipFile
from app.config import settings
from app.db.connection import transaction
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_runner as execution
from tests.test_jira_agent import env, _handover, _wait, _done, _latest


def test_artifact_preview_zip_and_access_control(env):
    cid = _handover(env, post_comment=False)["conversation"]["id"]
    detail = _wait(env, cid, _done)
    turn = _latest(detail)
    cid = turn["conversation_id"]
    samples = {"report.md": b"# Result\nPASS", "kernel.cu": b"__global__ void kernel() {}",
               "test.cpp": b"int main() {}", "log.txt": b"PASS\n", "data.bin": b"\x00\x01\xff"}
    records = []
    with execution.open_db() as conn, transaction(conn):
        for name, data in samples.items():
            path = settings.jira_agent_data_dir / cid / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            records.append(repo.add_file(conn, conversation_id=cid, turn_id=turn["id"], direction="output",
                source="artifact", name=name, local_path=str(path), remote_path="artifacts/" + name, size=len(data)))
    for file, kind in zip(records, ["markdown", "code", "code", "text", "binary"]):
        url = f"/api/jira-agent/conversations/{cid}/files/{file['id']}/preview"
        response = env.client.get(url)
        assert response.status_code == 200 and response.json()["kind"] == kind
    zip_url = f"/api/jira-agent/turns/{turn['id']}/artifacts.zip"
    response = env.client.get(zip_url)
    assert response.status_code == 200
    with ZipFile(io.BytesIO(response.content)) as archive:
        for name, data in samples.items():
            assert archive.read("artifacts/" + name) == data
    env.as_user("bob")
    assert env.client.get(zip_url).status_code == 404
    assert env.client.get(url).status_code == 404
