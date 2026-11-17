"""Final publication selection follows cycle chronology, with one record per cycle."""
from copy import deepcopy
import sqlite3

import pytest

from app.db.connection import init_db
from app.repositories import official_repo
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_qa, seed_app, seed_cycle, seed_signup


def publish_cycle(conn, app_id, *, name, date, version):
    release_id = seed_cycle(conn, name=name)
    seed_signup(conn, release_id, app_id, target_version=version)
    qa_service.set_qa_status_batch(conn, release_id, [{"app_id": app_id, "status": "qa_passed"}], user="qa", role="QA")
    close_qa(conn, release_id)
    conn.execute("UPDATE releases SET release_date=? WHERE id=?", (date, release_id))
    conn.commit()
    cycle_service.publish(conn, release_id, user="rm", role="RM")
    return release_id


def test_correction_of_older_cycle_does_not_replace_latest_official_or_exports(temp_db):
    app = seed_app(temp_db)
    # Cycle creation and publication order deliberately disagree with cycle dates and names.
    newer = publish_cycle(temp_db, app, name="3.10", date="2026-10-31", version="newer")
    older = publish_cycle(temp_db, app, name="3.9", date="2026-09-30", version="older")
    new_official = official_repo.current_by_app(temp_db)[app]
    assert new_official["release_id"] == newer
    before = lookup_service.latest_running_releases(temp_db)
    exported = catalog_service.compat_list_csv(temp_db)
    assert [release["id"] for release in lookup_service.published_releases(temp_db)] == [newer, older]
    for _ in range(2):
        cycle_service.unlock(temp_db, older, reason="correct old cycle", user="rm", role="RM")
        assert official_repo.current_for_release(temp_db, older) == {}
        cycle_service.publish(temp_db, older, user="rm", role="RM")
        assert official_repo.current_by_app(temp_db)[app] == new_official
        assert lookup_service.latest_running_releases(temp_db) == before
        assert catalog_service.compat_list_csv(temp_db) == exported
        history = catalog_service.app_detail(temp_db, app)["official_versions"]
        assert [record["release_id"] for record in history] == [newer, older]
        assert temp_db.execute("SELECT COUNT(*) FROM official_versions WHERE app_id=? AND release_id=?", (app, older)).fetchone()[0] == 1
    # Unlocking the latest cycle temporarily falls back to the prior successful cycle.
    cycle_service.unlock(temp_db, newer, reason="correct latest", user="rm", role="RM")
    assert official_repo.current_by_app(temp_db)[app]["release_id"] == older
    cycle_service.publish(temp_db, newer, user="rm", role="RM")
    assert official_repo.current_by_app(temp_db)[app]["release_id"] == newer


def test_migrated_entry_only_history_uses_same_cycle_selection_and_disappears_on_unlock(temp_db):
    app = seed_app(temp_db)
    newer = publish_cycle(temp_db, app, name="newer", date="2026-10-31", version="new")
    older = publish_cycle(temp_db, app, name="older", date="2026-09-30", version="old")
    temp_db.execute("DELETE FROM official_versions WHERE release_id=?", (newer,))
    temp_db.commit()
    current = official_repo.current_by_app(temp_db)[app]
    assert current["release_id"] == newer and current["id"] is None
    cycle_service.unlock(temp_db, older, reason="old correction", user="rm", role="RM")
    cycle_service.publish(temp_db, older, user="rm", role="RM")
    assert official_repo.current_by_app(temp_db)[app] == current
    assert catalog_service.compat_list(temp_db)[0]["official"]["release_name"] == "newer"
    cycle_service.unlock(temp_db, newer, reason="clear entry-only publication", user="rm", role="RM")
    assert [row["release_id"] for row in official_repo.history_for_app(temp_db, app)] == [older]
    assert official_repo.current_by_app(temp_db)[app]["release_id"] == older


def test_upgrade_removes_withdrawals_duplicates_and_unlocked_records_and_is_idempotent(temp_db):
    app = seed_app(temp_db)
    release = publish_cycle(temp_db, app, name="published", date="2026-10-31", version="original")
    original = official_repo.current_by_app(temp_db)[app]
    temp_db.execute("DROP INDEX idx_official_versions_app_release")
    temp_db.execute("ALTER TABLE official_versions ADD COLUMN withdrawn INTEGER NOT NULL DEFAULT 0")
    temp_db.execute("CREATE INDEX idx_official_versions_app ON official_versions(app_id,withdrawn,id)")
    # Simulate two old active revisions and a newer withdrawn revision.
    replacement = deepcopy(original)
    replacement["version"] = "replacement"
    replacement["test_manifest"]["entry"]["target_version"] = "replacement"
    kept_id = official_repo.insert_official(temp_db, replacement)
    discarded_id = official_repo.insert_official(temp_db, replacement)
    temp_db.execute("UPDATE official_versions SET withdrawn=1 WHERE id=?", (discarded_id,))
    open_cycle = seed_cycle(temp_db, name="open")
    official_repo.insert_official(temp_db, {**replacement, "release_id": open_cycle, "test_manifest": None})
    temp_db.commit()
    kept_json = temp_db.execute("SELECT test_manifest_json FROM official_versions WHERE id=?", (kept_id,)).fetchone()[0]
    init_db(temp_db)
    init_db(temp_db)
    assert "withdrawn" not in {row["name"] for row in temp_db.execute("PRAGMA table_info(official_versions)")}
    rows = temp_db.execute("SELECT id,test_manifest_json FROM official_versions").fetchall()
    assert [(row["id"], row["test_manifest_json"]) for row in rows] == [(kept_id, kept_json)]
    assert official_repo.current_by_app(temp_db)[app]["release_id"] == release
    with pytest.raises(sqlite3.IntegrityError):
        official_repo.insert_official(temp_db, replacement)
    temp_db.rollback()
