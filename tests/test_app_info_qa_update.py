"""QA maintains app_info until the doc deadline.

Covers the role rule (QA may update any app's Gerrit app_info before the doc
deadline) and the app-freeze handshake (only QA may take on a QA-scope
expansion after app freeze, and only after accepting the listed changes).
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.api.errors import AuthzError
from app.repositories import snapshots_repo
from app.services import app_service
from app.services.authz import require_app_info_update


BASE_APP_INFO = {
    "app_name": "testapp",
    "app_version": "1.0",
    "app_build": {
        "ubuntu22.04_amd64": {
            "arch": "amd64",
            "supported_chip": ["c500", "n300"],
            "enabled": True,
        }
    },
    "app_test": {
        "sanity": {
            "test_cmd": "testapp --version",
            "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
            "enabled": True,
        },
    },
}


def _with_sanity_chips(*chips: str) -> dict:
    app_info = deepcopy(BASE_APP_INFO)
    app_info["app_test"]["sanity"]["supported_chip"] = {
        chip: ["ubuntu22.04_amd64"] for chip in chips
    }
    return app_info


def _seed_app_info(conn, release_id: str, app_id: str) -> None:
    app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=BASE_APP_INFO,
        uploaded_by="owner_test",
        role="Owner",
    )


def _pass_app_freeze(conn, release_id: str) -> None:
    conn.execute(
        "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
        ("2000-01-01 00:00", "2099-01-01 00:00", release_id),
    )


def _pass_doc_deadline(conn, release_id: str) -> None:
    conn.execute(
        "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
        ("2000-01-01 00:00", "2000-02-01 00:00", release_id),
    )


def test_qa_may_update_app_info_of_any_app() -> None:
    require_app_info_update(["owner_test"], "qa_user", "QA")


def test_owner_stays_limited_to_owned_apps_and_guest_is_denied() -> None:
    require_app_info_update(["owner_test"], "owner_test", "Owner")
    with pytest.raises(AuthzError):
        require_app_info_update(["owner_test"], "other_owner", "Owner")
    with pytest.raises(AuthzError):
        require_app_info_update(["owner_test"], "guest_user", "Guest")


def test_qa_update_before_app_freeze_applies_directly(release_with_app) -> None:
    conn, release_id, app_id = release_with_app
    _seed_app_info(conn, release_id, app_id)

    app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=_with_sanity_chips("c500", "n300"),
        uploaded_by="qa_user",
        role="QA",
    )

    snapshot = snapshots_repo.get_snapshot(conn, release_id, app_id)
    assert snapshot["app_info"]["uploaded_by"] == "qa_user"


def test_qa_scope_expansion_after_app_freeze_needs_acceptance(release_with_app) -> None:
    conn, release_id, app_id = release_with_app
    _seed_app_info(conn, release_id, app_id)
    before = snapshots_repo.get_snapshot(conn, release_id, app_id)
    _pass_app_freeze(conn, release_id)

    with pytest.raises(app_service.QaScopeExpansionPending) as excinfo:
        app_service.apply_app_info(
            conn,
            release_id=release_id,
            app_id=app_id,
            app_info=_with_sanity_chips("c500", "n300"),
            uploaded_by="qa_user",
            role="QA",
        )

    pending = excinfo.value
    assert pending.scope_additions == ["测试 sanity 新增芯片 N300"]
    assert pending.changes, "QA needs the concrete app_info changes to accept"
    payload = pending.payload()
    assert payload["requires_scope_confirmation"] is True
    assert payload["scope_additions"] == pending.scope_additions
    # Nothing is written until QA accepts.
    assert snapshots_repo.get_snapshot(conn, release_id, app_id) == before


def test_qa_acceptance_applies_expanded_scope_and_is_audited(release_with_app) -> None:
    conn, release_id, app_id = release_with_app
    _seed_app_info(conn, release_id, app_id)
    _pass_app_freeze(conn, release_id)

    app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=_with_sanity_chips("c500", "n300"),
        uploaded_by="qa_user",
        role="QA",
        accept_scope_expansion=True,
    )

    snapshot = snapshots_repo.get_snapshot(conn, release_id, app_id)
    sanity = next(
        test
        for test in snapshot["app_info"]["parsed"]["tests"]
        if test["path"] == "sanity"
    )
    assert {chip.lower() for chip in sanity["supported_chips"]} == {"c500", "n300"}
    events = [
        row["event"]
        for row in conn.execute(
            "SELECT event FROM audit WHERE app_id=?", (app_id,)
        ).fetchall()
    ]
    assert "qa_accept_scope_expansion" in events


def test_other_roles_cannot_expand_scope_after_app_freeze(release_with_app) -> None:
    conn, release_id, app_id = release_with_app
    _seed_app_info(conn, release_id, app_id)
    _pass_app_freeze(conn, release_id)

    for role in ("RM", "Owner"):
        with pytest.raises(RuntimeError, match="测试 sanity 新增芯片 N300") as excinfo:
            app_service.apply_app_info(
                conn,
                release_id=release_id,
                app_id=app_id,
                app_info=_with_sanity_chips("c500", "n300"),
                uploaded_by="rm_user",
                role=role,
                # An accept flag from a role without the capability changes nothing.
                accept_scope_expansion=True,
            )
        assert not isinstance(excinfo.value, app_service.QaScopeExpansionPending)


def test_qa_cannot_update_app_info_after_doc_deadline(release_with_app) -> None:
    conn, release_id, app_id = release_with_app
    _seed_app_info(conn, release_id, app_id)
    _pass_doc_deadline(conn, release_id)

    with pytest.raises(RuntimeError, match="doc deadline"):
        app_service.apply_app_info(
            conn,
            release_id=release_id,
            app_id=app_id,
            app_info=_with_sanity_chips("c500", "n300"),
            uploaded_by="qa_user",
            role="QA",
            accept_scope_expansion=True,
        )
