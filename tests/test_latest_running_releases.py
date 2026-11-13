"""Running-App release inventory stays on successful frozen snapshots."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.main import create_app
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, make_dependency_app_info, seed_app, seed_cycle, seed_signup, store_app_info


@pytest.fixture()
def inventory(temp_db):
    for name in ("amber", "hpl", "skipped", "withdrawn", "stopped", "never"):
        seed_app(temp_db, name, official_name=name.upper(), app_info=make_dependency_app_info(name))
    cycles = []
    for version in ("3.9", "3.10"):
        release = seed_cycle(temp_db, name=version)
        cycles.append(release)
        for name in ("amber", "hpl", "skipped", "withdrawn", "stopped"):
            if version == "3.10" and name == "skipped":
                continue
            entry = seed_signup(temp_db, release, name, target_version=f"{name}-official-{version}")
            if version == "3.10" and name == "withdrawn":
                cycle_service.withdraw(temp_db, entry["id"], user="rm", role="RM")
                continue
            images = [{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{str(index + 1) * 64}"}
                      for index, build in enumerate(entry["delivery_builds"])]
            qa_service.set_qa_status_batch(temp_db, release, [{
                "app_id": name, "status": "cannot_release" if version == "3.10" and name == "amber" else "has_issues",
                "issue_note": "QA explanation", "delivery_revision": entry["delivery_revision"], "deliverable_images": images,
            }], user="qa", role="QA")
        close_qa(temp_db, release)
        cycle_service.publish(temp_db, release, user="rm", role="RM")
    temp_db.execute("UPDATE apps SET cicd_status='stopped' WHERE id='stopped'")
    temp_db.commit()
    changed = make_dependency_app_info("amber")
    for test in changed["app_test"].values():
        test["test_cmd"] = "development-only-command"
    store_app_info(temp_db, "amber", changed, commit_id="new-development")
    return cycles


def test_latest_success_retains_failed_skipped_and_withdrawn_apps_and_all_images(temp_db, inventory):
    before = temp_db.total_changes
    result = lookup_service.latest_running_releases(temp_db)
    assert temp_db.total_changes == before
    assert result["app_count"] == 5
    rows = result["rows"]
    assert {row["app_id"] for row in rows} == {"amber", "hpl", "skipped", "withdrawn", "never"}
    for name in ("amber", "skipped", "withdrawn"):
        subset = [row for row in rows if row["app_id"] == name]
        assert {row["hpc_version"] for row in subset} == {"3.9"}
        assert {row["app_version"] for row in subset} == {f"{name}-official-3.9"}
    hpl = [row for row in rows if row["app_id"] == "hpl"]
    assert {row["hpc_version"] for row in hpl} == {"3.10"}
    assert {row["arch"] for row in hpl} == {"amd64", "arm64"}
    assert len({row["os"] for row in hpl}) > 1
    assert all(row["chips"] == ["C600"] for row in hpl if row["os"] == "ubuntu22.04")
    assert all(row["chips"] == ["C588"] for row in hpl if row["os"] == "ubuntu24.04")
    assert all(row["chips"] == ["C500"] for row in hpl if row["arch"] == "arm64")
    assert {row["image_target"] for row in hpl} == {"release", "dbg"}
    assert all(row["delivery_reference"].endswith(row["digest"]) for row in hpl)
    assert all(row["delivery_reference"] in row["command"] for row in hpl if row.get("command"))
    assert not any("development-only-command" in row.get("command", "") for row in rows)
    never = next(row for row in rows if row["app_id"] == "never")
    assert never["hpc_version"] == "" and never["note"] == "尚无成功发布记录"


def test_handles_migrated_publications_and_unlocked_latest_release(temp_db, inventory):
    temp_db.execute("DELETE FROM official_versions WHERE app_id='amber'")
    temp_db.commit()
    assert {row["hpc_version"] for row in lookup_service.latest_running_releases(temp_db)["rows"] if row["app_id"] == "amber"} == {"3.9"}
    cycle_service.unlock(temp_db, inventory[1], reason="correction", user="rm", role="RM")
    assert {row["hpc_version"] for row in lookup_service.latest_running_releases(temp_db)["rows"] if row["app_id"] == "hpl"} == {"3.9"}


def test_missing_snapshot_does_not_borrow_development_commands(temp_db, inventory):
    temp_db.execute("UPDATE official_versions SET app_info_version_id=NULL WHERE app_id='hpl'")
    temp_db.commit()
    rows = [row for row in lookup_service.latest_running_releases(temp_db)["rows"] if row["app_id"] == "hpl"]
    assert rows and all(not row.get("command") for row in rows)
    assert all("快照缺失" in row["note"] for row in rows)
    assert all(row["digest"] for row in rows)


@pytest.mark.parametrize("role,status", [("QA", 200), ("Owner", 200), ("RM", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_inventory_api_permissions(temp_db, inventory, role, status):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: {"username": "reader", "role": role}
    with TestClient(app) as client:
        response = client.get("/api/lookup/latest-running")
        assert response.status_code == status
        if status == 200:
            assert response.json()["app_count"] == 5


def test_empty_inventory(temp_db):
    assert lookup_service.latest_running_releases(temp_db) == {"app_count": 0, "rows": []}


def test_legacy_images_and_unfilled_builds_are_not_assigned_to_guessed_platforms(temp_db, inventory):
    digest = "sha256:" + "a" * 64
    temp_db.execute("UPDATE official_versions SET images_json='[]', image='harbor.sh.mxcr.io/hpc-release/hpl-maca', digest=? WHERE app_id='hpl'", (digest,))
    temp_db.commit()
    rows = [row for row in lookup_service.latest_running_releases(temp_db)["rows"] if row["app_id"] == "hpl"]
    legacy = [row for row in rows if row.get("delivery_build_key") == "legacy"]
    assert len(legacy) == 1 and not legacy[0]["os"] and not legacy[0]["arch"]
    assert "未指定构建" in legacy[0]["note"]
    commands = [row for row in rows if row.get("command")]
    assert commands
    assert all(not row["delivery_reference"] and digest not in row["command"] for row in commands)
    assert all("命令为模板" in row["note"] for row in commands)
