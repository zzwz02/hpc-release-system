"""App catalog: identity, CICD status, optional profile, app_info, dependencies.

* The App row owns the Owner profile (optional, never a release prerequisite)
  and the CICD running status.
* app_info.json comes only from the App's repository. RM can trigger a bulk
  sync before the selected cycle's signup deadline, or sync individual Apps.
  Every new version is handed to
  ``cycle_service`` so signup entries follow it or ask QA to approve a wider
  scope.
* Official versions record what each App last released; stopped Apps keep
  their history but are left out of cumulative manuals and compatibility
  lists.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.api.errors import ApiError, AuthzError
from app.config import settings
from app.db.connection import connect, transaction
from app.domain import app_info as app_info_domain
from app.domain.app_profile import (
    APP_META_LABELS,
    display_name,
    normalize_app_description,
    normalize_doc_target,
)
from app.domain.audit_diff import field_diff
from app.domain.cycles import CICD_RUNNING, CICD_STOPPED
from app.domain.permissions import has_capability
from app.repositories import app_info_repo, apps_repo, entries_repo, official_repo, releases_repo
from app.repositories.audit_repo import log_audit
from app.domain import delivery_images, publication_manifest
from app.domain import phases
from app.timeutil import beijing_timestamp
from app.services import publication_service
from app.integrations.read_session import read_session
from app.identity import prefetch_manifests

logger = logging.getLogger(__name__)

PROFILE_TEXT_FIELDS = ("official_name", "type", "official_url", "description", "doc_target")
PROFILE_JSON_FIELDS = ("doc", "community", "sanity", "test_docs")
# Network reads run in parallel; the caller alone owns SQLite and applies each
# result. Bound each submitted wave so the signup deadline is rechecked at start.
APP_INFO_SYNC_WORKERS = 16


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------


def parse_version(version: dict[str, Any] | None) -> dict[str, Any] | None:
    """Parsed app_info of a stored version (None when there is no version)."""
    if not version:
        return None
    return app_info_domain.parse_app_info(version["raw"])


def version_summary(version: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact, UI-facing description of one app_info version."""
    if not version:
        return None
    parsed = parse_version(version) or {}
    return {
        "id": version["id"],
        "commit_id": version.get("commit_id", ""),
        "fetched_at": version.get("fetched_at", ""),
        "source": version.get("source", ""),
        "app_version": parsed.get("app_version", ""),
        "x86_chips": parsed.get("x86_chips", []),
        "arm_chips": parsed.get("arm_chips", []),
        "build_os": parsed.get("build_os", []),
        "build_arches": parsed.get("build_arches", []),
        "python_labels": parsed.get("python_labels", []),
        "pytorch_labels": parsed.get("pytorch_labels", []),
        "tests": [
            {
                "path": test["path"],
                "command": test["command"],
                "supported_chips": test.get("supported_chips", []),
                "arch_list": test.get("arch_list", []),
                "platforms": test.get("platforms", []),
            }
            for test in parsed.get("tests", [])
        ],
        "community": parsed.get("community_artifacts", {}),
        "dependencies": app_info_domain.declared_dependencies(version["raw"]),
    }


def app_name(app: dict[str, Any]) -> str:
    aliases = app.get("aliases") or []
    return app.get("official_name") or (aliases[0] if aliases else app["id"])


