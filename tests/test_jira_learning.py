"""Learning proposals use real SQLite records and never publish knowledge automatically."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db.connection import transaction
from app.db.jira_agent_connection import connect_jira_agent, reset_jira_agent_init_state
from app.deps import require_login
from app.domain import jira_learning
from app.main import create_app
from app.repositories import jira_agent_repo, jira_learning_repo
from app.services import jira_learning_service, jira_learning_sources


@pytest.fixture()
def conn(tmp_path: Path):
    reset_jira_agent_init_state()
    db = connect_jira_agent(f"sqlite:///{tmp_path / 'learning.db'}")
    try:
        yield db
    finally:
        db.close()
        reset_jira_agent_init_state()


def test_owner_messages_and_agent_comment_provenance(conn):
    with transaction(conn):
        conversation = jira_agent_repo.create_conversation(
            conn, conversation_id="jac_1", issue_key="MC3-1",
            issue_summary="回退", agent_group="HPC", owner="alice",
            created_by="carol", workspace="/tmp/work",
        )
        first = jira_agent_repo.create_turn(
            conn, conversation_id=conversation["id"], trigger="handover",
            created_by="alice", input_text="",
        )
        jira_agent_repo.add_event(
            conn, conversation_id=conversation["id"], turn_id=first["id"],
            kind="user_message", payload={"author": "alice", "text": "把 MC3-1 交给 agent", "files": [], "mode": "handover"},
        )
        jira_agent_repo.update_turn(conn, first["id"], status="completed", comment_id="101")
        second = jira_agent_repo.create_turn(
            conn, conversation_id=conversation["id"], trigger="followup",
            created_by="alice", input_text="请核对编译命令",
        )
        jira_agent_repo.add_event(
            conn, conversation_id=conversation["id"], turn_id=second["id"],
            kind="user_message", payload={"author": "carol", "text": "RM 的补充", "files": []},
        )
        owner_event = jira_agent_repo.add_event(
            conn, conversation_id=conversation["id"], turn_id=second["id"],
            kind="user_message", payload={"author": "alice", "text": "请核对编译命令", "files": []},
        )

    messages = jira_learning_repo.conversation_messages(conn, "MC3-1", "HPC")
    assert [item["text"] for item in messages] == ["RM 的补充", "请核对编译命令"]
    assert messages[-1]["id"] == owner_event["id"]
    comments = jira_learning_repo.agent_comment_ids(conn, "MC3-1")
    assert comments == {"101"}
    source = jira_learning.build_source({
        "key": "MC3-1", "description": "性能回退",
        "comments": [
            {"id": "101", "body": "agent 曾推测编译器原因"},
            {"id": "102", "body": "owner 复测后确认"},
        ],
        "custom_fields": {}, "attachments": [],
    }, messages, comments)
    assert source["sources"][1]["independent"] is False
    assert source["sources"][2]["independent"] is True
    assert source["sources"][4]["id"] == f"chat:{owner_event['id']}"


def test_proposal_requires_existing_independent_evidence(tmp_path: Path):
    source = jira_learning.build_source({
        "key": "MC3-1", "description": "原始复现记录",
        "comments": [{"id": "101", "body": "agent 输出"}],
        "custom_fields": {}, "attachments": [],
    }, [], {"101"})
    proposal = {
        "kind": "skill_update", "title": "补充 AOT 规则",
        "summary": "建议", "target_path": "modules/problem-analysis/SKILL.md",
        "proposed_text": "核对具体 kernel", "applicability": "AOT 回退",
        "updated_text": "# 问题分析\n\n补充：核对具体 kernel。\n",
        "evidence_ids": ["jira:comment:101"], "limitations": "", "validation": "",
    }
    snapshot = {"modules/problem-analysis/SKILL.md": "# 问题分析\n"}
    with pytest.raises(ValueError, match="独立证据"):
        jira_learning.validate_proposals(json.dumps({"proposals": [proposal]}), source, snapshot)
    proposal["evidence_ids"] = ["jira:description"]
    result = jira_learning.validate_proposals(
        json.dumps({"proposals": [proposal]}), source, snapshot
    )[0]
    assert result["patch_text"].startswith("diff --git a/modules/problem-analysis/SKILL.md")
    assert "+补充：核对具体 kernel。" in result["patch_text"]
    assert "updated_text" not in result
    target = tmp_path / "modules/problem-analysis/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text(snapshot["modules/problem-analysis/SKILL.md"])
    subprocess.run(
        ["git", "apply", "--check", "-"], cwd=tmp_path,
        input=result["patch_text"], text=True, check=True,
    )
    subprocess.run(
        ["git", "apply", "-"], cwd=tmp_path,
        input=result["patch_text"], text=True, check=True,
    )
    assert target.read_text() == proposal["updated_text"]
    proposal["target_path"] = "../AGENTS.md"
    with pytest.raises(ValueError, match="目标"):
        jira_learning.validate_proposals(json.dumps({"proposals": [proposal]}), source, snapshot)


def test_review_and_manual_publication_are_separate_database_actions(conn):
    with transaction(conn):
        job = jira_learning_repo.create_job(conn, "MC3-1", "HPC", "carol")
        jira_learning_repo.add_proposals(conn, job["id"], [{
            "kind": "skill_update", "title": "补充对照条件", "summary": "回退",
            "target_path": "modules/problem-analysis/SKILL.md",
            "proposed_text": "核对对照条件", "applicability": "特定版本",
            "evidence_ids": ["jira:comment:102"], "limitations": "",
            "validation": "复查原案",
        }])
    proposal = jira_learning_repo.list_proposals(conn, job["id"])[0]
    with transaction(conn):
        assert jira_learning_repo.review(conn, proposal["id"], "carol", "approved", "已核对")
    assert jira_learning_repo.get_proposal(conn, proposal["id"])["status"] == "approved"
    assert jira_learning_repo.get_proposal(conn, proposal["id"])["published_commit"] == ""
    with transaction(conn):
        assert jira_learning_repo.record_publication(
            conn, proposal["id"], "carol", "8b5e1f3", "人工完成"
        )
    published = jira_learning_repo.get_proposal(conn, proposal["id"])
    assert published["status"] == "published"
    assert published["published_commit"] == "8b5e1f3"
    assert [row["decision"] for row in jira_learning_repo.list_reviews(conn, proposal["id"])] == [
        "approved", "published",
    ]


def test_learning_api_is_rm_only(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    monkeypatch.setattr(settings, "admin_password_file", tmp_path / "admin.local")
    monkeypatch.setattr(settings, "jira_agent_database_url", f"sqlite:///{tmp_path / 'learning.db'}")
    monkeypatch.setattr(settings, "jira_agent_runner_enabled", False)
    monkeypatch.setattr(settings, "jira_learning_runner_enabled", False)
    reset_jira_agent_init_state()
    app = create_app()
    actor = {"username": "alice", "role": "Owner"}
    app.dependency_overrides[require_login] = lambda: actor
    with TestClient(app) as client:
        assert client.get("/api/jira-agent/learning/jobs").status_code == 403
        actor["role"] = "RM"
        response = client.get("/api/jira-agent/learning/jobs")
        assert response.status_code == 200
        assert response.json() == {"jobs": [], "has_more": False}
    reset_jira_agent_init_state()


def test_learning_api_exposes_dialogue_and_queues_followup(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "db_path", tmp_path / "main.db")
    monkeypatch.setattr(settings, "admin_password_file", tmp_path / "admin.local")
    monkeypatch.setattr(
        settings, "jira_agent_database_url", f"sqlite:///{tmp_path / 'learning.db'}",
    )
    monkeypatch.setattr(settings, "jira_agent_runner_enabled", False)
    monkeypatch.setattr(settings, "jira_learning_runner_enabled", False)
    reset_jira_agent_init_state()
    app = create_app()
    app.dependency_overrides[require_login] = lambda: {"username": "carol", "role": "RM"}
    with TestClient(app) as client:
        db = connect_jira_agent(settings.jira_agent_database_url)
        try:
            with transaction(db):
                job = jira_learning_repo.create_job(db, "MC3-2", "HPC", "carol")
                turn = jira_learning_repo.list_turns(db, job["id"])[0]
                jira_learning_repo.update_turn(
                    db, turn["id"], status="completed", finished_at="2026-09-24 12:00:00",
                )
                jira_learning_repo.update_job(
                    db,
                    job["id"],
                    status="completed",
                    source_path=str(tmp_path / "source.json"),
                    source_sha256="sha",
                    thread_id="thread-1",
                    finished_at="2026-09-24 12:00:00",
                )
        finally:
            db.close()

        response = client.post(
            f"/api/jira-agent/learning/jobs/{job['id']}/messages",
            json={"text": "补充适用边界"},
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "queued"
        detail = client.get(f"/api/jira-agent/learning/jobs/{job['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["job"]["status"] == "queued"
        assert body["job"]["can_message"] is True
        assert [turn["trigger"] for turn in body["turns"]] == ["initial", "followup"]
        assert body["events"][-1]["payload"]["text"] == "补充适用边界"
    reset_jira_agent_init_state()


def test_learning_runner_ignores_current_components_and_stages_proposals(monkeypatch, tmp_path: Path, knowledge):
    """Exercise the queue without restricting execution by the Jira current component."""
    import asyncio

    from app.integrations import jira
    from app.services import jira_learning_runner
    from tests import test_jira_agent as local_server

    from tests.test_jira_learning_joint import Learner
    fake = Learner().start()
    jira_api = local_server.FakeJira()
    analysis_conf = tmp_path / "release_system.conf"
    analysis_conf.write_text(
        "[jira_agent:HPC]\nDISPLAY_NAME = HPC\nCODEX_WS_URL = ws://unused:1\n"
        "CODEX_WS_TOKEN = unused\nWORKSPACE_ROOT = /analysis/workspaces\n"
        "COMPONENTS = OTHER_COMPONENT\nMAX_CONCURRENT = 1\nTURN_TIMEOUT_SECONDS = 60\n"
    )
    learning_conf = analysis_conf
    learning_conf.write_text(
        analysis_conf.read_text() + "\n[jira_learning:HPC]\nCODEX_WS_URL = " + fake.url + "\nCODEX_WS_TOKEN = " + local_server.TOKEN
        + "\nWORKSPACE_ROOT = /learning/workspaces\nREPOSITORY_REF=refs/heads/main\nTURN_TIMEOUT_SECONDS = 60\n"
    )
    monkeypatch.setattr(settings, "runtime_conf_path", analysis_conf)
    monkeypatch.setattr(settings, "jira_agent_database_url", f"sqlite:///{tmp_path / 'learning.db'}")
    monkeypatch.setattr(settings, "jira_agent_data_dir", tmp_path / "data")
    monkeypatch.setattr(jira, "get_issue_snapshot", jira_api.get_issue_snapshot)
    monkeypatch.setattr(local_server, "RESULT", {
        "proposals": [{
            "kind": "skill_update", "title": "核对尾部元素覆盖", "summary": "检查边界条件",
            "target_path": ".agents/skills/hpc-jira-agent/SKILL.md",
            "proposed_text": "记录复现输入与修复验证",
            "updated_text": "# HPC\n\n检查尾部元素是否被正确处理。\n",
            "applicability": "原工单输入", "evidence_ids": ["jira:description"],
            "limitations": "需核对输入规模与边界条件", "validation": "复查原输入",
        }],
    })
    fake.proposal = local_server.RESULT["proposals"][0]
    reset_jira_agent_init_state()
    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        with transaction(conn):
            queued = jira_learning_repo.create_job(conn, "MC3-1", "HPC", "carol", repository_only=True)
            work = jira_learning_repo.claim_next_turn(conn)
        assert work["job"]["id"] == queued["id"]
        asyncio.run(jira_learning_runner.runner._run(work["job"], work["turn"]))
        completed = jira_learning_repo.get_job(conn, work["job"]["id"])
        assert completed["status"] == "completed", completed["error"]
        assert completed["acquisition_status"] == "complete"
        assert not jira_learning_service.get_source(completed["id"])["collection_gaps"]
        assert jira_learning_service.material_index(completed["id"])["items"]
        proposal = jira_learning_repo.list_proposals(conn, work["job"]["id"])[0]
        assert proposal["status"] == "pending"
        assert proposal["patch_text"].startswith(
            "diff --git a/.agents/skills/hpc-jira-agent/SKILL.md"
        )
        assert Path(completed["source_path"]).stat().st_mode & 0o077 == 0
        assert any(item.get("type") == "skill" for item in fake.params("turn/start")[0]["input"])
        schema = fake.params("turn/start")[0]["outputSchema"]
        assert schema["properties"]["proposals"]["items"]["properties"]["kind"]["enum"] == ["do_not_publish", "new_skill", "skill_update"]
        assert "/learning/workspaces/" + work["job"]["id"] + "/source.json" in fake.files

        followup = jira_learning_service.send_message(
            {"username": "carol", "role": "RM"}, completed["id"], "缩小到已验证版本"
        )
        assert followup["mode"] == "queued"
        merged = jira_learning_service.send_message(
            {"username": "carol", "role": "RM"}, completed["id"], "补充适用边界"
        )
        assert merged["mode"] == "merged"
        with transaction(conn):
            next_work = jira_learning_repo.claim_next_turn(conn)
        assert next_work["turn"]["trigger"] == "followup"
        assert next_work["turn"]["input_text"] == "缩小到已验证版本\n\n补充适用边界"
        local_server.RESULT["proposals"][0]["previous_id"] = proposal["id"]
        asyncio.run(jira_learning_runner.runner._run(next_work["job"], next_work["turn"]))
        continued = jira_learning_repo.get_job(conn, completed["id"])
        assert continued["status"] == "completed", continued["error"]
        assert len(jira_learning_repo.list_turns(conn, completed["id"])) == 2
        events = jira_learning_repo.list_events(conn, completed["id"])
        assert [item["kind"] for item in events].count("user_message") == 3
        assert len([item for item in events if item["item_id"].startswith("result:")]) == 2
        assert any(item["kind"] == "agent_message" and not item["item_id"].startswith("result:") for item in events)
        versions = jira_learning_repo.list_proposals(conn, completed["id"])
        assert len(versions) == 2
        assert versions[0]["superseded_by"] == versions[1]["id"]
        assert versions[1]["previous_id"] == versions[0]["id"]
        assert len(fake.params("thread/start")) == 1
        assert len(fake.params("thread/resume")) == 1
        assert len(fake.params("turn/start")) == 2
    finally:
        conn.close()
        fake.stop()
        jira_learning_runner.worker.shutdown()
        reset_jira_agent_init_state()


def test_learning_sources_include_owner_files_agent_results_and_jira_text(monkeypatch, tmp_path: Path):
    owner_file = tmp_path / "owner.patch"
    owner_file.write_text("diff --git a/a.c b/a.c\n")
    agent_file = tmp_path / "REPORT.md"
    agent_file.write_text("已验证修复")
    path_file = tmp_path / "ANALYSIS_PATH.md"
    path_file.write_text("# 分析路径\n\n## 第 1 步：复现\n")
    binary_file = tmp_path / "binary.txt"
    binary_file.write_bytes(b"a\0b")
    monkeypatch.setattr(
        jira_learning_sources.jira,
        "download_attachment",
        lambda url, max_bytes: b"compiler command: mxcc test.cu\n",
    )
    messages = [{
        "conversation_id": "jac_1", "turn_id": "jat_1",
        "files": ["owner.patch"],
    }]
    results = [{
        "conversation_id": "jac_1", "turn_id": "jat_1", "turn_seq": 1,
        "result": {"conclusion": "agent 判断"},
    }]
    files = [
        {"id": "f1", "conversation_id": "jac_1", "turn_id": "jat_1",
         "source": "upload", "name": "owner.patch", "size": owner_file.stat().st_size,
         "local_path": str(owner_file)},
        {"id": "f2", "conversation_id": "jac_1", "turn_id": "jat_1",
         "source": "artifact", "name": "REPORT.md", "size": agent_file.stat().st_size,
         "local_path": str(agent_file)},
        {"id": "f3", "conversation_id": "jac_1", "turn_id": "jat_1",
         "source": "artifact", "name": "binary.txt", "size": binary_file.stat().st_size,
         "local_path": str(binary_file)},
        {"id": "f4", "conversation_id": "jac_1", "turn_id": "jat_1",
         "source": "artifact", "name": "ANALYSIS_PATH.md", "size": path_file.stat().st_size,
         "local_path": str(path_file)},
    ]
    issue = {"attachments": [{
        "id": "77", "filename": "build.log", "size": 20,
        "mime_type": "text/plain", "content_url": "https://jira.test/77",
    }]}
    sources, gaps = jira_learning_sources.collect_sources(issue, messages, results, files)
    by_id = {item["id"]: item for item in sources}
    assert by_id["chat:file:f1"]["independent"] is True
    assert by_id["agent:artifact:f2"]["independent"] is False
    assert by_id["agent:analysis-path:f4"]["kind"] == "agent_analysis_path"
    assert by_id["agent:analysis-path:f4"]["independent"] is False
    assert by_id["agent:result:jat_1"]["independent"] is False
    assert by_id["jira:attachment:77"]["independent"] is True
    assert any(item["source"] == "file:f3" for item in gaps)


def test_skill_context_includes_committed_references(knowledge):
    from app.integrations import knowledge_repository
    snapshot = knowledge_repository.snapshot('refs/heads/main')
    context = jira_learning.skill_context(snapshot['files'])
    assert '## AGENTS.md' in context
    assert '## .agents/skills/hpc-jira-agent/references/rule.md' in context


def test_gerrit_changes_are_indexed_then_read_on_demand(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.jira_learning_materials import MaterialStore, gerrit_catalog
    from urllib.parse import urlparse

    host = urlparse(settings.gerrit_ssh_base_url).hostname
    url = f"https://{host}/c/PDE/HPC/demo/+/12345/2"
    calls = []

    def fetch(project, change):
        calls.append((project, change))
        return b"diff --git a/a.c b/a.c\n", {
            "project": project, "change": change, "revision": "a" * 40,
            "patchset": "2", "ref": "refs/changes/45/12345/2",
        }

    monkeypatch.setattr(jira_learning_sources, "_fetch_gerrit_change", fetch)
    items = gerrit_catalog([
        {"id": "jira:comment:1", "text": "修复见 " + url, "independent": True},
        {"id": "agent:result:1", "text": f"https://{host}/c/PDE/HPC/ignored/+/9", "independent": False},
        {"id": "jira:comment:2", "text": f"https://{host}/c/OTHER/demo/+/8", "independent": True},
    ], "CASE-1")
    assert not calls
    assert len(items) == 1
    assert items[0]["origin"]["gerrit"]["evidence_id"] == "jira:comment:1"
    from app.services.jira_learning_archive import LearningArchive
    archive = LearningArchive(tmp_path)
    archive.initialize([{'key':'CASE-1'}])
    with archive.connect(write=True) as conn:
        for item in items: archive.put(conn, item)
    store = MaterialStore(tmp_path)
    arguments = {"action": "read", "source_id": items[0]["id"], "offset": 0, "limit": 48000, "reason": "核对修复"}
    result = store.request(arguments, "first")
    assert result["success"]
    assert "diff --git a/a.c b/a.c" in result["contentItems"][0]["text"]
    assert store.request(arguments, "again")["success"]
    assert calls == [("PDE/HPC/demo", "12345")]


def test_batch_jql_creates_one_joint_job(monkeypatch, tmp_path: Path):
    db = tmp_path / "learning.db"
    analysis = tmp_path / "release_system.conf"
    analysis.write_text(
        "[jira_agent:HPC]\nDISPLAY_NAME=HPC\nCODEX_WS_URL=ws://analysis.test:4510\n"
        "CODEX_WS_TOKEN=unused\nWORKSPACE_ROOT=/analysis/workspaces\n"
        "COMPONENTS=PDE_HPC\nMAX_CONCURRENT=1\nTURN_TIMEOUT_SECONDS=60\n"
    )
    learning = analysis
    learning.write_text(
        analysis.read_text() + "\n[jira_learning:HPC]\nCODEX_WS_URL=ws://learning.test:4520\nCODEX_WS_TOKEN=token\n"
        "WORKSPACE_ROOT=/learning/workspaces\nREPOSITORY_REF=refs/heads/main\nTURN_TIMEOUT_SECONDS=60\n"
    )
    monkeypatch.setattr(settings, "jira_agent_database_url", f"sqlite:///{db}")
    monkeypatch.setattr(settings, "runtime_conf_path", analysis)
    monkeypatch.setattr(settings, "jira_learning_runner_enabled", True)
    monkeypatch.setattr(jira_learning_service.jira, "search_issues", lambda query, max_results: {
        "total": 3,
        "issues": [
            {"key": "MC3-11", "components": ["PDE_HPC"]},
            {"key": "MC3-12", "components": ["pde_hpc"]},
            {"key": "OTHER-1", "components": ["OTHER"]},
        ],
    })
    reset_jira_agent_init_state()
    try:
        selected = [item['key'] for item in jira_learning_service.preview_issues('project = MC3')['issues']]
        first = jira_learning_service.create_job(
            {"username": "rm", "role": "RM"}, "HPC", selected
        )
        assert set(first) == {"job", "reused"}
        assert first["job"]["issue_keys"] == ["MC3-11", "MC3-12", "OTHER-1"]
        assert first["reused"] is False
        second = jira_learning_service.create_job(
            {"username": "rm", "role": "RM"}, "HPC", selected
        )
        assert set(second) == {"job", "reused"}
        assert second["job"]["id"] != first["job"]["id"]
        assert second["reused"] is False
    finally:
        reset_jira_agent_init_state()


def test_selected_handled_issues_do_not_depend_on_current_components(monkeypatch, tmp_path: Path):
    db = tmp_path / "learning.db"
    analysis = tmp_path / "release_system.conf"
    analysis.write_text(
        "[jira_agent:HPC]\nDISPLAY_NAME=HPC\nCODEX_WS_URL=ws://analysis.test:4510\n"
        "CODEX_WS_TOKEN=unused\nWORKSPACE_ROOT=/analysis/workspaces\n"
        "COMPONENTS=NEW_COMPONENT\nMAX_CONCURRENT=1\nTURN_TIMEOUT_SECONDS=60\n"
    )
    learning = analysis
    learning.write_text(
        analysis.read_text() + "\n[jira_learning:HPC]\nCODEX_WS_URL=ws://learning.test:4520\nCODEX_WS_TOKEN=token\n"
        "WORKSPACE_ROOT=/learning/workspaces\nREPOSITORY_REF=refs/heads/main\nTURN_TIMEOUT_SECONDS=60\n"
    )
    monkeypatch.setattr(settings, "jira_agent_database_url", f"sqlite:///{db}")
    monkeypatch.setattr(settings, "runtime_conf_path", analysis)
    monkeypatch.setattr(settings, "jira_learning_runner_enabled", True)
    monkeypatch.setattr(
        jira_learning_service.jira,
        "search_issues",
        lambda query, max_results: {"total": 3, "issues": [{"key": key} for key in ("MC3-21", "MC3-22", "OTHER-1")]},
    )
    reset_jira_agent_init_state()
    conn = connect_jira_agent(settings.jira_agent_database_url)
    try:
        with transaction(conn):
            for key in ("MC3-21", "MC3-22"):
                jira_agent_repo.create_conversation(
                    conn,
                    conversation_id="jac_" + key,
                    issue_key=key,
                    issue_summary=key,
                    agent_group="HPC",
                    owner="alice",
                    created_by="alice",
                    workspace="/work/" + key,
                )
            jira_agent_repo.create_conversation(
                conn,
                conversation_id="jac_other",
                issue_key="OTHER-1",
                issue_summary="other group",
                agent_group="OTHER",
                owner="alice",
                created_by="alice",
                workspace="/work/OTHER-1",
            )
    finally:
        conn.close()

    try:
        result = jira_learning_service.create_job(
            {"username": "rm", "role": "RM"},
            "HPC",
            ["mc3-21", "MC3-22", "MC3-21"],
        )
        assert set(result) == {"job", "reused"}
        assert result["job"]["issue_keys"] == ["MC3-21", "MC3-22"]
        historical = jira_learning_service.create_job({"username": "rm", "role": "RM"}, "HPC", ["OTHER-1"])
        assert historical["job"]["issue_key"] == "OTHER-1"
    finally:
        reset_jira_agent_init_state()

def test_model_source_is_bounded_without_changing_review_snapshot():
    original = {
        "sources": [
            {"id": "jira:description", "text": "a" * 150_000, "independent": True},
            {"id": "jira:comment:1", "text": "b" * 600_000, "independent": True},
        ],
        "collection_gaps": [],
    }
    bounded, gaps = jira_learning.model_source(original)
    assert len(original["sources"][0]["text"]) == 150_000
    assert bounded["sources"][0]["truncated"] is True
    assert bounded["sources"][0]["text"].startswith("a")
    assert bounded["sources"][0]["text"].endswith("a")
    assert len(bounded["sources"][1]["text"]) < len(original["sources"][1]["text"])
    assert {item["source"] for item in gaps} == {"jira:description", "jira:comment:1"}


def test_model_source_keeps_both_comparison_groups_and_short_context(monkeypatch):
    monkeypatch.setattr(jira_learning, 'MODEL_SOURCE_MAX_CHARS', 1000)
    monkeypatch.setattr(jira_learning, 'MODEL_SOURCE_MAX_ITEM_CHARS', 900)
    original = {'sources': [
        {'id': 'report', 'text': 'Conditions and limits.'},
        {'id': 'good', 'text': 'GOOD START\n' + 'a' * 5000 + '\nGOOD RESULT', 'independent': True},
        {'id': 'bad', 'text': 'BAD START\n' + 'b' * 5000 + '\nBAD RESULT', 'independent': True},
    ]}
    bounded, gaps = jira_learning.model_source(original)
    assert sum(len(item['text']) for item in bounded['sources']) <= 1000
    assert bounded['sources'][0]['text'] == original['sources'][0]['text']
    for item, before in zip(bounded['sources'][1:], original['sources'][1:]):
        assert item['text'].startswith(before['text'].splitlines()[0])
        assert item['text'].endswith(before['text'].splitlines()[-1])
        assert item['truncated'] is True
        assert len(before['text']) > 5000 and 'truncated' not in before
    assert {item['source'] for item in gaps} == {'good', 'bad'}


@pytest.fixture()
def knowledge(tmp_path, monkeypatch):
    from tests.learning_helpers import knowledge_repo
    return knowledge_repo(tmp_path, monkeypatch)
