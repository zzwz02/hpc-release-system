"""Look up which cycle, environment, test scope and test commands belong to a
delivered image digest or an App."""
from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories import apps_repo, entries_repo, releases_repo
from app.services import catalog_service, cycle_service, qa_service


def _entry_record(conn: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    release = cycle_service.get_release(conn, entry["release_id"])
    app = apps_repo.get_app(conn, entry["app_id"]) or {"id": entry["app_id"]}
    view = cycle_service.entry_view(conn, entry)
    image = releases_repo.get_base_image(conn, entry["base_image_id"]) if entry.get("base_image_id") else None
    return {
        "app_id": entry["app_id"],
        "app_name": catalog_service.app_name(app),
        "release": {
            "id": release["id"],
            "name": release["name"],
            "phase": release["phase"],
            "release_date": release["release_date"],
        },
        "environment": releases_repo.list_environment(conn, release["id"]),
        "base_image": image,
        "entry": {
            "id": entry["id"],
            "status": entry["status"],
            "target_version": entry["target_version"],
            "actual_delivery": entry["actual_delivery"],
            "known_issues": entry["known_issues"],
            "qa_status": entry["qa_status"],
            "qa_note": entry["qa_note"],
            "result": entry["result"],
            "deliverable_image": entry["deliverable_image"],
            "deliverable_digest": entry["deliverable_digest"],
            "deliverable_commit": entry["deliverable_commit"],
        },
        "app_info": view["locked_app_info"] and {
            key: view["locked_app_info"][key]
            for key in ("id", "commit_id", "app_version", "x86_chips", "arm_chips")
        },
        "scope_tests": [t for t in view["scope_tests"] if not t["excluded"]],
        "test_commands": qa_service.entry_test_commands(conn, entry),
    }


def by_digest(conn: sqlite3.Connection, digest: str) -> dict[str, Any]:
    digest = (digest or "").strip()
    if not digest:
        raise ValueError("请输入镜像 digest")
    entries = entries_repo.by_digest(conn, digest)
    if not entries and not digest.startswith("sha256:"):
        entries = entries_repo.by_digest(conn, f"sha256:{digest}")
    return {
        "digest": digest,
        "entries": [_entry_record(conn, entry) for entry in entries],
        "base_images": [
            {**image, "release_name": (releases_repo.get_release_row(conn, image["release_id"]) or {}).get("name", "")}
            for image in releases_repo.base_images_by_digest(conn, digest)
        ],
    }


def by_app(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    app = catalog_service.get_app_or_raise(conn, app_id)
    return {
        "app_id": app_id,
        "app_name": catalog_service.app_name(app),
        "cicd_status": app["cicd_status"],
        "entries": [_entry_record(conn, entry) for entry in reversed(entries_repo.list_for_app(conn, app_id))],
    }
