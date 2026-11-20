"""Look up which cycle, environment, test scope and test commands belong to a
delivered image digest or an App."""
from __future__ import annotations

import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.api.errors import ApiError, GerritNetworkError
from app.config import settings
from app.identity import prefetch_manifests, repo_storage_path
from app.domain import app_info, compatibility_table, cycles, delivery_images, image_lookup, publication_manifest, qa_results, test_commands
from app.domain.container_image import delivered_image_reference
from app.domain.test_platforms import is_enabled_value
from app.integrations import gerrit, harbor, manifest_archive
from app.integrations.read_session import read_session
from app.repositories import (
    app_info_repo,
    apps_repo,
    entries_repo,
    official_repo,
    releases_repo,
)
from app.services import catalog_service, compatibility_service, cycle_service, publication_service, qa_result_identity_service, qa_service


def _lookup_image(reference: str) -> dict[str, str]:
    image = image_lookup.parse_lookup_image(reference)
    if image["host"] != harbor.HARBOR_HOST:
        raise ValueError(f"仅支持 {harbor.HARBOR_HOST} 的镜像")
    return image


def _image_repository(conn: sqlite3.Connection, image: dict[str, str]) -> str:
    name = image["repository"].rsplit("/", 1)[-1]
    sources = _repository_index(conn, {name}).get(name, [])
    urls = {source["url"] for source in sources}
    if not urls:
        raise ValueError("未找到该镜像对应的 App 仓库，请先在 App 工作台登记并同步该 App")
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


def _publication_key(official: dict) -> str:
    return f'official:{official["id"]}' if official["id"] is not None else f'entry:{official["entry_id"]}'


def _publication_images(official: dict) -> list[dict]:
    return delivery_images.recorded(official["images"], official["image"], official["digest"])


def app_info_source(conn: sqlite3.Connection, reference: str, commit: str, *,
                    release_id: str = "", app_id: str = "", publication_key: str = "") -> dict[str, Any]:
    """Read the selected publication snapshot or a historical file at its pinned commit."""
    if release_id or app_id or publication_key:
        if not (release_id and app_id and publication_key):
            raise ValueError("发布来源需要完整的周期、App 和发布记录标识")
        image = image_lookup.parse_lookup_image(reference)
        official = official_repo.current_for_release(conn, release_id).get(app_id)
        if not official or _publication_key(official) != publication_key or not any(
            image_lookup.matches_delivery(image, delivery) for delivery in _publication_images(official)
        ):
            raise ApiError(409, "发布记录已变化或不包含该镜像，请重新查询")
        app = catalog_service.get_app_or_raise(conn, app_id)
        manifest, _ = publication_service.read_manifest(conn, official, app=app, app_name=catalog_service.app_name(app))
        if not manifest or manifest["app_info"]["commit_id"] != commit:
            raise ApiError(409, "发布 app_info 快照缺失或 commit 不符，请重新查询")
        return manifest["app_info"]["raw"]
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        raise ValueError("app_info 来源需要有效的 commit id")
    image = _lookup_image(reference)
    with read_session():
        return _app_info_at(_image_repository(conn, image), commit)


# Bound network fan-out while sharing identical Harbor/Gerrit work in one request.
LOOKUP_WORKERS = 16
MAX_BATCH_IMAGES = 500


def _repository_index(conn, names):
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    index = {}
    urls = {}
    candidates = []
    for version in app_info_repo.image_lookup_sources(conn):
        app = apps.get(version["app_id"])
        if not app:
            continue
        repositories = image_lookup.image_repositories(version["raw"]) & names
        if not repositories:
            continue
        candidates.append((version, app, repositories))
    prefetch_manifests({app["git_url"] for _, app, _ in candidates})
    for version, app, repositories in candidates:
        if app["id"] not in urls:
            urls[app["id"]] = gerrit.gerrit_remote_url(
                catalog_service.app_info_fetch_target(app)[0]
            )
        url = urls[app["id"]]
        source = {
            "app_id": app["id"],
            "url": url,
            "branch": app["git_branch"],
            "commit": version["commit_id"],
            "version": str(version["raw"].get("app_version") or ""),
            "app_name": catalog_service.app_name(app),
            "manifest_path": repo_storage_path("repo", app["git_url"]) if app["git_url"].endswith(".xml") else "",
        }
        for repository in repositories:
            index.setdefault(repository, []).append(source)
    return index


