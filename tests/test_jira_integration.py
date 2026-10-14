from __future__ import annotations

import asyncio

from app.api.routers import cicd as cicd_router
from app.config import settings
from app.db.connection import transaction
from app.integrations import jira
from app.services import cicd_service
from tests.test_cicd_ruling_bc import APP_ID, submit_create, approve


def _write_jira_section(tmp_path, monkeypatch, base_url: str):
    config_path = tmp_path / "release_system.conf"
    config_path.write_text(
        f"[jira]\nJIRA_BASE_URL = {base_url}\nJIRA_TOKEN = secret-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_conf_path", config_path)


def test_browse_url_uses_jira_section_without_exposing_token(tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "https://jira.example.test/")

    assert jira.browse_url() == "https://jira.example.test/browse/"


def test_cicd_public_config_returns_only_browser_safe_jira_url(tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "https://jira.example.test")

    result = cicd_router.get_config(user={"username": "rm", "role": "RM"})

    assert result == {"jira_browse_url": "https://jira.example.test/browse/"}
    assert "secret-token" not in repr(result)


class _JsonRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self) -> dict:
        return self._body


def _approve_with_auto_jira(conn, request_id: int) -> dict:
    body = {"request_id": request_id, "approval_mode": "dispatch_spd", "jira_auto_created": 1}
    return asyncio.run(cicd_router.post_approve(
        request=_JsonRequest(body), user={"username": "rm", "role": "RM"}, conn=conn,
    ))


def test_approve_reports_missing_jira_config_without_blocking(temp_db):
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert result["request"]["status"] == "approved"
    assert result["jira_error"].startswith("未配置 JIRA")


