"""Tests for the R3 app-layer decision-sync gating rule + preview endpoint.

Covers:
  * app.domain.decision_sync pure helpers (phase_label, resolve_synced_decision)
  * app.services.app_service.sync_decision_to_later_releases (the changed rule:
    upgrade-to-release on a frozen target release → cicd_only, not skip;
    Running/Stopped boundary changes force syncing all unlocked releases)
  * app.services.app_service.preview_decision_sync (dry-run, no writes)
  * app.services.app_service.update_snapshot wiring (sync_decision=True uses the
    new rule and returns resulting_decision per applied release)

These exercise the NEW app/ layer only — release_system.core stays frozen.
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

import pytest

import release_system.core as core
from app.domain import decision_sync
from app.services import app_service, cicd_service
from tests.conftest import seed_release


# ---------------------------------------------------------------------------
# Pure domain helpers
# ---------------------------------------------------------------------------

def test_phase_label_maps_known_phases():
    assert decision_sync.phase_label("before_app_freeze") == "App 冻结前"
    assert decision_sync.phase_label("after_app_freeze") == "App 冻结后"
    assert decision_sync.phase_label("after_doc_deadline") == "Doc deadline 后"
    assert decision_sync.phase_label("released_locked") == "已最终锁定"
    # unknown phase falls back to the raw string
    assert decision_sync.phase_label("???") == "???"


def test_resolve_synced_decision_release_gated_on_frozen_phases():
    # release upgrade onto a frozen release → cicd_only
    assert decision_sync.resolve_synced_decision("release", "after_app_freeze") == "cicd_only"
    assert decision_sync.resolve_synced_decision("release", "after_doc_deadline") == "cicd_only"
    # release upgrade before freeze → applied verbatim
    assert decision_sync.resolve_synced_decision("release", "before_app_freeze") == "release"


def test_resolve_synced_decision_non_release_always_verbatim():
    for phase in ("before_app_freeze", "after_app_freeze", "after_doc_deadline"):
        assert decision_sync.resolve_synced_decision("stopped", phase) == "stopped"
        assert decision_sync.resolve_synced_decision("cicd_only", phase) == "cicd_only"


def test_crosses_runtime_boundary_only_for_running_stopped_edges():
    assert decision_sync.crosses_runtime_boundary("release", "stopped") is True
    assert decision_sync.crosses_runtime_boundary("cicd_only", "stopped") is True
    assert decision_sync.crosses_runtime_boundary("stopped", "release") is True
    assert decision_sync.crosses_runtime_boundary("stopped", "cicd_only") is True
    assert decision_sync.crosses_runtime_boundary("release", "cicd_only") is False
    assert decision_sync.crosses_runtime_boundary("cicd_only", "release") is False
    assert decision_sync.crosses_runtime_boundary("stopped", "stopped") is False


def test_runtime_boundary_direction_helpers():
    assert decision_sync.is_running_upgrade("stopped", "release") is True
    assert decision_sync.is_running_upgrade("stopped", "cicd_only") is True
    assert decision_sync.is_running_downgrade("release", "stopped") is True
    assert decision_sync.is_running_downgrade("cicd_only", "stopped") is True
    assert decision_sync.is_running_upgrade("release", "stopped") is False
    assert decision_sync.is_running_downgrade("stopped", "release") is False


# ---------------------------------------------------------------------------
# Service apply + preview fixtures
# ---------------------------------------------------------------------------

NOW = dt.datetime(2026, 5, 15)


def _seed_chain(conn):
    """Seed a base release + three later releases at distinct phases.

    Returns (base_id, app_id, {name: release_id}). The base app starts as
    'release'; the app is cloned into every later release.
    """
    base_id = seed_release(conn)
    app_id = core.normalize_name("TestApp")
    # later releases (created after base → later by created_at)
    before = core.create_release_from_previous(
        conn, "before", app_freeze_deadline="2026-12-31 23:59", doc_deadline="2026-12-31 23:59"
    )
    frozen = core.create_release_from_previous(
        conn, "frozen", app_freeze_deadline="2026-01-01 00:00", doc_deadline="2026-12-31 23:59"
    )
    pastdoc = core.create_release_from_previous(
        conn, "pastdoc", app_freeze_deadline="2026-01-01 00:00", doc_deadline="2026-01-02 00:00"
    )
    return base_id, app_id, {"before": before, "frozen": frozen, "pastdoc": pastdoc}


def test_sync_release_downgrades_to_cicd_only_on_frozen(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        result = app_service.sync_decision_to_later_releases(
            conn, base_id, app_id, "release", user="rm", role="RM"
        )
    applied = {a["release_id"]: a["resulting_decision"] for a in result["applied"]}
    assert applied[rels["before"]] == "release"
    assert applied[rels["frozen"]] == "cicd_only"   # past app-freeze
    assert applied[rels["pastdoc"]] == "cicd_only"  # past doc-deadline
    assert result["skipped"] == []
    # persisted
    for rid, expected in [
        (rels["before"], "release"),
        (rels["frozen"], "cicd_only"),
        (rels["pastdoc"], "cicd_only"),
    ]:
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == expected


def test_sync_non_release_applies_verbatim_everywhere(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        result = app_service.sync_decision_to_later_releases(
            conn, base_id, app_id, "stopped", user="rm", role="RM"
        )
    applied = {a["release_id"]: a["resulting_decision"] for a in result["applied"]}
    assert all(v == "stopped" for v in applied.values())
    for rid in rels.values():
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "stopped"


def test_sync_skips_locked_and_absent(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    # lock the 'frozen' release → skipped with the locked reason
    core.final_lock_release(conn, rels["frozen"])
    # remove the app's snapshot from 'pastdoc' → skipped with the absent reason
    conn.execute(
        "DELETE FROM snapshots WHERE release_id = ? AND app_id = ?",
        (rels["pastdoc"], app_id),
    )
    conn.commit()
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        result = app_service.sync_decision_to_later_releases(
            conn, base_id, app_id, "stopped", user="rm", role="RM"
        )
    skipped = {s["release_id"]: s["reason"] for s in result["skipped"]}
    assert skipped[rels["frozen"]] == "已最终锁定"
    assert skipped[rels["pastdoc"]] == "本 release 无此 app"
    # 'before' still has the app → applied verbatim
    applied = {a["release_id"]: a["resulting_decision"] for a in result["applied"]}
    assert applied == {rels["before"]: "stopped"}


def test_forced_scope_updates_all_other_unlocked_releases(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        result = app_service.sync_decision_to_later_releases(
            conn,
            rels["frozen"],
            app_id,
            "stopped",
            user="rm",
            role="RM",
            scope="all_unlocked",
        )
    applied = {a["release_id"]: a["resulting_decision"] for a in result["applied"]}
    assert applied == {
        base_id: "stopped",
        rels["before"]: "stopped",
        rels["pastdoc"]: "stopped",
    }
    assert core.get_release(conn, rels["frozen"])["snapshots"][app_id]["release_decision"] == "release"
    for rid in (base_id, rels["before"], rels["pastdoc"]):
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "stopped"


def test_preview_running_boundary_uses_all_other_unlocked_releases(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        preview = app_service.preview_decision_sync(
            conn, release_id=rels["frozen"], app_id=app_id, decision="stopped"
        )
    assert preview["forced"] is True
    assert preview["scope"] == "all_unlocked"
    ids = {r["release_id"] for r in preview["releases"]}
    assert ids == {base_id, rels["before"], rels["pastdoc"]}


def test_preview_running_boundary_forces_stopped_to_running(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    core.update_snapshot(
        conn,
        base_id,
        app_id,
        lambda snap: snap.update({"release_decision": "stopped"}),
        skip_doc_deadline=True,
    )
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        preview = app_service.preview_decision_sync(
            conn, release_id=base_id, app_id=app_id, decision="cicd_only"
        )
    assert preview["forced"] is True
    assert preview["scope"] == "all_unlocked"
    ids = {r["release_id"] for r in preview["releases"]}
    assert ids == {rels["before"], rels["frozen"], rels["pastdoc"]}
    by_id = {r["release_id"]: r for r in preview["releases"]}
    assert by_id[rels["before"]]["resulting_decision"] == "cicd_only"
    assert by_id[rels["frozen"]]["resulting_decision"] == "cicd_only"
    assert by_id[rels["pastdoc"]]["resulting_decision"] == "cicd_only"


def test_preview_matches_apply_and_writes_nothing(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    core.final_lock_release(conn, rels["frozen"])
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        preview = app_service.preview_decision_sync(
            conn, release_id=base_id, app_id=app_id, decision="release"
        )
    by_id = {r["release_id"]: r for r in preview["releases"]}
    assert preview["decision"] == "release"
    assert preview["forced"] is False
    assert preview["scope"] == "later"
    # before: applied verbatim, App 冻结前
    assert by_id[rels["before"]]["resulting_decision"] == "release"
    assert by_id[rels["before"]]["phase_label"] == "App 冻结前"
    assert by_id[rels["before"]]["skipped"] is False
    # frozen: locked → skipped
    assert by_id[rels["frozen"]]["skipped"] is True
    assert by_id[rels["frozen"]]["reason"] == "已最终锁定"
    assert by_id[rels["frozen"]]["resulting_decision"] is None
    # pastdoc: gated → cicd_only, Doc deadline 后
    assert by_id[rels["pastdoc"]]["resulting_decision"] == "cicd_only"
    assert by_id[rels["pastdoc"]]["phase_label"] == "Doc deadline 后"

    # preview is read-only: decisions unchanged
    for rid in (rels["before"], rels["pastdoc"]):
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "release"


def test_update_snapshot_uses_new_rule_when_sync_decision_set(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    # base currently 'release'; change to cicd_only with sync → all verbatim cicd_only
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn, base_id, app_id, user="rm", role="RM",
            fields={
                "release_id": base_id,
                "app_id": app_id,
                "snapshot": {"release_decision": "cicd_only"},
                "sync_decision": True,
            },
        )
    assert "decision_sync" in resp
    applied = {a["release_id"]: a["resulting_decision"] for a in resp["decision_sync"]["applied"]}
    assert all(v == "cicd_only" for v in applied.values())


def test_update_snapshot_no_sync_key_skips_non_boundary_decision_sync(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn, base_id, app_id, user="rm", role="RM",
            fields={
                "release_id": base_id,
                "app_id": app_id,
                "snapshot": {"release_decision": "cicd_only"},
                # no sync_decision
            },
        )
    assert "decision_sync" not in resp
    # later releases untouched
    for rid in rels.values():
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "release"


def test_update_snapshot_forces_all_release_sync_across_running_boundary(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn,
            rels["frozen"],
            app_id,
            user="rm",
            role="RM",
            fields={
                "release_id": rels["frozen"],
                "app_id": app_id,
                "snapshot": {"release_decision": "stopped"},
                "sync_decision": False,
            },
        )
    assert resp["snapshot"]["release_decision"] == "stopped"
    assert resp["decision_sync"]["forced"] is True
    applied = {a["release_id"]: a["resulting_decision"] for a in resp["decision_sync"]["applied"]}
    assert applied == {
        base_id: "stopped",
        rels["before"]: "stopped",
        rels["pastdoc"]: "stopped",
    }
    for rid in (base_id, rels["before"], rels["pastdoc"]):
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "stopped"
    assert core.get_release(conn, rels["frozen"])["snapshots"][app_id]["release_decision"] == "stopped"


@pytest.mark.parametrize("finish", ["reject", "cancel"])
def test_rejected_or_cancelled_running_upgrade_rolls_back_synced_releases(temp_db, finish):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    for rid in (base_id, *rels.values()):
        core.update_snapshot(
            conn,
            rid,
            app_id,
            lambda snap: snap.update({"release_decision": "stopped"}),
            skip_doc_deadline=True,
        )

    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn,
            base_id,
            app_id,
            user="rm",
            role="RM",
            fields={
                "release_id": base_id,
                "app_id": app_id,
                "snapshot": {"release_decision": "release"},
                "sync_decision": False,
            },
        )

    req_id = resp["cicd_sync"]["request"]["id"]
    assert core.get_release(conn, base_id)["snapshots"][app_id]["release_decision"] == "stopped"
    assert core.get_release(conn, rels["before"])["snapshots"][app_id]["release_decision"] == "release"
    assert core.get_release(conn, rels["frozen"])["snapshots"][app_id]["release_decision"] == "cicd_only"
    assert core.get_release(conn, rels["pastdoc"])["snapshots"][app_id]["release_decision"] == "cicd_only"

    if finish == "reject":
        cicd_service.reject_request(
            conn,
            req_id,
            reviewer="rm",
            reviewer_role="RM",
            review_note="not ready",
        )
    else:
        cicd_service.cancel_request(conn, req_id, username="rm", role="RM")

    for rid in (base_id, *rels.values()):
        snap = core.get_release(conn, rid)["snapshots"][app_id]
        assert snap["release_decision"] == "stopped"


def test_running_downgrade_decision_sync_request_cannot_be_rejected(temp_db):
    conn = temp_db
    base_id, app_id, rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn,
            base_id,
            app_id,
            user="rm",
            role="RM",
            fields={
                "release_id": base_id,
                "app_id": app_id,
                "snapshot": {"release_decision": "stopped"},
                "sync_decision": False,
            },
        )

    req_id = resp["cicd_sync"]["request"]["id"]
    with pytest.raises(RuntimeError, match="不能拒绝"):
        cicd_service.reject_request(
            conn,
            req_id,
            reviewer="rm",
            reviewer_role="RM",
            review_note="no",
        )

    assert core.get_release(conn, base_id)["snapshots"][app_id]["release_decision"] == "stopped"
    assert core.get_release(conn, rels["before"])["snapshots"][app_id]["release_decision"] == "stopped"


def test_running_downgrade_decision_sync_request_cannot_be_cancelled(temp_db):
    conn = temp_db
    base_id, app_id, _rels = _seed_chain(conn)
    with mock.patch("release_system.core.beijing_now", return_value=NOW):
        resp = app_service.update_snapshot(
            conn,
            base_id,
            app_id,
            user="rm",
            role="RM",
            fields={
                "release_id": base_id,
                "app_id": app_id,
                "snapshot": {"release_decision": "stopped"},
            },
        )

    req_id = resp["cicd_sync"]["request"]["id"]
    with pytest.raises(RuntimeError, match="不能取消"):
        cicd_service.cancel_request(conn, req_id, username="rm", role="RM")