def _repo_artifact_source(tags, sources, url):
    builds = image_lookup.repo_build_tags(tags)
    paths = {
        f'hpc_manifest_{source["branch"]}/{directory}/release/'
        f'{build["version"]}_{build["build"]}_{filename}': build["tags"]
        for source in sources if source["manifest_path"]
        for directory, _, filename in [source["manifest_path"].rpartition("/")]
        for build in builds
    }
    if not paths:
        raise ValueError("该镜像没有有效 commit tag，也没有可定位固定 manifest 的 repo/版本/构建号 tags")
    resolved, missing = [], []
    for path, selected_tags in sorted(paths.items()):
        try:
            resolved.append({**manifest_archive.app_info_commit(path, url), "tags": selected_tags})
        except manifest_archive.ArchiveNotFound as exc:
            missing.append(str(exc))
    if not resolved:
        raise ValueError("；".join(missing))
    commits = {source["commit_id"] for source in resolved}
    if len(commits) != 1:
        raise ValueError("该镜像的固定 manifest 包含多个 app_info commit，无法唯一确定")
    return {
        "commit_id": commits.pop(),
        "commit_tags": sorted({tag for source in resolved for tag in source["tags"]}),
        "commit_source": "manifest_archive",
        "manifest_urls": sorted({source["manifest_url"] for source in resolved}),
    }


def _resolve_artifact(reference, index):
    image = _lookup_image(reference)
    artifact = harbor.get_artifact(image["project"], image["repository"], image["digest"])
    tags = sorted(
        {
            tag["name"]
            for tag in artifact.get("tags") or []
            if isinstance(tag, dict) and isinstance(tag.get("name"), str)
        }
    )
    sources = index.get(image["repository"].rsplit("/", 1)[-1], [])
    urls = {source["url"] for source in sources}
    if not urls:
        raise ValueError("未找到该镜像对应的 App 仓库，请先在 App 工作台登记并同步该 App")
    if len(urls) != 1:
        raise ValueError("多个 Gerrit 仓库声明了相同镜像名，无法唯一定位历史 app_info")
    url = urls.pop()
    try:
        commit, selected_tags = image_lookup.commit_tags(tags)
        provenance = {"commit_id": commit, "commit_tags": selected_tags,
                      "commit_source": "harbor_tag", "manifest_urls": []}
    except image_lookup.CommitTagMissing:
        if not any(source["manifest_path"] for source in sources):
            raise
        provenance = _repo_artifact_source(tags, sources, url)
    return {
        **image,
        "tags": tags,
        **provenance,
        "git_url": url,
        "sources": sources,
    }


def _source_identity(artifact, document):
    sources = artifact["sources"]
    commit = artifact["commit_id"]
    exact = [
        source
        for source in sources
        if source["commit"]
        and (source["commit"].startswith(commit) or commit.startswith(source["commit"]))
    ]
    version = [source for source in sources if source["version"] == str(document.get("app_version") or "")]
    selected_sources = exact or version or sources
    branches = {source["branch"] for source in selected_sources}
    branch = branches.pop() if len(branches) == 1 else ""
    names = {source["app_name"] for source in selected_sources
             if not branch or source["branch"] == branch}
    report_name = names.pop() if len(names) == 1 else ""
    app_ids = {source["app_id"] for source in selected_sources}
    return branch, report_name, app_ids.pop() if len(app_ids) == 1 else ""


def _branch_heads(url):
    return gerrit.branch_heads(url, project_root=settings.db_path.parent)


def _commands_for_artifact(artifact, document, rows=None, heads=None):
    # Tags determine all build dimensions. Harbor's architecture/OS metadata is
    # deliberately not used to select or validate a build.
    rows = test_commands.command_rows(document) if rows is None else rows
    result = image_lookup.historical_image_commands(
        document,
        image=artifact["image"],
        repository=artifact["repository"],
        tags=artifact["commit_tags"],
        architecture="",
        rows=rows,
        tag_version=artifact["commit_id"] if artifact["commit_source"] == "harbor_tag" else "repo",
    )
    branch, report_name, app_id = _source_identity(artifact, document)
    if not branch and heads is not None:
        if isinstance(heads, Exception):
            raise heads
        matching = [name for name, commit in heads.items() if commit.startswith(artifact["commit_id"])]
        if len(matching) == 1:
            branch = matching[0]
            selected = [source for source in artifact["sources"] if source["branch"] == branch]
            if selected:
                _, report_name, app_id = _source_identity({**artifact, "sources": selected}, document)
    versions = image_lookup.hpc_versions(
        document, artifact["repository"], artifact["tags"], selected_tags=artifact["commit_tags"]
    )
    return {
        **result,
        "source": "historical_config",
        "git_branch": branch,
        "app_id": app_id,
        "app_name": report_name,
        "image_app_name": result["app_name"],
        "hpc_version": ", ".join(versions),
        "hpc_versions": versions,
        **{
            key: artifact[key]
            for key in ("image", "digest", "tags", "commit_id", "commit_tags", "git_url", "commit_source", "manifest_urls")
        },
    }


