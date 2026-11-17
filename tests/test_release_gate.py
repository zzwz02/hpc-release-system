import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.errors import ApiError, register_error_handlers
from app.api.routers import release_gate
from app.deps import get_db, require_login
from app.repositories import release_gate_repo
from app.services import release_gate_service as gate
from tests.conftest import seed_app, seed_cycle, seed_signup

SHA = "a" * 40


@pytest.fixture()
def enrolled(temp_db):
    app = seed_app(temp_db, git_url="hpc_testapp", commit_id=SHA)
    release = seed_cycle(temp_db, name="3.10.2")
    entry = seed_signup(temp_db, release, app)
    gate.save_version(temp_db, release_gate.VersionWrite(maca_version="3.10.2.11", release_id=release), {"username": "rm", "role": "RM"})
    return entry


def request(**updates):
    return release_gate.GateCheck(**{"build_type": "release", "maca_version": "3.10.2.11",
        "git_url": "ssh://gerrit.metax-internal.com:29418/PDE/HPC/hpc_testapp.git", "git_branch": "maca", **updates})


def test_checks_order_exact_versions_and_success(temp_db, enrolled):
    assert gate.check(temp_db, request(maca_version="3.10.2.110"))["reason"] == "version_unauthorized"
    assert gate.check(temp_db, request(git_branch="other"))["decision"] == "skip"
    assert gate.check(temp_db, request())["reason"] == "preflight_passed"
    result = gate.check(temp_db, request(phase="verify", baseline_commit=SHA, actual_commit=SHA, resolved_expected_commit=SHA))
    assert result["reason"] == "passed"
    assert result["evidence"]["code_owner"] == "owner_test"
    assert result["evidence"]["snapshot_id"] == enrolled["locked_version_id"]


def test_lock_precedes_commit_and_withdrawal_skips(temp_db, enrolled):
    temp_db.execute("UPDATE entries SET build_locked=1 WHERE id=?", (enrolled["id"],)); temp_db.commit()
    assert gate.check(temp_db, request(phase="verify"))["reason"] == "build_locked"
    temp_db.execute("UPDATE entries SET status='withdrawn' WHERE id=?", (enrolled["id"],)); temp_db.commit()
    assert gate.check(temp_db, request())["decision"] == "skip"


def test_snapshot_not_global_head_and_no_sync_or_mutation(temp_db, enrolled):
    temp_db.execute("UPDATE apps SET sync_head_commit=? WHERE id=?", ("b" * 40, enrolled["app_id"])); temp_db.commit()
    assert gate.check(temp_db, request())["evidence"]["expected_commit"] == SHA
    assert gate.check(temp_db, request(phase="verify", baseline_commit=SHA, actual_commit="b"*40, resolved_expected_commit=SHA))["reason"] == "commit_mismatch"
    assert temp_db.execute("SELECT commit_id FROM app_info_versions WHERE id=?", (enrolled["locked_version_id"],)).fetchone()[0] == SHA


def test_changed_missing_or_unresolvable_baseline_is_not_code_owner_failure(temp_db, enrolled):
    assert gate.check(temp_db, request(phase="verify", baseline_commit="b"*40))["reason"] == "service_error"
    assert gate.check(temp_db, request(phase="verify", baseline_commit=SHA))["reason"] == "service_error"
    assert gate.check(temp_db, request(phase="verify", baseline_commit=SHA, resolved_expected_commit=SHA))["reason"] == "service_error"
    temp_db.execute("UPDATE app_info_versions SET commit_id='' WHERE id=?", (enrolled["locked_version_id"],)); temp_db.commit()
    assert gate.check(temp_db, request())["reason"] == "service_error"


def test_renamed_release_cannot_authorize_wrong_major_version(temp_db, enrolled):
    temp_db.execute("UPDATE releases SET name='3.10.3' WHERE id=?", (enrolled["release_id"],))
    temp_db.commit()
    assert gate.check(temp_db, request())["reason"] == "service_error"


def test_short_commit_requires_resolved_prefix_not_substring(temp_db, enrolled):
    temp_db.execute("UPDATE app_info_versions SET commit_id='abcdef1' WHERE id=?", (enrolled["locked_version_id"],)); temp_db.commit()
    sha = "abcdef1" + "2"*33
    assert gate.check(temp_db, request(phase="verify", baseline_commit="abcdef1", actual_commit=sha, resolved_expected_commit=sha))["decision"] == "allow"
    bad = "2" + "abcdef1" + "2"*32
    assert gate.check(temp_db, request(phase="verify", baseline_commit="abcdef1", actual_commit=bad, resolved_expected_commit=bad))["reason"] == "service_error"


def test_repo_identity_uses_xml_path_and_manifest_branch(temp_db, enrolled):
    temp_db.execute("UPDATE apps SET git_url='APP/slurm/hpc_slurm_25.05.8.xml',git_branch='master',cicd_repo_type='repo' WHERE id=?", (enrolled["app_id"],)); temp_db.commit()
    result = gate.check(temp_db, request(git_url="APP/slurm/hpc_slurm_25.05.8.xml", git_branch="master"))
    assert result["evidence"]["expected_commit"] == SHA
    assert gate.check(temp_db, request(git_url="APP/slurm/hpc_slurm_25.05.8.xml", git_branch="maca-v25.05.8"))["decision"] == "skip"


def test_version_change_audited_stale_write_rejected_and_published_cannot_enable(temp_db, enrolled):
    body = release_gate.VersionWrite(maca_version="3.10.2.11", release_id=enrolled["release_id"], enabled=False, expected_revision=0)
    gate.save_version(temp_db, body, {"username": "rm", "role": "RM"})
    assert gate.check(temp_db, request())["reason"] == "version_unauthorized"
    with pytest.raises(ApiError): gate.save_version(temp_db, body, {"username": "rm", "role": "RM"})
    assert temp_db.execute("SELECT count(*) FROM audit WHERE event='release_build_admission'").fetchone()[0] == 2
    temp_db.execute("UPDATE releases SET released_locked=1 WHERE id=?", (enrolled["release_id"],)); temp_db.commit()
    with pytest.raises(ValueError): gate.save_version(temp_db, body.model_copy(update={"enabled":True,"expected_revision":1}), {"username":"rm","role":"RM"})


@pytest.mark.parametrize("role,status", [("RM",200),("SPD",403),("Owner",403),("QA",403),("Admin",403)])
def test_version_write_is_rm_only(temp_db, enrolled, role, status):
    app = FastAPI(); register_error_handlers(app); app.include_router(release_gate.router)
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username":"actor","role":role}
    with TestClient(app) as client:
        response = client.post("/api/cicd/release-gate/versions", json={"maca_version":"3.10.2.12","release_id":enrolled["release_id"]})
    assert response.status_code == status, response.text


def test_machine_check_requires_scoped_token(temp_db, enrolled, monkeypatch):
    app = FastAPI(); app.include_router(release_gate.router)
    app.dependency_overrides[get_db] = lambda: temp_db
    with TestClient(app) as client:
        monkeypatch.setattr(release_gate.runtime_config, "section", lambda section: {})
        assert client.post("/api/cicd/release-gate/check", json=request().model_dump()).status_code == 503
        monkeypatch.setattr(release_gate.runtime_config, "section", lambda section: {"RELEASE_BUILD_TOKEN":"test"})
        assert client.post("/api/cicd/release-gate/check", json=request().model_dump()).status_code == 403
        assert client.post("/api/cicd/release-gate/check", json=request().model_dump(),headers={"Authorization":"Bearer test"}).status_code == 200
