"""Look up which cycle, environment, test scope and test commands belong to a
delivered image digest or an App."""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app.api.errors import GerritNetworkError
from app.config import settings
from app.domain import cycles, delivery_images, image_lookup
from app.domain.container_image import delivered_image_reference
from app.integrations import gerrit, harbor
from app.repositories import app_info_repo, apps_repo, entries_repo, official_repo, releases_repo
from app.services import catalog_service, cycle_service, qa_service


def _lookup_image(reference: str) -> dict[str, str]:
    image = image_lookup.parse_lookup_image(reference)
    if image["host"] != harbor.HARBOR_HOST:
        raise ValueError(f"仅支持 {harbor.HARBOR_HOST} 的镜像")
    return image


def _image_repository(conn: sqlite3.Connection, image: dict[str, str]) -> str:
    image_name = image["repository"].rsplit("/", 1)[-1]
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    candidate_ids = set()
    # App identity comes from the catalog, never a guessed hpc_<image> URL.
    for version in app_info_repo.image_lookup_sources(conn):
        if image_name in image_lookup.image_repositories(version["raw"]):
            candidate_ids.add(version["app_id"])
    if not candidate_ids:
        raise ValueError("未找到该镜像对应的 App 仓库，请先在 App 工作台登记并同步该 App")
    urls = {gerrit.gerrit_remote_url(catalog_service.app_info_fetch_target(apps[app_id])[0])
            for app_id in candidate_ids}
    if len(urls) != 1:
        raise ValueError("多个 Gerrit 仓库声明了相同镜像名，无法唯一定位历史 app_info")
    return urls.pop()


def _app_info_at(url: str, commit: str) -> dict[str, Any]:
    try:
        raw = gerrit.fetch_app_info_at(url, commit, project_root=settings.db_path.parent)
    except RuntimeError as exc:
        raise GerritNetworkError(f"无法读取 Gerrit {url} @ {commit} 的 app_info.json") from exc
    try:
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("app_info 不是 JSON 对象")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("该 commit 的 app_info.json 格式无效，无法生成测试命令") from exc
    return document


def app_info_source(conn: sqlite3.Connection, reference: str, commit: str) -> dict[str, Any]:
    """Read the original file at the pinned commit without mutable Harbor tags."""
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError("app_info 来源需要有效的 commit id")
    image = _lookup_image(reference)
    return _app_info_at(_image_repository(conn, image), commit)


def by_image(conn: sqlite3.Connection, reference: str) -> dict[str, Any]:
    image = _lookup_image(reference)
    artifact = harbor.get_artifact(image["project"], image["repository"], image["digest"])
    tags = sorted({tag["name"] for tag in artifact.get("tags") or []
                   if isinstance(tag, dict) and isinstance(tag.get("name"), str)})
    commit, selected_tags = image_lookup.commit_tags(tags)
    url = _image_repository(conn, image)
    document = _app_info_at(url, commit)
    try:
        result = image_lookup.historical_image_commands(
            document, image=image["image"], repository=image["repository"], tags=selected_tags,
            architecture=str((artifact.get("extra_attrs") or {}).get("architecture") or ""),
        )
    except (TypeError, AttributeError, KeyError) as exc:
        raise ValueError("该 commit 的 app_info.json 格式无效，无法生成测试命令") from exc
    return {**result, "image": image["image"], "digest": image["digest"], "tags": tags,
            "commit_id": commit, "commit_tags": selected_tags, "git_url": url}


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
            "deliverable_images": delivery_images.recorded(entry.get('deliverable_images') or [],
                entry['deliverable_image'], entry['deliverable_digest'], entry['deliverable_commit']),
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


def published_releases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Published and locked cycles, newest first; no live catalog is needed."""
    return [
        {key: release[key] for key in ("id", "name", "release_date")}
        for release in reversed(releases_repo.list_release_rows(conn))
        if release["released_locked"]
    ]


def published_apps(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise ValueError("版本不存在，请重新选择已冻结版本")
    if not release["released_locked"]:
        raise ValueError("该版本尚未发布冻结或已解锁，请重新选择已冻结版本")
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    rows = []
    for entry in entries_repo.list_for_release(conn, release_id):
        if entry["result"] != cycles.RESULT_RELEASED:
            continue
        app = apps.get(entry["app_id"]) or {"id": entry["app_id"]}
        rows.append({
            "app_id": entry["app_id"],
            "app_name": catalog_service.app_name(app),
            "app_version": entry["target_version"],
            "image_reference": delivered_image_reference(entry["deliverable_image"], entry["deliverable_digest"]),
            "images": delivery_images.recorded(entry.get('deliverable_images') or [],
                entry['deliverable_image'], entry['deliverable_digest'], entry['deliverable_commit']),
            "deliverable_image": entry["deliverable_image"],
            "deliverable_digest": entry["deliverable_digest"],
            "qa_status": entry["qa_status"],
            "qa_note": entry["qa_note"],
        })
    rows.sort(key=lambda row: (row["app_name"].casefold(), row["app_id"]))
    return {
        "release": {key: release[key] for key in ("id", "name", "release_date")},
        "apps": rows,
    }


def latest_running_releases(conn: sqlite3.Connection) -> dict[str, Any]:
    """Latest successful publication for every running App, including migration-only history."""
    officials = official_repo.current_by_app(conn)
    rows = []
    apps = sorted((app for app in apps_repo.list_apps(conn) if app["cicd_status"] == cycles.CICD_RUNNING),
                  key=lambda app: (catalog_service.app_name(app).casefold(), app["id"]))
    for app in apps:
        official = officials.get(app["id"])
        if not official:
            official = next((record for record in official_repo.history_for_app(conn, app["id"])
                             if not record["withdrawn"]), None)
        common = {"app_id": app["id"], "app_name": catalog_service.app_name(app),
                  "hpc_version": official["release_name"] if official else "",
                  "app_version": official["version"] if official else "",
                  "app_info_version_id": official.get("app_info_version_id") if official else None}
        if not official:
            rows.append({**common, "note": "尚无成功发布记录"})
            continue
        images = delivery_images.recorded(official["images"], official["image"], official["digest"])
        snapshot = app_info_repo.get_version(conn, official.get("app_info_version_id"))
        valid = snapshot is not None and snapshot["app_id"] == app["id"]
        entry = entries_repo.get_entry(conn, official["entry_id"]) if official.get("entry_id") else None
        commands = delivery_images.published_commands(snapshot["raw"], images, set(entry["excluded_tests"] if entry else [])) if valid else []
        used = {command["delivery_build_key"] for command in commands}
        inventory = list(commands)
        inventory.extend({**image, "delivery_reference": image.get("reference", ""),
                          "delivery_build_key": image["build_key"]}
                         for image in images if image["build_key"] not in used)
        for item in inventory or [{}]:
            notes = []
            if not valid:
                notes.append("正式版 app_info 快照缺失或归属不符，无法还原测试命令")
            if item.get("delivery_build_key") == "legacy":
                notes.append("历史镜像未指定构建，无法确定 OS/架构")
            if item.get("command") and not item.get("delivery_reference"):
                notes.append("缺少对应交付镜像，命令为模板")
            if not item.get("command"):
                notes.append("无对应启用测试命令")
            rows.append({**item, **common, "note": "；".join(notes)})
    return {"app_count": len(apps), "rows": rows}