def _attempt(function, *args):
    try:
        return function(*args)
    except (ValueError, RuntimeError, ApiError, TypeError, AttributeError, KeyError) as exc:
        return exc


def resolve_images(conn, references):
    """No cross-request cache: DB routes repositories, live sources generate commands.

    SQLite stays in the calling thread. Independent network requests run in a
    bounded pool; Gerrit is fetched only once for each repository/commit pair.
    """
    if not isinstance(references, list) or not references or len(references) > MAX_BATCH_IMAGES:
        raise ValueError(f"请提供 1 到 {MAX_BATCH_IMAGES} 个镜像")
    canonical, errors = [], []
    for reference in references:
        try:
            canonical.append(_lookup_image(reference)["image"])
        except (ValueError, TypeError) as exc:
            errors.append({"image": str(reference), "error": str(exc)})
    unique = list(dict.fromkeys(canonical))
    names = {_lookup_image(ref)["repository"].rsplit("/", 1)[-1] for ref in unique}
    with read_session() as batch:
        index = _repository_index(conn, names)
        with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
            artifacts = list(pool.map(lambda ref: batch.run(_attempt, _resolve_artifact, ref, index), unique))
            commits = list(
                dict.fromkeys((a["git_url"], a["commit_id"]) for a in artifacts if isinstance(a, dict))
            )
            documents = dict(
                zip(commits, pool.map(lambda pair: batch.run(_attempt, _app_info_at, *pair), commits), strict=True)
            )
            unresolved_urls = sorted({
                a["git_url"] for a in artifacts if isinstance(a, dict)
                if not isinstance(document := documents[(a["git_url"], a["commit_id"])], Exception)
                and not _source_identity(a, document)[0]
            })
            heads = dict(zip(
                unresolved_urls, pool.map(lambda url: batch.run(_attempt, _branch_heads, url), unresolved_urls), strict=True
            ))
    prepared = {
        key: _attempt(test_commands.command_rows, document)
        for key, document in documents.items()
        if not isinstance(document, Exception)
    }
    images = []
    for reference, artifact in zip(unique, artifacts, strict=True):
        if isinstance(artifact, Exception):
            errors.append({"image": reference, "error": str(artifact)})
            continue
        document = documents[(artifact["git_url"], artifact["commit_id"])]
        rows = prepared.get((artifact["git_url"], artifact["commit_id"]))
        result = (
            document
            if isinstance(document, Exception)
            else rows
            if isinstance(rows, Exception)
            else _attempt(_commands_for_artifact, artifact, document, rows, heads.get(artifact["git_url"]))
        )
        if isinstance(result, Exception):
            errors.append({"image": reference, "error": str(result)})
        else:
            images.append(result)
    return {"images": images, "errors": errors, "duplicate_count": len(canonical) - len(unique)}


def by_image(conn: sqlite3.Connection, reference: str) -> dict[str, Any]:
    image = _lookup_image(reference)
    name = image["repository"].rsplit("/", 1)[-1]
    with read_session():
        artifact = _resolve_artifact(reference, _repository_index(conn, {name}))
        document = _app_info_at(artifact["git_url"], artifact["commit_id"])
        heads = _branch_heads(artifact["git_url"]) if not _source_identity(artifact, document)[0] else None
    return _commands_for_artifact(artifact, document, heads=heads)


def batch_images(conn, references):
    result = resolve_images(conn, references)
    rows = []
    for image in result["images"]:
        if not image["app_id"] or not image["git_branch"]:
            result["errors"].append(
                {"image": image["image"], "error": "无法唯一确定 app_id / git_branch，不能匹配历史结果"}
            )
            continue
        rows.extend(qa_results.flat_commands(image))
    history = {}
    keys = {qa_results.result_key(row) for row in rows}
    for record in qa_result_identity_service.list_records(conn):
        data = record["data"]
        key = qa_results.result_key(data)
        if data["app_id"] and key in keys:
            history.setdefault(key, data)
    for row in rows:
        previous = history.get(qa_results.result_key(row), {})
        row.update({column: previous.get(qa_results.WORKSHEET_ALIASES.get(column, column), previous.get(column, ""))
                    for column in qa_results.RESULT_COLUMNS})
    sources = [
        {
            key: image[key]
            for key in (
                "image",
                "digest",
                "tags",
                "commit_id",
                "hpc_version",
                "hpc_versions",
                "git_url",
                "git_branch",
                "app_name",
                "app_id",
                "app_version",
                "commit_source",
                "manifest_urls",
            )
        }
        for image in result["images"]
    ]
    return {**result, "images": sources, "columns": qa_results.COLUMNS, "rows": rows}


