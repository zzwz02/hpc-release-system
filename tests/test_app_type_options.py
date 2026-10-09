from app.repositories import snapshots_repo
from app.services import app_service, release_service
from tests.conftest import seed_app, seed_release


def test_state_app_types_include_history_and_remove_blank_and_duplicate_values(temp_db, tmp_dir):
    first = seed_release(temp_db, tmp_path=tmp_dir)
    app_id = temp_db.execute("SELECT id FROM apps LIMIT 1").fetchone()["id"]
    second = release_service.create_release(
        temp_db, name="next", maca_version="next", app_freeze_deadline="",
        doc_deadline="", user="rm", role="RM",
    )["release_id"]
    snap = snapshots_repo.get_snapshot(temp_db, second, app_id)
    snap["type"] = " 科学计算 "
    snapshots_repo.save_snapshot(temp_db, second, app_id, snap)
    for i, app_type in enumerate(["科学计算", "", " \t ", None]):
        extra_id = seed_app(temp_db, second, git_url=f"repo/extra-{i}")
        extra = snapshots_repo.get_snapshot(temp_db, second, extra_id)
        extra["type"] = app_type
        snapshots_repo.save_snapshot(temp_db, second, extra_id, extra)
    temp_db.commit()

    # Options are global even when viewing an older release.
    for release_id in (first, second):
        state = app_service.get_state(
            temp_db, user={"username": "rm", "role": "RM"}, release_id_param=release_id,
        )
        assert set(state["app_types"]) == {"分子动力学", "科学计算"}
        assert len(state["app_types"]) == 2


def test_empty_state_has_no_existing_app_types(temp_db):
    state = app_service.get_state(temp_db, user={"username": "rm", "role": "RM"})
    assert state["app_types"] == []
