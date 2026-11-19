"""QA CSV REST downloads match the display and the complete upload template."""
from copy import deepcopy
import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import qa_commands_table, qa_results
from app.main import create_app
from app.services import qa_commands_service


@pytest.fixture()
def result():
    row = {**dict.fromkeys(qa_results.WORKSHEET_COLUMNS, ""), "app_id": "pyg", "git_branch": "maca",
           "hpc_version": "3.10.1.2", "app_version": "1.0", "arch": "x86", "OS": "ubuntu24.04",
           "py_label": "py312", "torch_label": "torch2.8", "test_name": "sanity", "perf_golden": "0",
           "website_docker_cmd": 'docker run image sh -c \'printf "a,b"\\n\''}
    rows = [{**row, "GPU": chip} for chip in ("C500", "C600", "X301")]
    rows.extend([{**row, "GPU": "C500", "py_label": "py310"},
                 {**row, "GPU": "", "test_name": "missing", "hpc_version": "",
                  "website_docker_cmd": "docker run image:<hpc_version> sh -c test"}])
    return {"columns": qa_results.WORKSHEET_COLUMNS, "rows": rows, "errors": [],
            "hpc_version": "latest", "hpc_versions": ["3.10.1.2"]}


def parse(content):
    return list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))


def test_display_export_groups_gpu_and_hides_labels_without_collapsing_builds(result):
    original = deepcopy(result)
    content = qa_commands_table.export_csv(result)
    rows = parse(content)
    assert content.startswith("\ufeffapp_id,")
    assert len(rows) == 3
    assert rows[0]["GPU"] == "C500, C600, X301"
    assert "py_label" not in rows[0] and "torch_label" not in rows[0]
    assert rows[0]["website_docker_cmd"] == result["rows"][0]["website_docker_cmd"]
    assert rows[0]["perf_golden"] == "0"
    assert rows[-1]["GPU"] == rows[-1]["hpc_version"] == ""
    assert "<hpc_version>" in rows[-1]["website_docker_cmd"]
    assert result == original


def test_template_export_preserves_all_columns_and_individual_gpu_rows(result):
    rows = parse(qa_commands_table.export_csv(result, template=True))
    assert list(rows[0]) == qa_results.WORKSHEET_COLUMNS
    assert [row["GPU"] for row in rows[:3]] == ["C500", "C600", "X301"]
    assert rows[0]["py_label"] == "py312" and rows[3]["py_label"] == "py310"
    assert all(row[column] == "" for row in rows for column in qa_results.WORKSHEET_QA_COLUMNS)


@pytest.fixture()
def client(temp_db, monkeypatch, result):
    def query(conn, *, release_id, hpc_version):
        assert conn is temp_db and release_id == "release1"
        return {**result, "hpc_version": hpc_version}
    monkeypatch.setattr(qa_commands_service, "query", query)
    identity = {"username": "qa", "role": "QA"}
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: identity
    with TestClient(app) as http:
        yield http, identity


@pytest.mark.parametrize("role,status", [("QA", 200), ("RM", 200), ("Owner", 200), ("Guest", 200), ("Admin", 403), ("SPD", 403)])
def test_endpoint_uses_existing_qa_read_permissions_and_default_latest(client, role, status):
    http, identity = client
    identity["role"] = role
    response = http.get("/api/qa/test-commands/export", params={"release_id": "release1"})
    assert response.status_code == status
    if status == 200:
        assert response.content.startswith(b"\xef\xbb\xbf")
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == 'attachment; filename="test_commands_latest.csv"'
        assert response.headers["cache-control"] == "no-store"
        assert parse(response.text)[0]["GPU"] == "C500, C600, X301"


def test_specific_version_and_template_download_do_not_change_json_contract(client):
    http, _identity = client
    response = http.get("/api/qa/test-commands/export", params={
        "release_id": "release1", "hpc_version": "3.10.1.2", "template": "true",
    })
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="test_commands_3.10.1.2_template.csv"'
    assert len(parse(response.text)) == 5
    assert parse(response.text)[0]["py_label"] == "py312"
    original = http.get("/api/qa/test-commands", params={"release_id": "release1"})
    assert original.status_code == 200
    assert len(original.json()["rows"]) == 5
    assert original.json()["columns"] == qa_results.WORKSHEET_COLUMNS


def test_missing_release_and_invalid_template_are_rejected(client):
    http, _identity = client
    assert http.get("/api/qa/test-commands/export").status_code == 422
    assert http.get("/api/qa/test-commands/export", params={"release_id": "release1", "template": "wrong"}).status_code == 422


@pytest.mark.parametrize("template", [False, True])
def test_resolution_errors_do_not_silently_download_partial_csv(client, result, template):
    result["errors"].append({"image": "missing-image", "error": "Gerrit unavailable"})
    http, _identity = client
    response = http.get("/api/qa/test-commands/export", params={"release_id": "release1", "template": template})
    assert response.status_code == 502
    assert "Gerrit unavailable" in response.text
    assert "content-disposition" not in response.headers


def test_missing_images_and_empty_scopes_remain_downloadable(client, result):
    http, _identity = client
    result["rows"] = [result["rows"][-1]]
    result["hpc_versions"] = []
    response = http.get("/api/qa/test-commands/export", params={"release_id": "release1"})
    assert response.status_code == 200
    assert "<hpc_version>" in parse(response.text)[0]["website_docker_cmd"]
    result["rows"] = []
    response = http.get("/api/qa/test-commands/export", params={"release_id": "release1"})
    assert response.status_code == 200
    assert parse(response.text) == []
    assert response.text.startswith("\ufeffapp_id,")


def test_upstream_failure_is_returned_as_an_error(client, monkeypatch):
    def fail(*args, **kwargs):
        raise ApiError(503, "Harbor unavailable")
    monkeypatch.setattr(qa_commands_service, "query", fail)
    http, _identity = client
    response = http.get("/api/qa/test-commands/export", params={"release_id": "release1"})
    assert response.status_code == 503
    assert "Harbor unavailable" in response.text
    assert "content-disposition" not in response.headers
