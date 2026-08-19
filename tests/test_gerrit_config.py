from __future__ import annotations

import json
from pathlib import Path

from app import identity
from app.config import (
    DEFAULT_GERRIT_MANIFEST_BRANCH,
    DEFAULT_GERRIT_SSH_BASE_URL,
    settings,
)
from app.integrations.gerrit import gerrit_remote_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG_PATH = PROJECT_ROOT / "shared" / "integrations.json"
PATH_CONTRACT_PATH = PROJECT_ROOT / "tests" / "contracts" / "gerrit_paths.json"


def _shared_gerrit() -> dict[str, str]:
    return json.loads(SHARED_CONFIG_PATH.read_text(encoding="utf-8"))["gerrit"]


def _expand_contract_value(value: str) -> str:
    replacements = {
        "{gerrit_hpc_base}": settings.gerrit_hpc_base_url,
        "{gerrit_hpc_project}": settings.gerrit_hpc_project,
        "{gerrit_manifest_project}": settings.gerrit_manifest_project,
        "{gerrit_manifest_repo_url}": settings.manifest_repo_url,
    }
    for placeholder, replacement in replacements.items():
        value = value.replace(placeholder, replacement)
    return value


def test_backend_gerrit_urls_are_derived_from_shared_config():
    shared = _shared_gerrit()
    ssh_base = shared["ssh_base_url"].rstrip("/")
    hpc_project = shared["hpc_project"].strip("/")
    manifest_project = shared["manifest_project"].strip("/")
    manifest_branch = shared["manifest_branch"].strip()
    configured_ssh_base = settings.gerrit_ssh_base_url
    hpc_base = f"{configured_ssh_base}/{hpc_project}"

    assert DEFAULT_GERRIT_SSH_BASE_URL == ssh_base
    assert DEFAULT_GERRIT_MANIFEST_BRANCH == manifest_branch
    assert settings.gerrit_hpc_base_url == hpc_base
    assert settings.hpc_gerrit_root == f"{configured_ssh_base}/"
    assert settings.hpc_gerrit_prefix == f"{hpc_base}/"
    assert settings.manifest_repo_url == f"{hpc_base}/{manifest_project}"
    assert settings.manifest_repo_base == f"{hpc_base}/"
    assert identity.RESOLVED_REPO_BASE == hpc_base
    assert identity.MANIFEST_REPO_URL == settings.manifest_repo_url
    assert identity.MANIFEST_BRANCH == settings.gerrit_manifest_branch == manifest_branch


def test_one_base_url_override_updates_gerrit_fetch_and_identity(monkeypatch):
    replacement = "ssh://gerrit.example.test:29418"
    monkeypatch.setattr(settings, "gerrit_ssh_base_url", replacement)
    identity.clear_manifest_cache()

    expected_hpc_base = f"{replacement}/{settings.gerrit_hpc_project}"
    assert gerrit_remote_url("hpc_demo") == f"{expected_hpc_base}/hpc_demo"
    assert (
        gerrit_remote_url(f"{settings.gerrit_hpc_project}/hpc_demo")
        == f"{expected_hpc_base}/hpc_demo"
    )
    assert identity.normalize_git_url("hpc_demo") == f"{expected_hpc_base}/hpc_demo"
    assert settings.manifest_repo_url.startswith(f"{expected_hpc_base}/")


def test_repo_storage_path_keeps_database_identity_origin_free():
    hpc_project = settings.gerrit_hpc_project
    manifest_project = settings.gerrit_manifest_project

    assert identity.repo_storage_path("git", "hpc_demo") == "hpc_demo"
    assert (
        identity.repo_storage_path(
            "git",
            f"ssh://gerrit.example.test:29418/{hpc_project}/team/hpc_demo",
        )
        == "team/hpc_demo"
    )
    assert (
        identity.repo_storage_path(
            "repo",
            f"ssh://gerrit.example.test:29418/{hpc_project}/"
            f"{manifest_project}/APP/demo/default.xml",
        )
        == "APP/demo/default.xml"
    )


def test_python_identity_matches_shared_path_contract():
    contract = json.loads(PATH_CONTRACT_PATH.read_text(encoding="utf-8"))
    for case in contract["cases"]:
        input_value = _expand_contract_value(case["input"])
        assert identity.repo_storage_path(case["repo_type"], input_value) == (
            _expand_contract_value(case["storage_path"])
        ), case["name"]
        assert identity.normalize_git_url(input_value) == (
            _expand_contract_value(case["normalized_url"])
        ), case["name"]


def test_offline_report_reuses_authoritative_identity_implementation():
    from test_data import get_release_report_test_cmd as report_script

    assert report_script.normalize_git_url is identity.normalize_git_url
    assert report_script.resolve_manifest_url is identity.resolve_manifest_url


def test_runtime_sources_do_not_duplicate_shared_gerrit_base_url():
    """Changing the shared default must not require editing another runtime file."""
    ssh_base = _shared_gerrit()["ssh_base_url"]
    source_roots = (
        PROJECT_ROOT / "app",
        PROJECT_ROOT / "web" / "src",
        PROJECT_ROOT / "tools",
        PROJECT_ROOT / "test_data",
    )
    duplicates = []
    for root in source_roots:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx", ".js"}:
                continue
            if ssh_base in path.read_text(encoding="utf-8"):
                duplicates.append(str(path.relative_to(PROJECT_ROOT)))
    assert duplicates == []
