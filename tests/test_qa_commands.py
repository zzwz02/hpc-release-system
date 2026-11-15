"""Version worksheet -> QA Excel -> live validation -> saved report."""
from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.errors import ApiError
from app.deps import get_db, require_login
from app.domain import qa_results
from app.domain.test_platforms import release_tests
from app.integrations import harbor
from app.main import create_app
from app.services import qa_commands_service, qa_results_service
from tests.conftest import seed_cycle, seed_signup
from tests.test_image_lookup import DIGEST, IMAGE, TAGS

TAGGED_IMAGE = IMAGE.replace("@", ":" + TAGS[2] + "@")
from tests.test_qa_results import live as live


@pytest.fixture()
def scope(temp_db, live, monkeypatch):
    release_id = seed_cycle(temp_db, name="3.10.1")
    seed_signup(temp_db, release_id, "testapp")
    state = {"versions": ["3.10.1.2"], "missing": False}

    def listing(project, repository, version):
        assert not temp_db.in_transaction
        if repository != "abacus-maca" or state["missing"]:
            return []
        return [{"digest": DIGEST, "tags": [{"name": tag.replace("3.10.1.2", item)}
                 for item in state["versions"] for tag in TAGS]}]

    monkeypatch.setattr(harbor, "list_artifacts", listing)
    return release_id, state


def filled_row(temp_db, scope):
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    row = next(row for row in result["rows"] if row["website_docker_cmd"] != "no_image")
    return {**row, "qa_digest": IMAGE, "qa_docker_cmd": row["website_docker_cmd"],
            "qa_result": 0, "qa_env": "C500_server_1", "qa_status": "pass", "qa_comment": "中文备注\n第二行"}


def upload(conn, rows, *, columns=None):
    book = Workbook()
    sheet = book.active
    columns = qa_results.WORKSHEET_COLUMNS if columns is None else columns
    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(column, "") for column in columns])
    env = book.create_sheet("C500_server_1")
    env.merge_cells("A1:C3")
    env["A1"] = "原样保留"
    env["B8"] = "=1+2"
    content = io.BytesIO()
    book.save(content)
    payload = content.getvalue()
    result = qa_results_service.upload(conn, content_b64=base64.b64encode(payload).decode(),
                                      filename="report.xlsx", user="qa", role="QA")
    return result, payload


def test_exact_version_preserves_missing_builds_and_all_qa_cells_blank(temp_db, scope):
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    assert result["columns"] == qa_results.WORKSHEET_COLUMNS
    assert list(result["rows"][0]) == result["columns"]
    real = [row for row in result["rows"] if row["website_docker_cmd"] != "no_image"]
    assert len(real) == 3
    assert [(row["test_name"], row["GPU"]) for row in real] == [
        ("nested.daily", "C500"), ("sanity", "C500"), ("sanity", "X201")]
    assert all(TAGGED_IMAGE in row["website_docker_cmd"] for row in real)
    assert any(row["arch"] == "arm" and row["website_docker_cmd"] == "no_image" for row in result["rows"])
    assert any(row["OS"] == "ubuntu24.04" and row["website_docker_cmd"] == "no_image" for row in result["rows"])
    for row in result["rows"]:
        assert row["hpc_version"] == "3.10.1.2"
        assert all(row[column] == "" for column in qa_results.WORKSHEET_QA_COLUMNS)


@pytest.mark.parametrize("requested", ["3.10.1.2", "latest"])
def test_version_queries_only_read_maca_and_ignore_newer_hpcc_images(temp_db, scope, monkeypatch, requested):
    original = harbor.list_artifacts
    visited = []
    def listing(project, repository, version):
        visited.append(repository)
        if repository.endswith(("-hpcc", "-hpcc-dbg")):
            return [{"digest": DIGEST, "tags": [{"name": tag.replace("3.10.1.2", "3.10.1.99")} for tag in TAGS]}]
        return original(project, repository, version)
    monkeypatch.setattr(harbor, "list_artifacts", listing)
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version=requested)
    assert result["hpc_version"] == "3.10.1.2"
    assert set(visited) == {"abacus-maca", "abacus-maca-dbg"}
    assert not result["errors"]
    assert any(TAGGED_IMAGE in row["website_docker_cmd"] for row in result["rows"])


def test_hpcc_only_artifact_cannot_fill_a_missing_maca_image(temp_db, scope, monkeypatch):
    def listing(project, repository, version):
        if repository.endswith(("-hpcc", "-hpcc-dbg")):
            return [{"digest": DIGEST, "tags": [{"name": tag} for tag in TAGS]}]
        return []
    monkeypatch.setattr(harbor, "list_artifacts", listing)
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    assert result["rows"] and all(row["website_docker_cmd"] == "no_image" for row in result["rows"])
    with pytest.raises(ValueError, match="没有.*可用镜像"):
        qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="latest")