def test_approve_reports_jira_create_failure_and_files_under_fixed_project(temp_db, tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    created: dict = {}

    def fail_create(title, desc, *, jira_config):
        created["config"] = jira_config
        raise RuntimeError("component SPD_CICD not found")

    monkeypatch.setattr(jira, "create_issue", fail_create)
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert result["request"]["status"] == "approved"
    assert result["jira_error"] == "component SPD_CICD not found"
    assert created["config"]["JIRA_BASE_URL"] == "http://jira"
    assert (jira.DISPATCH_PROJECT, jira.DISPATCH_COMPONENT) == ("SPD", "SPD_CICD")


def test_approve_success_has_no_jira_error(temp_db, tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    monkeypatch.setattr(jira, "create_issue", lambda title, desc, *, jira_config: "SPD-9")
    req = submit_create(temp_db)

    result = _approve_with_auto_jira(temp_db, req["id"])

    assert "jira_error" not in result
    assert result["request"]["jira_id"] == "SPD-9"


def test_auto_jira_description_puts_task_context_in_basic_info(temp_db, tmp_path, monkeypatch):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    create_req = submit_create(temp_db)
    approve(temp_db, create_req["id"])
    with transaction(temp_db):
        stop_req = cicd_service.sync_decision_to_cicd(
            temp_db,
            APP_ID,
            "stopped",
            submitter="rm",
            current_status_override="Running",
        )
    captured: dict = {}

    def fake_create(title, desc, *, jira_config):
        captured["title"] = title
        captured["description"] = desc
        return "SPD-10"

    monkeypatch.setattr(jira, "create_issue", fake_create)

    result = _approve_with_auto_jira(temp_db, stop_req["id"])

    assert result["request"]["jira_id"] == "SPD-10"
    assert captured["title"] == "[Stop] RulingBCTestApp 【停止发布 main 分支】"
    basic, detail = captured["description"].split("h3. 变更详情", 1)
    assert "|项目名称|RulingBCTestApp|" in basic
    assert "|仓库名|hpc_ruling_bc|" in basic
    assert "|分支|main|" in basic
    assert basic.index("|项目名称|") < basic.index("|仓库名|") < basic.index("|分支|")
    assert "|状态|Running|Stopped|" in detail


def test_auto_jira_repo_branch_modify_keeps_old_context_and_prioritizes_change_detail(
    temp_db,
    tmp_path,
    monkeypatch,
):
    _write_jira_section(tmp_path, monkeypatch, "http://jira")
    create_req = submit_create(temp_db)
    approve(temp_db, create_req["id"])
    modify_req = cicd_service.submit_request(
        temp_db,
        task_id=APP_ID,
        request_type="modify",
        payload={
            "notes": {"old": "old note", "new": "new note"},
            "branch": {"old": "main", "new": "develop"},
            "repo_name": {
                "old": "ssh://gerrit/PDE/HPC/hpc_ruling_bc",
                "new": "hpc_ruling_bc_next",
            },
        },
        submitter="owner_test",
        submitter_role="Owner",
        source="app_workbench",
    )
    captured: dict = {}

    def fake_create(title, desc, *, jira_config):
        captured["title"] = title
        captured["description"] = desc
        return "SPD-11"

    monkeypatch.setattr(jira, "create_issue", fake_create)

    result = _approve_with_auto_jira(temp_db, modify_req["id"])

    assert result["request"]["jira_id"] == "SPD-11"
    assert captured["title"] == "[Change] RulingBCTestApp 【修改项目】"
    basic, detail = captured["description"].split("h3. 变更详情", 1)
    assert "|仓库名|hpc_ruling_bc|" in basic
    assert "|分支|main|" in basic
    repo_row = "|仓库名|hpc_ruling_bc|hpc_ruling_bc_next|"
    branch_row = "|分支|main|develop|"
    notes_row = "|备注|old note|new note|"
    assert repo_row in detail
    assert branch_row in detail
    assert notes_row in detail
    assert detail.index(repo_row) < detail.index(branch_row) < detail.index(notes_row)


def test_build_description_includes_version_repo_and_branch_at_end_of_basic_info():
    desc = jira.build_description(
        request_id=131,
        request_type="modify",
        payload={"status": {"old": "Running", "new": "Stopped"}},
        task_id="ginkgo",
        submitter="zhawu",
        title="[Change] Ginkgo 【修改项目】",
        task_context={
            "app_name": "Ginkgo",
            "app_version": "v1.9.0",
            "repo_type": "git",
            "repo_name": "ssh://gerrit/PDE/HPC/hpc_ginkgo",
            "branch": "maca",
        },
    )

    basic = desc.split("h3. 变更详情", 1)[0]
    assert "|项目名称|Ginkgo|" in basic
    assert "|版本|v1.9.0|" in basic
    assert "|仓库名|hpc_ginkgo|" in basic
    assert "|分支|maca|" in basic
    assert basic.index("|版本|") < basic.index("|仓库名|") < basic.index("|分支|")


def test_compute_title_uses_app_backed_task_context_for_modify_status(temp_db):
    assert jira.compute_title(
        temp_db,
        "modify",
        {"status": {"old": "Running", "new": "Stopped"}},
        APP_ID,
        task_context={"app_name": "alignn", "branch": "maca"},
    ) == "[Stop] alignn 【停止发布 maca 分支】"


def test_compute_title_uses_change_for_non_stop_modify(temp_db):
    assert jira.compute_title(
        temp_db,
        "modify",
        {"status": {"old": "Stopped", "new": "Running"}},
        APP_ID,
        task_context={"app_name": "alignn", "branch": "maca"},
    ) == "[Change] alignn 【修改项目】"


def test_compute_title_uses_app_backed_append_for_existing_create(temp_db):
    assert jira.compute_title(
        temp_db,
        "create",
        {"app_name": "alignn", "app_version": "v2.0"},
        APP_ID,
        task_context={"app_name": "alignn", "app_version": "v1.0"},
    ) == "[Append] alignn 【追加发布新版本】"


def test_compute_title_keeps_cicd_first_created_as_new(temp_db):
    assert jira.compute_title(
        temp_db,
        "create",
        {
            "app_name": "alignn",
            "app_version": "v1.0",
            "_cicd_first_action": "created",
        },
        APP_ID,
        task_context={"app_name": "alignn", "app_version": "v1.0"},
    ) == "[New] alignn 【新发布项目】"


def test_compute_title_treats_cicd_first_associated_as_append(temp_db):
    assert jira.compute_title(
        temp_db,
        "create",
        {
            "app_name": "alignn",
            "app_version": "v2.0",
            "_cicd_first_action": "associated",
        },
        APP_ID,
        task_context={"app_name": "alignn", "app_version": "v1.0"},
    ) == "[Append] alignn 【追加发布新版本】"


def test_create_issue_sends_fixed_project_types_and_eta_fields(monkeypatch):
    sent: list[tuple[str, str, dict]] = []

    def fake_request(base, token, method, path, body=None):
        sent.append((method, path, body))
        return {"key": "SPD-1"}

    monkeypatch.setattr(jira, "_request", fake_request)
    cfg = {"JIRA_BASE_URL": "http://jira", "JIRA_TOKEN": "t", "JIRA_ASSIGNEE": "m00930"}

    assert jira.create_issue("[New] App", "desc", jira_config=cfg) == "SPD-1"
    jira.create_issue("[New] App", "desc", jira_config={**cfg, "JIRA_PARENT_ISSUE": "SPD-1535"})

    assert [(method, path) for method, path, _ in sent] == [("POST", "/rest/api/2/issue")] * 2
    task, subtask = (body["fields"] for _, _, body in sent)
    assert task["project"] == {"key": "SPD"}
    assert task["components"] == [{"name": "SPD_CICD"}]
    assert task["issuetype"] == {"name": "Task"}
    assert "parent" not in task
    assert task["assignee"] == {"name": "m00930"}
    assert task["customfield_10115"] == task["customfield_10116"]
    assert subtask["issuetype"] == {"name": "Sub-task"}
    assert subtask["parent"] == {"key": "SPD-1535"}
