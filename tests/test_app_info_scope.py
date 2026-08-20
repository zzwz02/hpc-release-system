from __future__ import annotations

from copy import deepcopy

import pytest

from app.domain import app_info as app_info_domain
from app.repositories import snapshots_repo
from app.services import app_service


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
        "regression": {
            "test_cmd": "testapp --regression",
            "supported_chip": {"n300": ["ubuntu22.04_amd64"]},
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


def test_qa_scope_additions_reports_chip_added_to_existing_test_path() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    new = app_info_domain.parse_app_info(_with_sanity_chips("c500", "n300"))

    assert app_info_domain.qa_scope_additions(old, new) == [
        "测试 sanity 新增芯片 N300"
    ]


def test_qa_scope_additions_normalizes_test_chip_case() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    new = app_info_domain.parse_app_info(_with_sanity_chips("C500"))

    assert app_info_domain.qa_scope_additions(old, new) == []


def test_app_freeze_blocks_chip_added_to_existing_test_path(
    release_with_app,
) -> None:
    conn, release_id, app_id = release_with_app
    app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=BASE_APP_INFO,
        uploaded_by="owner_test",
        role="Owner",
    )
    before = snapshots_repo.get_snapshot(conn, release_id, app_id)

    conn.execute(
        "UPDATE releases SET app_freeze_deadline=?, doc_deadline=? WHERE id=?",
        ("2000-01-01 00:00", "2099-01-01 00:00", release_id),
    )

    with pytest.raises(RuntimeError, match="测试 sanity 新增芯片 N300"):
        app_service.apply_app_info(
            conn,
            release_id=release_id,
            app_id=app_id,
            app_info=_with_sanity_chips("c500", "n300"),
            uploaded_by="owner_test",
            role="Owner",
        )

    assert snapshots_repo.get_snapshot(conn, release_id, app_id) == before