def test_maca_debug_images_are_still_resolved(temp_db, scope, monkeypatch):
    original = harbor.list_artifacts
    def listing(project, repository, version):
        if repository == "abacus-maca-dbg":
            return [{"digest": "sha256:" + "9d" * 32, "tags": [{"name": tag} for tag in TAGS]}]
        return original(project, repository, version)
    monkeypatch.setattr(harbor, "list_artifacts", listing)
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    debug = [row for row in result["rows"] if row["test_name"] == "debug"]
    assert debug and all("abacus-maca-dbg:" in row["website_docker_cmd"] for row in debug)


def test_website_command_uses_actual_numbered_tag_when_standard_alias_is_absent(temp_db, scope, monkeypatch):
    tags = [TAGS[0], TAGS[1], TAGS[1].replace("3012", "9"),
            TAGS[2].replace("ubuntu22.04", "ubuntu24.04")]
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {"tags": [{"name": tag} for tag in reversed(tags)]})
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    expected = IMAGE.replace("@", ":" + TAGS[1] + "@")
    assert all(expected in row["website_docker_cmd"] for row in result["rows"]
               if row["website_docker_cmd"] != "no_image")


def test_website_tag_changes_only_image_operand_preserving_env_and_quoted_script(temp_db, scope, live):
    from app.domain.test_commands import docker_cmd
    from app.services import lookup_service

    cfg = live[0]["app_test"]["sanity"]
    cfg["container_args"] = f"--env 'NOTE= {IMAGE} sh -c text' --device=/dev/dri -u 0"
    cfg["test_cmd"] = f"printf '%s' \" {IMAGE} sh -c test\""
    result = lookup_service.by_image(temp_db, IMAGE)
    rows = qa_results.worksheet_commands(result, hpc_version="3.10.1.2")
    assert next(row["website_docker_cmd"] for row in rows if row["test_name"] == "sanity") == docker_cmd(cfg, TAGGED_IMAGE)


@pytest.mark.parametrize("changed_tags", [False, True])
def test_latest_uses_numeric_patch_order_and_stays_inside_selected_cycle(temp_db, scope, monkeypatch, changed_tags):
    scope[1]["versions"] = ["3.10.1.9", "3.10.1.10", "3.10.2.99", "13.10.1.20"]
    if not changed_tags:
        monkeypatch.setattr(harbor, "get_artifact", lambda *args: {
            "tags": [{"name": tag.replace("3.10.1.2", version)}
                     for version in scope[1]["versions"] for tag in TAGS]})
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="latest")
    assert result["hpc_version"] == "3.10.1.10"
    if changed_tags:
        assert result["errors"] and "tag 已变化" in result["errors"][0]["error"]
    else:
        assert not result["errors"]
        assert any(IMAGE.replace("@", ":" + TAGS[2].replace("3.10.1.2", "3.10.1.10") + "@") in row["website_docker_cmd"] for row in result["rows"])
        assert all(row["hpc_version"] == "3.10.1.10" for row in result["rows"])


def test_missing_exact_version_keeps_whole_cartesian_scope(temp_db, scope):
    scope[1]["missing"] = True
    result = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.7")
    assert len(result["rows"]) > 3
    assert all(row["website_docker_cmd"] == "no_image" for row in result["rows"])
    with pytest.raises(ValueError, match="没有.*可用镜像"):
        qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="latest")


def test_version_export_upload_roundtrip_and_download_keeps_environment_bytes(temp_db, scope):
    row = filled_row(temp_db, scope)
    result, original = upload(temp_db, [row])
    report = qa_results_service.query(temp_db)
    saved = report["rows"][0]
    assert report["columns"] == qa_results.WORKSHEET_COLUMNS
    for column in qa_results.WORKSHEET_COLUMNS:
        assert saved[column] == row[column]
    assert saved["qa_digest_gerrit_commit_id"] == "bb46c60"
    assert qa_results_service.download(temp_db, result["report_id"])[0] == original
    # A saved result never pre-fills any qa_* cell in a fresh download.
    fresh = qa_commands_service.query(temp_db, release_id=scope[0], hpc_version="3.10.1.2")
    assert all(all(item[column] == "" for column in qa_results.WORKSHEET_QA_COLUMNS) for item in fresh["rows"])


