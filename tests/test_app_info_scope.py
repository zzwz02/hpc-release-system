from __future__ import annotations

from copy import deepcopy

import pytest

from app.domain import app_info as app_info_domain
from app.repositories import apps_repo
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


def _with_community_pub() -> dict:
    app_info = deepcopy(BASE_APP_INFO)
    app_info["app_build"] = {
        "ubuntu22.04_amd64": {
            "arch": "amd64",
            "supported_chip": ["c500"],
            "pub": {"img": ["ai4s"], "pkg": ["AI4S"]},
        },
        "kylin_arm64": {
            "arch": "arm64",
            "supported_chip": ["x201"],
            "enabled": True,
            "pub": {"img": ["ai4s"]},
        },
        "disabled": {
            "arch": "amd64",
            "supported_chip": ["n300"],
            "enabled": False,
            "pub": {"img": ["ai4s"], "pkg": ["ai4s"]},
        },
        "string_disabled": {
            "arch": "amd64",
            "supported_chip": ["n300"],
            "enabled": "false",
            "pub": {"img": ["ai4s"], "pkg": ["ai4s"]},
        },
    }
    return app_info


def test_community_artifacts_count_enabled_app_build_aliases() -> None:
    parsed = app_info_domain.parse_app_info(_with_community_pub())

    community = parsed["community_artifacts"]

    assert community["release_status"] == "镜像：2，二进制包：1"
    assert community["image_aliases"] == ["ubuntu22.04_amd64", "kylin_arm64"]
    assert community["pkg_aliases"] == ["ubuntu22.04_amd64"]


def test_qa_scope_additions_reports_chip_added_to_existing_test_path() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    new = app_info_domain.parse_app_info(_with_sanity_chips("c500", "n300"))

    assert app_info_domain.qa_scope_additions(old, new) == [
        "测试 sanity 新增芯片 N300"
    ]


def test_apply_app_info_derives_community_fields_and_app_config(
    release_with_app,
) -> None:
    conn, release_id, app_id = release_with_app

    result = app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=_with_community_pub(),
        uploaded_by="owner_test",
        role="Owner",
    )
    snapshot = result["snapshot"]
    app = apps_repo.get_app(conn, app_id)

    assert snapshot["community"]["release_status"] == "镜像：2，二进制包：1"
    assert snapshot["community"]["menu_level1"] == ""
    assert snapshot["community"]["doc_url"] == ""
    assert snapshot["community"]["description"] == ""
    assert snapshot["community"]["image_count"] == 2
    assert snapshot["community"]["pkg_count"] == 1
    assert app["cicd_community_artifact"] == "image, pkg"
    missing_texts = [item["text"] for item in snapshot["missing_items"]]
    assert "缺少社区发布情况" not in missing_texts
    assert "缺少社区一级菜单" in missing_texts
    assert "缺少社区说明文档链接" in missing_texts
    assert "缺少社区描述" in missing_texts
    assert "缺少社区包支持 Python 版本" in missing_texts
    assert "缺少社区包支持框架及版本" in missing_texts


def test_app_info_preserves_community_doc_metadata_when_artifacts_remain() -> None:
    community = app_info_domain.community_fields_from_app_info(
        _with_community_pub(),
        {
            "menu_level1": "AI4Sci 应用",
            "doc_url": "https://docs.example.com/app",
            "description": "社区说明",
            "python_version": "Python 3.10",
            "framework_version": "PyTorch 2.1",
        },
    )

    assert community["menu_level1"] == "AI4Sci 应用"
    assert community["doc_url"] == "https://docs.example.com/app"
    assert community["description"] == "社区说明"
    assert community["python_version"] == "Python 3.10"
    assert community["framework_version"] == "PyTorch 2.1"


def test_app_info_clears_community_doc_metadata_without_artifacts() -> None:
    community = app_info_domain.community_fields_from_app_info(
        BASE_APP_INFO,
        {
            "menu_level1": "AI4Sci 应用",
            "doc_url": "https://docs.example.com/app",
            "description": "社区说明",
            "python_version": "Python 3.10",
            "framework_version": "PyTorch 2.1",
        },
    )

    assert community["release_status"] == ""
    assert community["menu_level1"] == ""
    assert community["doc_url"] == ""
    assert community["description"] == ""
    assert community["python_version"] == ""
    assert community["framework_version"] == ""


def test_refresh_missing_items_requires_partial_community_doc_metadata(
    release_with_app,
) -> None:
    conn, release_id, app_id = release_with_app
    app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=_with_community_pub(),
        uploaded_by="owner_test",
        role="Owner",
    )
    snapshot = snapshots_repo.get_snapshot(conn, release_id, app_id)
    assert snapshot is not None
    snapshot["community"].update({
        "menu_level1": "AI4Sci 应用",
        "python_version": "Python 3.10",
        "framework_version": "PyTorch 2.1",
    })
    snapshots_repo.save_snapshot(conn, release_id, app_id, snapshot)

    missing = app_service.refresh_missing_items(conn, release_id)[app_id]
    missing_texts = [item["text"] for item in missing]

    assert "缺少社区一级菜单" not in missing_texts
    assert "缺少社区说明文档链接" in missing_texts
    assert "缺少社区描述" in missing_texts
    assert "缺少社区包支持 Python 版本" not in missing_texts
    assert "缺少社区包支持框架及版本" not in missing_texts


def test_apply_app_info_image_only_does_not_require_package_metadata(
    release_with_app,
) -> None:
    conn, release_id, app_id = release_with_app
    app_info = _with_community_pub()
    app_info["app_build"]["ubuntu22.04_amd64"]["pub"] = {"img": ["ai4s"]}

    result = app_service.apply_app_info(
        conn,
        release_id=release_id,
        app_id=app_id,
        app_info=app_info,
        uploaded_by="owner_test",
        role="Owner",
    )
    snapshot = result["snapshot"]

    assert snapshot["community"]["release_status"] == "镜像：2"
    assert snapshot["community"]["pkg_count"] == 0
    missing_texts = [item["text"] for item in snapshot["missing_items"]]
    assert "缺少社区一级菜单" in missing_texts
    assert "缺少社区说明文档链接" in missing_texts
    assert "缺少社区描述" in missing_texts
    assert "缺少社区包支持 Python 版本" not in missing_texts
    assert "缺少社区包支持框架及版本" not in missing_texts


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