def list_app_views(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All Apps with profile, CICD status, latest app_info and official version."""
    officials = official_repo.current_by_app(conn)
    deps = app_info_repo.dependencies_by_app(conn)
    latest_ids = app_info_repo.latest_version_ids(conn)
    views = []
    for app in apps_repo.list_apps(conn):
        latest = app_info_repo.get_version(conn, latest_ids.get(app["id"]))
        summary = version_summary(latest)
        official = officials.get(app["id"])
        view = dict(app)
        view["name"] = display_name(app_name(app), (summary or {}).get("app_version", ""))
        view["latest_app_info"] = summary
        view["dependencies"] = deps.get(app["id"], [])
        view["dependency_declared"] = bool(summary and summary["dependencies"] is not None)
        view["official"] = (
            {
                "release_id": official["release_id"],
                "release_name": official["release_name"],
                "version": official["version"],
                "image": official["image"],
                "digest": official["digest"],
                "images": delivery_images.recorded(official['images'], official['image'], official['digest']),
                "released_at": official["released_at"],
            }
            if official
            else None
        )
        views.append(view)
    return views


def get_app_or_raise(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    app = apps_repo.get_app(conn, app_id)
    if not app:
        raise KeyError(f"Unknown app: {app_id}")
    return app


def is_owner(app: dict[str, Any], username: str) -> bool:
    return username in (app.get("owners") or [])


def require_app_editor(app: dict[str, Any], username: str, role: str) -> None:
    """RM may edit any App; Owner only Apps they own."""
    if has_capability(role, "app.edit.any"):
        return
    if has_capability(role, "app.edit.owned") and is_owner(app, username):
        return
    raise AuthzError("只有该 App 的 Owner 或 RM 可以修改")


def app_detail(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    """Version history and official-version history of one App."""
    get_app_or_raise(conn, app_id)
    history = official_repo.history_for_app(conn, app_id)
    for record in history:
        record['images'] = delivery_images.recorded(record['images'], record['image'], record['digest'])
    return {
        "app_info_versions": app_info_repo.list_versions(conn, app_id),
        "official_versions": history,
    }


def get_version_summary(conn: sqlite3.Connection, version_id: int) -> dict[str, Any]:
    version = app_info_repo.get_version(conn, version_id)
    if not version:
        raise KeyError(f"Unknown app_info version: {version_id}")
    summary = version_summary(version) or {}
    summary["app_id"] = version["app_id"]
    summary["raw"] = version["raw"]
    return summary


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


def update_profile(
    conn: sqlite3.Connection,
    app_id: str,
    fields: dict[str, Any],
    *,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Save optional profile fields.  Owners list is RM-only."""
    app = get_app_or_raise(conn, app_id)
    require_app_editor(app, user, role)
    allowed = set(PROFILE_TEXT_FIELDS) | set(PROFILE_JSON_FIELDS) | {"owners"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"不支持的字段：{', '.join(sorted(unknown))}")
    rm_only = {"owners", "official_name", "sanity"} & set(fields)
    if rm_only and not has_capability(role, "app.edit.rm_fields"):
        raise AuthzError("只有 RM 可以修改 Owner、官方名称和 Sanity")
    update: dict[str, Any] = {}
    for key in PROFILE_TEXT_FIELDS:
        if key in fields:
            value = str(fields[key] or "").strip()
            if key == "description":
                value = normalize_app_description(value)
            if key == "doc_target":
                value = normalize_doc_target(value)
            update[key] = value
    for key in PROFILE_JSON_FIELDS:
        if key in fields:
            value = fields[key]
            expected = list if key == "test_docs" else dict
            if not isinstance(value, expected):
                raise ValueError(f"{key} 格式错误")
            update[key] = value
    if "owners" in fields:
        owners = fields["owners"]
        if isinstance(owners, str):
            owners = [item.strip() for item in owners.replace("，", ",").split(",")]
        update["owners"] = sorted({str(item).strip() for item in owners or [] if str(item).strip()})
    if update.get("official_name", "x") == "":
        raise ValueError("官方名称不能为空")
    before = {key: app.get(key) for key in update}
    ts = beijing_timestamp()
    with transaction(conn):
        apps_repo.update_profile(conn, app_id, update)
        changes = field_diff(before, update, {**APP_META_LABELS, "doc": "文档", "community": "社区信息",
                                              "sanity": "Sanity", "test_docs": "测试说明"})
        if changes:
            log_audit(
                conn,
                "修改 App 资料",
                ts=ts,
                user=user,
                role=role,
                app_id=app_id,
                event="update_app_profile",
                detail=changes,
            )
    return get_app_or_raise(conn, app_id)


# ---------------------------------------------------------------------------
# App creation (called when a CICD create request takes effect)
# ---------------------------------------------------------------------------


def create_app(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    official_name: str,
    git_url: str,
    git_branch: str,
    owners: list[str],
    doc_target: str,
    app_type: str,
    created_by: str,
    ts: str,
) -> None:
    apps_repo.insert_app(
        conn,
        {
            "id": app_id,
            "git_url": git_url,
            "git_branch": git_branch,
            "aliases": [],
            "created_by": created_by,
            "created_at": ts,
            "cicd_status": CICD_RUNNING,
            "official_name": official_name,
            "owners": owners,
            "doc_target": normalize_doc_target(doc_target),
            "type": app_type,
        },
    )


def set_cicd_status(
    conn: sqlite3.Connection,
    app_id: str,
    status: str,
    *,
    user: str,
    role: str,
    ts: str,
    request_id: int,
) -> None:
    if status not in (CICD_RUNNING, CICD_STOPPED):
        raise ValueError(f"无效的 CICD 状态：{status}")
    app = get_app_or_raise(conn, app_id)
    if app["cicd_status"] == status:
        return
    apps_repo.set_cicd_status(conn, app_id, status)
    log_audit(
        conn,
        f"CICD 状态 {app['cicd_status']} -> {status}（申请 #{request_id} 生效）",
        ts=ts,
        user=user,
        role=role,
        app_id=app_id,
        event="cicd_status",
    )


# ---------------------------------------------------------------------------
# app_info storage and sync
# ---------------------------------------------------------------------------


def app_info_fetch_target(app: dict[str, Any]) -> tuple[str, str, str]:
    """Return (git_url, branch, source label) holding the App's app_info.json.

    Manifest (repo-type) Apps are resolved to their underlying project.
    """
    original_url = str(app.get("git_url") or "").strip()
    original_branch = str(app.get("git_branch") or "").strip()
    if not original_url or not original_branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    source = f"{original_url} {original_branch}:app_info.json"
    repo_type = str(app.get("cicd_repo_type") or "").strip()
    if repo_type == "repo" or original_url.endswith(".xml"):
        if not original_url.endswith(".xml"):
            raise RuntimeError(
                f"repo 类型缺少 manifest XML 路径，不能拉取 app_info.json：{original_url} @ {original_branch}"
            )
        from app.identity import repo_to_git_identity

        url, branch = repo_to_git_identity("repo", original_url, original_branch, refresh_manifest=True)
        if not url or not branch:
            raise RuntimeError(f"无法解析 repo manifest，不能拉取 app_info.json：{original_url} @ {original_branch}")
        return url, branch, f"{source} -> {url} {branch}:app_info.json"
    return original_url, original_branch, source


def store_app_info(
    conn: sqlite3.Connection,
    app_id: str,
    raw: str | dict[str, Any],
    *,
    commit_id: str,
    source: str,
    user: str,
    role: str = "system",
) -> int | None:
    """Store app_info content if it differs from the App's latest version.

    Returns the new version id, or None when the content is unchanged.  Must
    be called inside a transaction; it also lets signup entries react.
    """
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError("app_info.json 必须是 JSON 对象")
    app_info_domain.parse_app_info(data)  # validates shape before storing
    digest = app_info_domain.content_hash(data)
    latest = app_info_repo.latest_version(conn, app_id)
    if latest and latest["content_hash"] == digest:
        return None
    ts = beijing_timestamp()
    version_id = app_info_repo.insert_version(
        conn,
        app_id=app_id,
        commit_id=commit_id,
        content_hash=digest,
        raw_json=app_info_domain.canonical_json(data),
        source=source,
        fetched_at=ts,
        fetched_by=user,
    )
    app_info_repo.replace_dependencies(conn, app_id, app_info_domain.declared_dependencies(data) or [])
    log_audit(
        conn,
        f"app_info 更新（commit {commit_id[:12] or '-'}）",
        ts=ts,
        user=user,
        role=role,
        app_id=app_id,
        event="app_info_version",
        detail={"version_id": version_id, "source": source},
    )
    from app.services import cycle_service

    cycle_service.on_app_info_version(conn, app_id, version_id)
    return version_id


def _read_sync_app(app, *, force=False):
    """Fetch a consistent branch-head/file pair without touching SQLite."""
    from app.integrations import gerrit

    result = {"app": app, "checked_at": beijing_timestamp()}
    try:
        with read_session() as batch:
            url, branch, source = batch.memo(
                ("sync-target", app["git_url"], app["git_branch"], app.get("cicd_repo_type")),
                lambda: app_info_fetch_target(app),
            )
            remote = gerrit.gerrit_remote_url(url)
            head = batch.memo(("sync-head", remote, branch),
                              lambda: gerrit.head_commit(url, branch, project_root=settings.db_path.parent))
            raw = None
            if head != app.get("sync_head_commit") or force:
                raw = batch.memo(("sync-file", remote, head),
                                 lambda: gerrit.fetch_app_info_at(url, head, project_root=settings.db_path.parent))
                try:
                    json.loads(raw)
                except ValueError as exc:
                    raise RuntimeError(f"app_info.json 不是合法 JSON：{exc}") from exc
            result.update(head=head, raw=raw, source=source)
    except Exception as exc:
        result["error"] = str(exc) or type(exc).__name__
    return result


def _check_sync_source(current, initial, head):
    if any(current.get(key) != initial.get(key) for key in ("git_url", "git_branch", "cicd_repo_type")):
        raise RuntimeError("App 的仓库或分支已变化，请重新同步 app_info")
    if initial["cicd_status"] == CICD_RUNNING and current["cicd_status"] != CICD_RUNNING:
        raise RuntimeError("App 已停止运行，未保存同步结果")
    if current.get("sync_head_commit") not in (initial.get("sync_head_commit"), head):
        raise RuntimeError("另一次同步已更新 app_info，请重新检查，未覆盖新结果")


def sync_app(
    conn: sqlite3.Connection,
    app_id: str,
    *,
    user: str,
    force: bool = False,
    _prepared: dict | None = None,
    _release_id: str | None = None,
) -> dict[str, Any]:
    """Check one App's branch head and store app_info when it changed.

    Network I/O runs outside any transaction. Source errors are recorded on
    the App and returned; an expired bulk-sync window aborts the batch.
    """
    app = _prepared["app"] if _prepared is not None else get_app_or_raise(conn, app_id)
    prepared = _read_sync_app(app, force=force) if _prepared is None else _prepared
    ts = prepared["checked_at"]
    try:
        if "error" in prepared:
            raise RuntimeError(prepared["error"])
        head, raw, source = prepared["head"], prepared["raw"], prepared["source"]
        with transaction(conn, immediate=True):
            if _release_id is not None:
                require_bulk_sync_release(conn, _release_id)
            _check_sync_source(get_app_or_raise(conn, app_id), prepared["app"], head)
            if raw is None:
                apps_repo.update_sync_state(conn, app_id, checked_at=ts, error="")
                version_id = None
            else:
                version_id = store_app_info(conn, app_id, raw, commit_id=head, source=source, user=user)
                apps_repo.update_sync_state(conn, app_id, checked_at=ts, error="", head_commit=head)
        if raw is None:
            return {"app_id": app_id, "status": "unchanged", "commit_id": head}
        return {
            "app_id": app_id,
            "status": "updated" if version_id else "unchanged",
            "commit_id": head,
            "version_id": version_id,
        }
    except Exception as exc:  # noqa: BLE001 - every failure is reported per App
        if _release_id is not None and isinstance(exc, ApiError):
            raise  # Stop a bulk job without writing results after signup closes.
        message = str(exc) or exc.__class__.__name__
        with transaction(conn, immediate=True):
            if _release_id is not None:
                require_bulk_sync_release(conn, _release_id)
            current = apps_repo.get_app(conn, app_id)
            if current is not None:
                try:
                    _check_sync_source(current, prepared["app"], prepared.get("head", app.get("sync_head_commit")))
                except RuntimeError:
                    # A stale read must not overwrite a newer sync's success
                    # state, or attach an old-repository error to a new one.
                    pass
                else:
                    apps_repo.update_sync_state(conn, app_id, checked_at=ts, error=message)
        return {"app_id": app_id, "status": "error", "error": message}


def require_bulk_sync_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    if not release_id:
        raise ApiError(400, "请先选择发布周期，再全量同步 app_info")
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise ApiError(404, "发布周期不存在，请刷新后重试")
    if not phases.can(release, "sync_all_app_info"):
        raise ApiError(409, f"{release['name']} 已过报名截止时间或已发布，不允许全量同步 app_info")
    return release


def sync_all(conn: sqlite3.Connection, *, release_id: str, user: str, force: bool = False) -> dict[str, Any]:
    """Sync running Apps only while the selected cycle is accepting signups."""
    require_bulk_sync_release(conn, release_id)
    apps = [app for app in apps_repo.list_apps(conn) if app["cicd_status"] == CICD_RUNNING]
    results = []
    stopped = ""
    with read_session() as batch, ThreadPoolExecutor(max_workers=APP_INFO_SYNC_WORKERS) as pool:
        prefetch_manifests({app["git_url"] for app in apps})
        for start in range(0, len(apps), APP_INFO_SYNC_WORKERS):
            try:
                pending = []
                for previous in apps[start:start + APP_INFO_SYNC_WORKERS]:
                    require_bulk_sync_release(conn, release_id)
                    app = get_app_or_raise(conn, previous["id"])
                    if app["cicd_status"] != CICD_RUNNING:
                        continue
                    pending.append((app["id"], pool.submit(batch.run, _read_sync_app, app, force=force)))
                for app_id, future in pending:
                    results.append(sync_app(conn, app_id, user=user, force=force,
                                            _prepared=future.result(), _release_id=release_id))
            except ApiError as exc:
                stopped = str(exc)
                break
    failed = [result for result in results if result["status"] == "error"]
    if failed:
        from app.services import notification_channels

        names = {app["id"]: app_name(app) for app in apps_repo.list_apps(conn)}
        lines = [f"- {names.get(r['app_id'], r['app_id'])}：{r['error'][:120]}" for r in failed[:30]]
        more = f"\n…另有 {len(failed) - 30} 个" if len(failed) > 30 else ""
        notification_channels.notify(
            conn,
            "app_info_sync_failed",
            f"app_info 同步失败 {len(failed)} 个 App：\n" + "\n".join(lines) + more,
        )
    return {
        "checked": len(results),
        "updated": sum(1 for r in results if r["status"] == "updated"),
        "failed": len(failed),
        "results": results,
        **({"error": stopped} if stopped else {}),
    }


def sync_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Per-App sync state for the RM status page (problems first)."""
    rows = []
    for app in apps_repo.list_apps(conn):
        rows.append(
            {
                "app_id": app["id"],
                "name": app_name(app),
                "cicd_status": app["cicd_status"],
                "git_url": app["git_url"],
                "git_branch": app["git_branch"],
                "head_commit": app["sync_head_commit"],
                "checked_at": app["sync_checked_at"],
                "error": app["sync_error"],
            }
        )
    rows.sort(key=lambda row: (not row["error"], row["cicd_status"] != CICD_RUNNING, row["name"].lower()))
    return rows


# ---------------------------------------------------------------------------
# Component dependencies and compatibility list
# ---------------------------------------------------------------------------


def dependency_overview(conn: sqlite3.Connection, source: str = "official") -> dict[str, Any]:
    """Group dependencies from one explicit snapshot source; never mix development and release."""
    if source not in {"official", "development"}:
        raise ApiError(400, "未知依赖数据来源")
    officials = official_repo.current_by_app(conn)
    latest_ids = app_info_repo.latest_version_ids(conn) if source == "development" else {}
    signed_up = entries_repo.ever_signed_up_app_ids(conn)
    snapshot_errors: list[str] = []
    all_rows: list[dict[str, Any]] = []
    components: dict[str, list[dict[str, Any]]] = {}
    undeclared: list[dict[str, Any]] = []
    for app in apps_repo.list_apps(conn):
        if app["cicd_status"] != CICD_RUNNING or app["id"] not in signed_up:
            continue
        official = officials.get(app["id"]) if source == "official" else None
        if source == "official" and not official:
            continue
        manifest, manifest_source = publication_service.read_manifest(conn, official, app=app, app_name=app_name(app)) if official else (None, "development")
        snapshot = manifest["app_info"] if manifest else app_info_repo.get_version(conn, latest_ids.get(app["id"])) if source == "development" else None
        if source == "development" and snapshot and snapshot["app_id"] != app["id"]:
            snapshot = None
        if official and not snapshot:
            snapshot_errors.append(f"{app_name(app)}：正式版缺少有效 app_info 快照，无法确定兼容依赖")
        images = manifest["images"] if manifest else delivery_images.recorded(official["images"], official["image"], official["digest"]) if official else []
        declared = manifest["declared_dependencies"] if manifest else app_info_domain.declared_dependencies(snapshot["raw"]) if snapshot else None
        combined_dependencies = manifest["dependencies"] if manifest else list(dict.fromkeys([
            *(declared or []),
            *(app_info_domain.python_pytorch_dependencies(snapshot["raw"]) if snapshot else []),
        ]))
        row = {
            "app_id": app["id"],
            "name": app_name(app),
            "owners": app.get("owners") or [],
            "dependencies": combined_dependencies,
            "app_info_version_id": snapshot["id"] if snapshot else None,
            "commit_id": snapshot["commit_id"] if snapshot else "",
            "app_version": snapshot["raw"].get("app_version", "") if snapshot else "",
            "official": (
                {
                    "release_name": manifest["release"]["name"] if manifest else official["release_name"],
                    "app_info_version_id": snapshot["id"] if snapshot else official["app_info_version_id"],
                    "version": official["version"],
                    "image": official["image"],
                    "digest": official["digest"],
                    "images": images,
                }
                if official
                else None
            ),
            "tests": manifest["commands"] if manifest else [],
            "manifest_source": manifest_source,
        }
        all_rows.append(row)
        if declared is None or not combined_dependencies:
            undeclared.append(row)
        for component in combined_dependencies:
            components.setdefault(component, []).append(row)
    return {
        "source": source,
        "snapshot_errors": snapshot_errors,
        "apps": sorted(all_rows, key=lambda r: r["name"].lower()),
        "components": [
            {"component": name, "apps": sorted(rows, key=lambda r: r["name"].lower())}
            for name, rows in sorted(components.items())
        ],
        "undeclared": sorted(undeclared, key=lambda r: r["name"].lower()),
    }


def compat_list(conn: sqlite3.Connection, component: str = "") -> list[dict[str, Any]]:
    """Running Apps with a current official version, optionally by component."""
    overview = dependency_overview(conn)
    if overview["snapshot_errors"]:
        raise ApiError(409, "；".join(overview["snapshot_errors"]))
    if component:
        group = next((g for g in overview["components"] if g["component"] == component), None)
        rows = group["apps"] if group else []
    else:
        rows = overview["apps"]
    return [row for row in rows if row["official"]]


def compat_list_csv(conn: sqlite3.Connection, component: str = "") -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["App", "依赖组件", "Owner", "最新hpc release版本", "OS", "CPU 架构", "镜像类型",
                     "Python label", "PyTorch label", "支持芯片", "镜像", "Digest", "镜像来源", "测试项", "测试命令", "清单来源"])
    for row in compat_list(conn, component):
        official = row["official"]
        tests = list(row["tests"])
        used = {test["delivery_build_key"] for test in tests}
        for image in official["images"]:
            if image["build_key"] not in used:
                tests.append({**image, "image": image.get("reference") or image.get("image", ""),
                              "delivery_reference": image.get("reference", ""),
                              "delivery_build_key": image["build_key"]})
        tests = tests or [{}]
        for test in tests:
            source = ("交付镜像（未指定构建）" if test.get("delivery_build_key") == "legacy"
                      else "交付镜像" if test.get("delivery_reference")
                      else "镜像模板（未填写交付镜像）" if test.get("command") else "")
            writer.writerow(
                [
                    row["name"],
                    ",".join(row["dependencies"]),
                    ",".join(row["owners"]),
                    official["release_name"],
                    test.get("os", ""),
                    {"amd64": "x86", "arm64": "ARM"}.get(test.get("arch"), test.get("arch", "")),
                    test.get("image_target", ""),
                    test.get("python_label", ""),
                    test.get("pytorch_label", ""),
                    ",".join(test.get("chips") or []),
                    test.get("image", ""),
                    test.get("digest", ""),
                    source,
                    test.get("path", ""),
                    test.get("command", ""),
                    publication_manifest.source_label(row["manifest_source"]),
                ]
            )
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# "Sync all" started from the UI (runs in a background thread)
# ---------------------------------------------------------------------------

_sync_all_lock = threading.Lock()
_sync_all_state: dict[str, Any] = {"running": False, "release_id": "", "release_name": "", "started_at": "", "started_by": "",
                                   "finished_at": "", "summary": None}


def sync_all_state() -> dict[str, Any]:
    with _sync_all_lock:
        return dict(_sync_all_state)


def start_sync_all(conn: sqlite3.Connection, *, release_id: str, user: str) -> dict[str, Any]:
    release = require_bulk_sync_release(conn, release_id)
    with _sync_all_lock:
        if _sync_all_state["running"]:
            if _sync_all_state["release_id"] != release_id:
                raise ApiError(409, "另一个发布周期的全量同步正在进行，请稍后重试")
            return dict(_sync_all_state)
        _sync_all_state.update(running=True, release_id=release_id, release_name=release["name"],
                               started_at=beijing_timestamp(), started_by=user,
                               finished_at="", summary=None)

    def _run() -> None:
        conn = connect(settings.db_path)
        summary: dict[str, Any] | None = None
        try:
            summary = sync_all(conn, release_id=release_id, user=user)
            summary.pop("results", None)
        except Exception as exc:  # noqa: BLE001 - reported on the status page
            logger.exception("app_info 全部同步失败")
            summary = {"error": str(exc)}
        finally:
            conn.close()
            with _sync_all_lock:
                _sync_all_state.update(running=False, finished_at=beijing_timestamp(), summary=summary)

    threading.Thread(target=_run, name="app-info-sync-all", daemon=True).start()
    return sync_all_state()
