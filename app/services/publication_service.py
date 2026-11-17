"""Read frozen publication evidence, with explicitly identified legacy reconstruction."""
from __future__ import annotations

import sqlite3

from app.api.errors import ApiError
from app.domain import delivery_images, publication_manifest
from app.repositories import app_info_repo, entries_repo, releases_repo


def read_manifest(conn: sqlite3.Connection, official: dict, *, app: dict, app_name: str) -> tuple[dict | None, str]:
    saved = official.get("test_manifest")
    if saved is not None:
        try:
            publication_manifest.validate(saved, official)
        except ValueError as exc:
            raise ApiError(409, str(exc)) from exc
        return saved, "sealed"

    version = app_info_repo.get_version(conn, official.get("app_info_version_id"))
    if not version or version["app_id"] != app["id"]:
        return None, "unavailable"
    release = releases_repo.get_release_row(conn, official["release_id"])
    release = {**release, "environment": releases_repo.list_environment(conn, release["id"]),
               "base_images": releases_repo.list_base_images(conn, release["id"])}
    entry = entries_repo.get_entry(conn, official["entry_id"]) if official.get("entry_id") else None
    entry = {"excluded_tests": [], "qa_status": "", "qa_note": "", **(entry or {}),
             "id": official.get("entry_id"), "target_version": official["version"]}
    # This is a read-time reconstruction, never persisted as a publication-time archive.
    return publication_manifest.capture(
        app=app, app_name=app_name, release=release, entry=entry, version=version,
        images=delivery_images.recorded(official["images"], official["image"], official["digest"]),
        published_at=official["released_at"], published_by="",
    ), "reconstructed"