def test_hpc_patches_are_saved_independently_and_exact_reupload_replaces_only_one(temp_db, scope, monkeypatch):
    first = filled_row(temp_db, scope)
    monkeypatch.setattr(harbor, "get_artifact", lambda *args: {"tags": [{"name": tag}
        for tag in TAGS + [tag.replace("3.10.1.2", "3.10.1.3") for tag in TAGS]]})
    second = {**first, "hpc_version": "3.10.1.3", "qa_result": "fail"}
    saved, _ = upload(temp_db, [first, second])
    assert saved["rows"] == 2 and saved["duplicate_rows"] == 0
    assert qa_results_service.query(temp_db)["total"] == 2
    updated, _ = upload(temp_db, [{**first, "qa_result": 42}])
    assert updated["replaced"] == 1
    rows = qa_results_service.query(temp_db)["rows"]
    assert {(row["hpc_version"], row["qa_result"]) for row in rows} == {("3.10.1.2", 42), ("3.10.1.3", "fail")}
    for row in rows:
        assert f"-{row['hpc_version']}-ubuntu22.04-amd64@{DIGEST}" in row["website_docker_cmd"]


def test_python_and_torch_combinations_are_saved_independently(temp_db, scope, live, monkeypatch):
    base = filled_row(temp_db, scope)
    raw = live[0]
    raw["app_build"]["ubuntu22.04_amd64"].update(python_label="py310,py312", pytorch_label="torch2.4,torch2.8")
    def artifact(project, repository, digest):
        py, torch = ("py310", "torch2.4") if digest == DIGEST else ("py312", "torch2.8")
        return {"digest": digest, "tags": [{"name": tag.replace("-ubuntu22.04", f"-{torch}-{py}-ubuntu22.04")} for tag in TAGS]}
    monkeypatch.setattr(harbor, "get_artifact", artifact)
    other = IMAGE.replace("8d" * 32, "9d" * 32)
    first = {**base, "py_label": "py310", "torch_label": "torch2.4",
             "qa_docker_cmd": "docker run " + IMAGE.replace("@", ":" + TAGS[2].replace("-ubuntu22.04", "-torch2.4-py310-ubuntu22.04") + "@") + " sh -c test"}
    second = {**base, "py_label": "py312", "torch_label": "torch2.8", "qa_digest": other,
              "qa_docker_cmd": "docker run " + other.replace("@", ":" + TAGS[2].replace("-ubuntu22.04", "-torch2.8-py312-ubuntu22.04") + "@") + " sh -c test"}
    result, _ = upload(temp_db, [first, second])
    assert result["rows"] == 2 and result["duplicate_rows"] == 0
    rows = qa_results_service.query(temp_db)["rows"]
    assert {(row["py_label"], row["torch_label"]) for row in rows} == {("py310", "torch2.4"), ("py312", "torch2.8")}
    assert all(row["website_docker_cmd"] for row in rows)


def test_exact_legacy_record_is_converted_on_upload_without_losing_workbooks(temp_db, scope):
    row = filled_row(temp_db, scope)
    first, original = upload(temp_db, [row])
    legacy = {**row, "commit_id": "bb46c60"}
    temp_db.execute("UPDATE qa_test_results SET result_key=?, data_json=?",
                    (qa_results.result_key(row), json.dumps(legacy)))
    temp_db.commit()
    second, _ = upload(temp_db, [{**row, "qa_result": "fail"}])
    assert second["replaced"] == 1
    assert qa_results_service.query(temp_db)["total"] == 1
    assert qa_results_service.download(temp_db, first["report_id"])[0] == original


@pytest.mark.parametrize("column,value", [
    ("hpc_version", "3.10.1.9"), ("GPU", "C500X"), ("OS", "ubuntu24.04"),
    ("arch", "arm"), ("test_name", "not-a-test"), ("app_name", "wrong"),
    ("git_branch", "wrong"), ("app_version", "99"), ("py_label", "py999"),
    ("torch_label", "torch99"), ("perf_golden", "incorrect"), ("perf_unit", "incorrect"),
    ("qa_env", "missing"), ("qa_status", "pending"),
    ("qa_digest", "bad-digest"),
])
def test_upload_dimensions_are_validated_atomically(temp_db, scope, column, value):
    row = filled_row(temp_db, scope)
    upload(temp_db, [row])
    with pytest.raises(ValueError, match="第 3 行"):
        upload(temp_db, [row, {**row, column: value}])
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_reports").fetchone()[0] == 1
    assert qa_results_service.query(temp_db)["rows"][0]["qa_result"] == 0


