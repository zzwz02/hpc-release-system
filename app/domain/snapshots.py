"""Snapshot/app-view domain helpers — ported from core.py:536-573, 938-993, 1068-1120.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

from typing import Any

from app.domain.textutil import normalize_name

MAX_APP_DESCRIPTION_CHARS = 30

SNAPSHOT_META_FIELDS = ("official_name", "type", "official_url", "description", "doc_target", "owners")
APP_META_LABELS = {
    "official_name": "官方名称",
    "type": "App类型",
    "official_url": "官方 URL",
    "description": "描述",
    "doc_target": "文档类型",
    "owners": "Owner",
}


def normalize_doc_target(value: str | None) -> str:
    target = (value or "manual").strip()
    aliases = {
        "HPC": "manual",
        "hpc": "manual",
        "manual": "manual",
        "AI4Sci": "ai4sci",
        "ai4sci": "ai4sci",
        "AI4SCI": "ai4sci",
    }
    return aliases.get(target, "manual")


def app_description_count(value: str | None) -> int:
    text = (value or "").strip()
    count = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isascii() and ch.isalnum():
            while i < len(text) and text[i].isascii() and text[i].isalnum():
                i += 1
            count += 1
            continue
        count += 1
        i += 1
    return count


def normalize_app_description(value: str | None) -> str:
    description = (value or "").strip()
    if app_description_count(description) > MAX_APP_DESCRIPTION_CHARS:
        raise ValueError(f"描述不能超过{MAX_APP_DESCRIPTION_CHARS}字")
    return description


def display_name(official_name: str | None, version: str | None = "") -> str:
    """Human-facing app name: official name plus version when known."""
    official = (official_name or "").strip()
    ver = (version or "").strip()
    return f"{official} {ver}".strip() if ver else official


def app_view(app: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Merge the global app row with per-release snapshot metadata.

    official_name/type/official_url/description/doc_target/owners live on the
    snapshot (per-release); id/git_url/git_branch are global app identity.
    """
    snapshot = snapshot or {}
    view = {
        "id": app.get("id", ""),
        "git_url": app.get("git_url", ""),
        "git_branch": app.get("git_branch", ""),
        "aliases": app.get("aliases", []),
        "created_by": app.get("created_by", ""),
        "created_at": app.get("created_at", ""),
        "official_name": snapshot.get("official_name", ""),
        "type": snapshot.get("type", ""),
        "official_url": snapshot.get("official_url", ""),
        "description": snapshot.get("description", ""),
        "doc_target": normalize_doc_target(snapshot.get("doc_target")),
        "owners": list(snapshot.get("owners", []) or []),
        "version": snapshot.get("version", ""),
    }
    view["name"] = display_name(view["official_name"], view["version"])
    return view


def variant_app_id(base_id: str, version: str, branch: str, used_ids: set[str]) -> str:
    suffix = normalize_name(version) or normalize_name(branch) or "variant"
    candidate = f"{base_id}_{suffix}" if suffix else base_id
    branch_suffix = normalize_name(branch)
    if candidate in used_ids and branch_suffix and branch_suffix not in candidate:
        candidate = f"{candidate}_{branch_suffix}"
    index = 2
    original = candidate
    while candidate in used_ids:
        candidate = f"{original}_{index}"
        index += 1
    return candidate


def base_snapshot(
    app_id: str,
    *,
    official_name: str = "",
    app_type: str = "",
    official_url: str = "",
    description: str = "",
    doc_target: str = "manual",
    owners: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "official_name": official_name,
        "type": app_type,
        "official_url": official_url,
        "description": description,
        "doc_target": normalize_doc_target(doc_target),
        "owners": sorted(set(owners or [])),
        "release_decision": "release",
        "qa_status": "not_checked",
        "qa_issue_note": "",
        "owner_confirmed": False,
        "version": "",
        "x86_chips": "",
        "arm_chips": "",
        "hpcc_chip": "",
        "arch": "",
        "python_labels": "",
        "pytorch_labels": "",
        "build_os": "",
        "build_arches": "",
        "maca_version": "",
        "doc": {
            "intro": "",
            "image_usage": "",
            "binary_usage": "",
            "env_setup": "",
            "limitations": "",
        },
        "community": {
            "release_status": "",
            "python_version": "",
            "framework_version": "",
        },
        "sanity": {
            "arm_kylin": False,
            "ubuntu": False,
        },
        "app_info": None,
        "app_info_diffs": [],
        "test_docs": [],
        "missing_items": [],
    }
