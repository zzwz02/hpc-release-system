from __future__ import annotations

from app.domain import cicd_config


def test_shared_cicd_config_defaults_and_field_mapping() -> None:
    assert cicd_config.CICD_REPO_TYPE_OPTIONS == ("git", "repo")
    assert cicd_config.CICD_REPO_TYPE_DEFAULT == "git"
    assert cicd_config.CICD_TEST_TIMEOUT_DEFAULT == 40
    assert cicd_config.CICD_APP_FIELD_TO_PAYLOAD_FIELD == {
        "cicd_repo_type": "repo_type",
        "cicd_community_artifact": "community_artifact",
        "cicd_build_image": "build_image",
        "cicd_test_timeout": "test_timeout",
        "cicd_notes": "notes",
    }


def test_shared_cicd_config_normalization() -> None:
    assert cicd_config.normalize_repo_type("repo") == "repo"
    assert cicd_config.normalize_repo_type("unknown") == "git"
    assert cicd_config.normalize_test_timeout("75") == 75
    assert cicd_config.normalize_test_timeout("invalid") == 40
    assert cicd_config.normalize_test_timeout(0) == 40
    assert cicd_config.normalize_community_artifacts("镜像, package, image") == [
        "image",
        "pkg",
    ]


def test_app_config_normalization_uses_shared_schema() -> None:
    assert cicd_config.normalize_app_config(
        {
            "cicd_repo_type": "invalid",
            "cicd_community_artifact": ["软件包", "image", "pkg"],
            "cicd_build_image": "  image:latest  ",
            "cicd_test_timeout": "-1",
            "cicd_notes": "  note  ",
            "unknown": "discarded",
        }
    ) == {
        "cicd_repo_type": "git",
        "cicd_community_artifact": "pkg, image",
        "cicd_build_image": "image:latest",
        "cicd_test_timeout": "40",
        "cicd_notes": "note",
    }