@pytest.mark.parametrize("column", qa_results.WORKSHEET_METADATA_COLUMNS)
def test_all_metadata_headers_required_even_without_website_column(temp_db, scope, column):
    row = filled_row(temp_db, scope)
    columns = [item for item in qa_results.WORKSHEET_COLUMNS if item not in (column, "website_docker_cmd")]
    with pytest.raises(ValueError, match="缺少列.*" + column):
        upload(temp_db, [row], columns=columns)
    assert temp_db.execute("SELECT COUNT(*) FROM qa_test_reports").fetchone()[0] == 0


@pytest.mark.parametrize("golden", [0, 0.0, "0", "0.000", "0e0"])
def test_numeric_zero_performance_baseline_survives_excel_formatting(temp_db, scope, live, golden):
    for _, cfg in release_tests(live[0]):
        cfg.update(perf_golden=0, perf_unit="seconds")
    row = filled_row(temp_db, scope)
    assert row["perf_golden"] == "0"
    upload(temp_db, [{**row, "perf_golden": golden}])
    saved = qa_results_service.query(temp_db)["rows"][0]
    assert saved["perf_golden"] == "0" and saved["perf_unit"] == "seconds"


@pytest.mark.parametrize("field", ["perf_golden", "perf_unit"])
def test_declared_performance_metadata_cannot_be_erased(temp_db, scope, live, field):
    for _, cfg in release_tests(live[0]):
        cfg.update(perf_golden="max", perf_unit="GFlops")
    row = filled_row(temp_db, scope)
    with pytest.raises(ValueError, match="第 2 行.*" + field):
        upload(temp_db, [{**row, field: ""}])


@pytest.mark.parametrize("untrusted", ["docker run uploaded-command", "=1+2", "no_image"])
def test_uploaded_website_command_is_ignored_and_query_always_reads_live_sources(temp_db, scope, live, untrusted):
    row = filled_row(temp_db, scope)
    expected = row["website_docker_cmd"]
    saved, original = upload(temp_db, [{**row, "website_docker_cmd": untrusted}])
    stored = json.loads(temp_db.execute("SELECT data_json FROM qa_test_results").fetchone()[0])
    assert stored["website_docker_cmd"] == expected
    assert qa_results_service.query(temp_db)["rows"][0]["website_docker_cmd"] == expected
    cfg = dict(release_tests(live[0]))[row["test_name"]]
    cfg["test_cmd"] = "live command after upload"
    report = qa_results_service.query(temp_db)["rows"][0]
    assert "live command after upload" in report["website_docker_cmd"]
    assert report["qa_docker_cmd"] == row["qa_docker_cmd"]
    assert qa_results_service.download(temp_db, saved["report_id"])[0] == original


@pytest.mark.parametrize("operand,valid", [
    (IMAGE, True), (IMAGE.split("@")[0] + ":" + TAGS[1], True),
    (IMAGE.replace("8d" * 32, "9d" * 32), False),
    (IMAGE.replace("abacus-maca", "abacus-maca-dbg"), False),
    (IMAGE.replace("@", ":not-a-tag@"), False),
])
def test_qa_command_must_identify_same_live_artifact(temp_db, scope, operand, valid):
    row = {**filled_row(temp_db, scope), "qa_docker_cmd": f"docker run --rm {operand} sh -c test"}
    if valid:
        upload(temp_db, [row])
    else:
        with pytest.raises(ValueError, match="第 2 行.*qa_docker_cmd"):
            upload(temp_db, [row])


def test_real_http_payload_and_no_store_contract(temp_db, scope):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "qa", "role": "QA"}
    with TestClient(app) as client:
        response = client.get("/api/qa/test-commands", params={"release_id": scope[0], "hpc_version": "3.10.1.2"})
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["columns"] == qa_results.WORKSHEET_COLUMNS


def test_harbor_artifact_pagination_404_and_failure(monkeypatch):
    real_client = httpx.Client
    state = {"status": 200}
    def handler(request):
        assert request.url.params["q"] == "tags=~3.10.1"
        assert request.url.params["with_tag"] == "true"
        assert "team%252Fhpl" in request.url.raw_path.decode()
        data = [{"digest": "sha256:" + "a" * 64}] if request.url.params["page"] == "1" else []
        return httpx.Response(state["status"], json=data)
    monkeypatch.setattr(harbor, "ARTIFACT_PAGE_SIZE", 1)
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    assert len(harbor.list_artifacts("hpc-release", "team/hpl", "3.10.1")) == 1
    state["status"] = 404
    assert harbor.list_artifacts("hpc-release", "team/hpl", "3.10.1") == []
    state["status"] = 503
    with pytest.raises(ApiError, match="503"):
        harbor.list_artifacts("hpc-release", "team/hpl", "3.10.1")
