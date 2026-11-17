"""Pure app_info parsing rules: community artifacts, QA scope widening, scope."""
from __future__ import annotations

from copy import deepcopy

from app.domain import app_info as app_info_domain

BASE_APP_INFO = {
    "app_name": "testapp",
    "app_version": "1.0",
    "app_build": {
        "ubuntu22.04_amd64": {
            "arch": "amd64",
            "os": "ubuntu22.04",
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

    assert app_info_domain.qa_scope_additions(old, new) == ["测试 sanity 新增芯片 N300"]


def test_qa_scope_additions_normalizes_test_chip_case() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    new = app_info_domain.parse_app_info(_with_sanity_chips("C500"))

    assert app_info_domain.qa_scope_additions(old, new) == []


def test_qa_scope_additions_reports_new_tests_chips_and_builds() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    widened = deepcopy(BASE_APP_INFO)
    widened["app_build"]["kylin_arm64"] = {
        "arch": "arm64",
        "os": "kylin",
        "supported_chip": ["x201"],
        "enabled": True,
    }
    widened["app_test"]["perf"] = {
        "test_cmd": "testapp --perf",
        "supported_chip": {"c500": ["ubuntu22.04_amd64"]},
        "enabled": True,
    }
    new = app_info_domain.parse_app_info(widened)

    assert app_info_domain.qa_scope_additions(old, new) == [
        "新增芯片 X201",
        "新增测试 perf",
        "新增构建 kylin/arm64",
    ]


def test_removals_and_command_edits_do_not_widen_scope() -> None:
    old = app_info_domain.parse_app_info(BASE_APP_INFO)
    narrowed = deepcopy(BASE_APP_INFO)
    narrowed["app_test"].pop("regression")
    narrowed["app_test"]["sanity"]["test_cmd"] = "testapp --version --verbose"
    new = app_info_domain.parse_app_info(narrowed)

    assert app_info_domain.qa_scope_additions(old, new) == []


def test_scope_tests_drop_the_tests_the_owner_removed() -> None:
    parsed = app_info_domain.parse_app_info(BASE_APP_INFO)

    assert [t["path"] for t in app_info_domain.scope_tests(parsed)] == ["sanity", "regression"]
    assert [t["path"] for t in app_info_domain.scope_tests(parsed, ["regression"])] == ["sanity"]


def test_declared_dependencies_tell_none_from_an_empty_declaration() -> None:
    assert app_info_domain.declared_dependencies(BASE_APP_INFO) is None
    assert app_info_domain.declared_dependencies({**BASE_APP_INFO, "app_dependency": []}) == []
    assert app_info_domain.declared_dependencies(
        {**BASE_APP_INFO, "app_dependency": ["mcpy", "mcpy", " sdk "]}
    ) == ["mcpy", "sdk"]
    assert app_info_domain.declared_dependencies({**BASE_APP_INFO, "app_dependency": "mcpy"}) == [
        "mcpy"
    ]


def test_content_hash_ignores_key_order_only() -> None:
    reordered = {key: BASE_APP_INFO[key] for key in reversed(list(BASE_APP_INFO))}
    assert app_info_domain.content_hash(BASE_APP_INFO) == app_info_domain.content_hash(reordered)
    changed = deepcopy(BASE_APP_INFO)
    changed["app_version"] = "2.0"
    assert app_info_domain.content_hash(changed) != app_info_domain.content_hash(BASE_APP_INFO)


def test_runtime_dependencies_pair_labels_within_each_enabled_build() -> None:
    raw = {"app_build": {
        "ubuntu_amd64": {"python_label": " py310 ", "pytorch_label": "torch2.4"},
        "ubuntu_arm64": {"python_label": "py310", "pytorch_label": "torch2.4"},
        "kylin_amd64": {"python_label": "py312", "pytorch_label": "torch2.8"},
        "disabled": {"python_label": "py399", "pytorch_label": "torch9", "enabled": False},
        "disabled_string": {"python_label": "py399", "pytorch_label": "torch9", "enabled": "false"},
        "python_only": {"python_label": "py311"},
        "torch_only": {"pytorch_label": "torch2.5"},
    }}

    assert app_info_domain.python_pytorch_dependencies(raw) == [
        "torch 2.4 + py310",
        "torch 2.8 + py312",
    ]
    assert app_info_domain.python_pytorch_dependencies({}) == []


def test_runtime_dependencies_use_build_label_options_and_inherited_defaults() -> None:
    raw = {"pytorch_label": "torch2.8", "app_build": {
        "options_amd64": {"python_label": ["py310", "py311"], "pytorch_label": "torch2.5,torch2.6"},
        "inherited_arm64": {"python_label": "py312"},
    }}

    assert app_info_domain.python_pytorch_dependencies(raw) == [
        "torch 2.5 + py310", "torch 2.5 + py311",
        "torch 2.6 + py310", "torch 2.6 + py311",
        "torch 2.8 + py312",
    ]