def _entry_record(conn: sqlite3.Connection, entry: dict[str, Any]) -> dict[str, Any]:
    release = cycle_service.get_release(conn, entry["release_id"])
    app = apps_repo.get_app(conn, entry["app_id"]) or {"id": entry["app_id"]}
    official = official_repo.current_for_release(conn, release["id"]).get(entry["app_id"]) if release["released_locked"] else None
    manifest, source = publication_service.read_manifest(conn, official, app=app, app_name=catalog_service.app_name(app)) if official else (None, "unpublished")
    if manifest:
        entry = manifest["entry"]
        release = {**release, **manifest["release"]}
        info = manifest["app_info"]
        info = {**info, "app_version": info["raw"].get("app_version", "")}
        scope_tests = manifest["scope"]["tests"]
        commands = manifest["commands"]
        image = next((image for image in release["base_images"] if image["id"] == entry.get("base_image_id")), None)
        environment = release["environment"]
    elif official:
        # Missing legacy publication evidence must not borrow a later entry's configuration.
        info, scope_tests, commands, image = None, [], [], None
        environment = releases_repo.list_environment(conn, release["id"])
    else:
        view = cycle_service.entry_view(conn, entry)
        info = view["locked_app_info"]
        scope_tests = [test for test in view["scope_tests"] if not test["excluded"]]
        commands = qa_service.entry_test_commands(conn, entry)
        image = releases_repo.get_base_image(conn, entry["base_image_id"]) if entry.get("base_image_id") else None
        environment = releases_repo.list_environment(conn, release["id"])
    return {
        "app_id": entry["app_id"],
        "app_name": catalog_service.app_name(app),
        "release": {key: release[key] for key in ("id", "name", "phase", "release_date")},
        "environment": environment,
        "base_image": image,
        "entry": {
            **{key: entry[key] for key in ("id", "status", "target_version", "actual_delivery", "known_issues",
                "qa_status", "qa_note", "result", "deliverable_image", "deliverable_digest", "deliverable_commit")},
            "deliverable_images": manifest["images"] if manifest else delivery_images.recorded(entry.get('deliverable_images') or [],
                entry['deliverable_image'], entry['deliverable_digest'], entry['deliverable_commit']),
        },
        "app_info": {key: info[key] for key in ("id", "commit_id", "app_version", "x86_chips", "arm_chips")} if info else None,
        "scope_tests": scope_tests,
        "test_commands": commands,
        "manifest_source": source,
        "official_version_id": official["id"] if official else None,
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
    officials = official_repo.current_for_release(conn, release_id)
    rows = []
    for entry in entries_repo.list_for_release(conn, release_id):
        if entry["result"] != cycles.RESULT_RELEASED:
            continue
        app = apps.get(entry["app_id"]) or {"id": entry["app_id"]}
        official = officials.get(entry["app_id"])
        manifest, source = publication_service.read_manifest(conn, official, app=app, app_name=catalog_service.app_name(app)) if official else (None, "reconstructed")
        if manifest:
            entry = manifest["entry"]
        rows.append({
            "app_id": entry["app_id"],
            "app_name": catalog_service.app_name(app),
            "app_version": entry["target_version"],
            "image_reference": delivered_image_reference(entry["deliverable_image"], entry["deliverable_digest"]),
            "images": delivery_images.recorded(entry.get('deliverable_images') or [],
                entry['deliverable_image'], entry['deliverable_digest'], entry['deliverable_commit']),
            "deliverable_image": entry["deliverable_image"],
            "deliverable_digest": entry["deliverable_digest"],
            "manifest_source": source,
            "official_version_id": official["id"] if official else None,
            "qa_status": entry["qa_status"],
            "qa_note": entry["qa_note"],
        })
    rows.sort(key=lambda row: (row["app_name"].casefold(), row["app_id"]))
    return {
        "release": {key: release[key] for key in ("id", "name", "release_date")},
        "apps": rows,
    }


def export_latest_running_csv(conn: sqlite3.Connection, **filters) -> str:
    return compatibility_table.export_csv(latest_running_releases(conn)["rows"], **filters)


def latest_running_releases(conn: sqlite3.Connection) -> dict[str, Any]:
    """Latest valid successful publication for running Apps."""
    officials = official_repo.current_by_app(conn)
    latest_ids = app_info_repo.latest_version_ids(conn)
    rows = []
    apps = sorted((app for app in apps_repo.list_apps(conn)
                   if app["cicd_status"] == cycles.CICD_RUNNING and app["id"] in officials),
                  key=lambda app: (catalog_service.app_name(app).casefold(), app["id"]))
    for app in apps:
        official = officials[app["id"]]
        latest = app_info_repo.get_version(conn, latest_ids.get(app["id"]))
        manifest, source = publication_service.read_manifest(conn, official, app=app, app_name=catalog_service.app_name(app))
        if manifest:
            manifest = publication_manifest.compatibility_view(manifest, latest["raw"] if latest else {})
        common = {"app_id": app["id"], "app_name": catalog_service.app_name(app),
                  "git_branch": manifest["app"]["git_branch"] if manifest else app["git_branch"],
                  "release_id": official["release_id"] if official else "",
                  "owners": app.get("owners") or [], "dependencies": manifest["dependencies"] if manifest else [],
                  "scope_pending": manifest["scope_pending"] if manifest else True,
                  "hpc_version": manifest["release"]["name"] if manifest else official["release_name"] if official else "",
                  "app_version": manifest["entry"]["target_version"] if manifest else official["version"] if official else "",
                  "app_info_commit_id": "",
                  "publication_app_info_commit_id": manifest["app_info"]["commit_id"] if manifest else "",
                  "app_info_version_id": manifest["app_info"]["id"] if manifest else official.get("app_info_version_id") if official else None,
                  "official_version_id": official["id"] if official else None,
                  "manifest_source": source,
                  "qa_status": manifest["qa"]["status"] if manifest else "",
                  "qa_note": manifest["qa"]["note"] if manifest else "",
                  "_excluded_tests": manifest["scope"]["excluded_tests"] if manifest else [],
                  "_disabled_builds": [alias for alias, cfg in ((latest["raw"].get("app_build") or {}).items() if latest else [])
                                       if isinstance(cfg, dict) and not is_enabled_value(cfg.get("enabled"))]}
        images = manifest["images"] if manifest else delivery_images.recorded(official["images"], official["image"], official["digest"])
        commands = []
        if manifest:
            # Earlier manifests stored the test configuration but omitted its performance fields.
            test_configs = dict(test_commands.release_tests(manifest["app_info"]["raw"]))
            commands = [
                {**test_commands.performance_fields(test_configs.get(command.get("path"), {})), **command}
                for command in manifest["commands"]
            ]
        builds = {build["build_key"]: build for build in manifest["builds"]} if manifest else {}
        used = {command["delivery_build_key"] for command in commands}
        inventory = list(commands)
        inventory.extend({**image, "delivery_reference": image.get("reference") or delivered_image_reference(image.get("image", ""), image.get("digest", "")),
                          "delivery_build_key": image["build_key"]}
                         for image in images if image["build_key"] not in used)
        for item in inventory or [{}]:
            dependencies = list(dict.fromkeys([*(manifest["declared_dependencies"] or []),
                *app_info.python_pytorch_dependencies({}, platforms=[item])])) if manifest and item else common["dependencies"]
            notes = []
            if source == "reconstructed":
                notes.append("根据历史配置生成（未在发布时封存）")
            if not manifest:
                notes.append("正式版 app_info 快照缺失或归属不符，无法还原测试命令")
            if item.get("delivery_build_key") == "legacy":
                notes.append("历史镜像未指定构建，无法确定 OS/架构")
            if item.get("command") and not item.get("delivery_reference"):
                notes.append("缺少对应交付镜像，命令为模板")
            if not item.get("command"):
                notes.append("无对应启用测试命令")
            rows.append({**item, **common,
                         "dependencies": dependencies,
                         "image_pattern": builds.get(item.get("delivery_build_key"), {}).get("image_pattern", item.get("image_pattern", "")),
                         "note": "；".join(notes)})
    return {"app_count": len(apps), "rows": compatibility_service.expand_rows(conn, rows, resolve_images, batch_size=MAX_BATCH_IMAGES)}
