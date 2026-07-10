"""App workbench service — app CRUD, snapshot update, and app_info fetch.

Full port of the matching server.py handlers + core.py functions onto the app
repositories and domain modules.  All transaction boundaries, audit messages,
and error text match the old server.  Ruling D (decision↔CICD status sync) is
wired into update_snapshot via cicd_service.sync_decision_to_cicd (Phase 4
Wave 2).

Per the brief (DA finding P1): snapshots stay loose dicts — no strict
Pydantic field validation.  All timestamps are naive Beijing strings (§5.4).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.api.errors import AuthzError
from app.db.connection import transaction
from app.domain import app_info as app_info_domain
from app.domain import decision_sync as decision_sync_domain
from app.domain import gates
from app.domain import phases as phase_policy
from app.domain.audit_diff import field_diff, fmt_audit_value, test_docs_diff
from app.domain.decisions import RELEASE_DECISIONS, normalize_release_decision
from app.domain.snapshots import (
    APP_META_LABELS,
    SNAPSHOT_META_FIELDS,
    base_snapshot,
    normalize_app_description,
    normalize_doc_target,
    variant_app_id,
)
from app.domain.textutil import normalize_name
from app.repositories import apps_repo, qa_repo, releases_repo, schedule_repo, snapshots_repo
from app.repositories.audit_repo import (
    app_audit_log as repo_app_audit_log,
    log_audit,
    qa_audit_logs_by_app,
)
from app.repositories.base import dumps_json, new_id
from app.services import release_reads
from app.services.authz import (
    require_app_audit_access as authz_require_app_audit_access,
)
from app.services.authz import (
    require_owner_or_rm_with_owners,
)
from app.timeutil import beijing_timestamp

# ---------------------------------------------------------------------------
# State / list helpers (for GET /api/state)
# ---------------------------------------------------------------------------

CICD_APP_CONFIG_FIELDS = {
    "cicd_repo_type",
    "cicd_community_artifact",
    "cicd_build_image",
    "cicd_test_timeout",
    "cicd_notes",
}
APP_REPO_IDENTITY_FIELDS = {"git_url", "git_branch"}

CICD_APP_CONFIG_LABELS = {
    "cicd_repo_type": "仓库类型",
    "cicd_community_artifact": "开发者社区产物",
    "cicd_build_image": "构建依赖镜像",
    "cicd_test_timeout": "超时",
    "cicd_notes": "备注",
}

_AFTER_DOC_EDIT_MESSAGE = (
    "已过 doc deadline，只能修改 release 决策和 CICD 配置，不能再修改文档/表单/app_info"
)


def _require_phase_action(release: dict, action: str, message: str) -> None:
    if not phase_policy.can(release, action):
        raise RuntimeError(message)


def _require_app_update_phase_permissions(
    release: dict,
    *,
    snap_update_keys: set[str],
    app_cicd_keys: set[str],
    app_repo_identity_keys: set[str],
    app_other_keys: set[str],
) -> None:
    if "release_decision" in snap_update_keys:
        _require_phase_action(release, "edit_release_decision", _AFTER_DOC_EDIT_MESSAGE)
    if "owner_confirmed" in snap_update_keys:
        _require_phase_action(release, "edit_owner_confirmation", _AFTER_DOC_EDIT_MESSAGE)
    if snap_update_keys - {"release_decision", "owner_confirmed"}:
        _require_phase_action(release, "edit_release_doc_fields", _AFTER_DOC_EDIT_MESSAGE)
    if app_cicd_keys:
        _require_phase_action(release, "edit_cicd_config", _AFTER_DOC_EDIT_MESSAGE)
    if app_repo_identity_keys:
        _require_phase_action(release, "edit_gerrit_identity", _AFTER_DOC_EDIT_MESSAGE)
    if app_other_keys:
        _require_phase_action(release, "edit_release_doc_fields", _AFTER_DOC_EDIT_MESSAGE)


def _community_required(app: dict) -> bool:
    return bool((app.get("cicd_community_artifact") or "").strip())


def _missing_items_for(app: dict, snapshot: dict) -> list[dict[str, str]]:
    items = list(gates.missing_items_for(app, snapshot))
    if normalize_release_decision(snapshot.get("release_decision")) != "release":
        return items
    if not _community_required(app):
        return items
    community = snapshot.get("community") or {}
    required = {
        "release_status": "社区发布情况",
        "python_version": "社区包 Python 版本",
        "framework_version": "社区包框架及版本",
    }
    for key, label in required.items():
        if not (community.get(key) or "").strip():
            items.append({"kind": "doc", "text": f"缺少{label}"})
    return items


def _serialize_release(release: dict) -> dict:
    """Mirror server.py:_serialize_release — add phase, coerce released_locked."""
    out = dict(release)
    out["released_locked"] = bool(out.get("released_locked"))
    out["phase"] = phase_policy.current_phase(out)
    return out


def _get_app_or_raise(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    """Mirror core.py:get_app — KeyError for an unknown app."""
    app = apps_repo.get_app(conn, app_id)
    if not app:
        raise KeyError(f"Unknown app: {app_id}")
    return app


# ---------------------------------------------------------------------------
# Missing-items refresh (shared with artifact generation)
# ---------------------------------------------------------------------------

def refresh_missing_items(conn: sqlite3.Connection, release_id: str) -> dict[str, list]:
    """Recompute missing_items for every snapshot in the release; return map.

    Only writes back when the recomputed value (or normalized decision) differs
    from what is stored, so /api/state polling doesn't thrash the DB.
    Mirrors core.py:refresh_missing_items (with the community-fields overlay).
    """
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        return {app_id: snap.get("missing_items", []) for app_id, snap in release["snapshots"].items()}
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    results: dict[str, list] = {}
    with transaction(conn):
        for app_id, snapshot in release["snapshots"].items():
            app = apps.get(app_id)
            if not app:
                continue
            before = dumps_json(snapshot)
            snapshot["release_decision"] = normalize_release_decision(snapshot.get("release_decision"))
            snapshot.pop("cicd", None)
            items = _missing_items_for(app, snapshot)
            snapshot["missing_items"] = items
            results[app_id] = items
            after = dumps_json(snapshot)
            if before != after:
                snapshots_repo.save_snapshot(conn, release_id, app_id, snapshot)
    return results


def release_qa_audit_logs(conn: sqlite3.Connection, release_id: str) -> dict[str, list[dict[str, Any]]]:
    """QA status-change audit entries grouped by app for one release.

    Mirrors core.py:release_qa_audit_logs.
    """
    release = release_reads.get_release(conn, release_id)
    app_ids = [
        app_id
        for app_id, snapshot in release["snapshots"].items()
        if snapshot.get("release_decision") == "release"
    ]
    if not app_ids:
        return {}
    return qa_audit_logs_by_app(conn, release_id, app_ids)


def get_state(
    conn: sqlite3.Connection,
    *,
    user: dict,
    release_id_param: str = "",
) -> dict:
    """Build the full page-state payload.

    Mirrors server.py:state_payload exactly.
    """
    releases = release_reads.list_releases(conn)
    release_ids = {r["id"] for r in releases}
    latest = releases[-1]["id"] if releases else ""
    requested = release_id_param or latest
    release_id = requested if requested in release_ids else latest

    apps = apps_repo.list_apps(conn)
    from app.services import cicd_service as _cicd_svc

    _cicd_svc.attach_cicd_onboarding_state(conn, apps)
    payload: dict = {
        "apps": apps,
        "releases": [_serialize_release(r) for r in releases],
        "release": None,
        "artifacts": [],
        "user": {
            "username": user["username"],
            "role": user["role"],
            "display_name": user.get("display_name", ""),
        },
        "user_display_names": {
            row["username"]: row["display_name"]
            for row in conn.execute(
                "SELECT username, display_name FROM users WHERE display_name <> ''"
            )
        },
        "qa_log": None,
        "qa_audit_logs": {},
        "release_schedule": schedule_repo.list_schedule(conn),
    }
    if release_id:
        refresh_missing_items(conn, release_id)
        release = release_reads.get_release(conn, release_id)
        payload["release"] = _serialize_release(release)
        payload["artifacts"] = [
            dict(row)
            for row in conn.execute(
                "SELECT kind, name, final, generated_at FROM artifacts WHERE release_id = ?",
                (release_id,),
            )
        ]
        payload["qa_log"] = qa_repo.get_qa_log(conn, release_id)
        if user["role"] in {"QA", "RM", "Owner", "Guest"}:
            payload["qa_audit_logs"] = release_qa_audit_logs(conn, release_id)
        apps_by_id = {app["id"]: app for app in apps}
        for aid, snap in (payload["release"] or {}).get("snapshots", {}).items():
            app = apps_by_id.get(aid)
            if app:
                snap["missing_items"] = _missing_items_for(app, snap)
    return payload


# ---------------------------------------------------------------------------
# App audit log (for GET /api/app-audit)
# ---------------------------------------------------------------------------

def get_app_audit(
    conn: sqlite3.Connection,
    *,
    app_id: str,
    release_id: str,
    username: str,
    role: str,
) -> list[dict]:
    """Return audit entries for an app, enforcing access control.

    Mirrors server.py:393-400 + require_app_audit_access.
    """
    authz_require_app_audit_access(conn, app_id, username, role, release_id)
    return repo_app_audit_log(conn, app_id, release_id)


# ---------------------------------------------------------------------------
# New app (for POST /api/apps/new)
# ---------------------------------------------------------------------------

def _initial_snapshot_for_future_release(
    snapshot: dict[str, Any],
    target_release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mirror core.py:_initial_snapshot_for_future_release."""
    future = json.loads(json.dumps(snapshot))
    future.pop("locked_in_release", None)
    future.update(
        {
            "qa_status": "not_checked",
            "qa_issue_note": "",
            "missing_items": [],
        }
    )
    if (
        target_release
        and future.get("release_decision") == "release"
        and not phase_policy.can(target_release, "new_app_release")
    ):
        future["release_decision"] = "cicd_only"
    return future


def add_new_app_request(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    official_name: str,
    git_url: str,
    git_branch: str,
    release_decision: str,
    owner: str,
    doc_target: str = "manual",
) -> str:
    """Find-or-create an app and register it in this + future unlocked releases.

    Mirrors core.py:add_new_app_request.
    """
    raw_decision = (release_decision or "").strip()
    git_url = (git_url or "").strip()
    git_branch = (git_branch or "").strip()
    if not official_name or not git_url or not git_branch or not raw_decision or not owner:
        raise ValueError("New app requires official_name, git_url, git_branch, release_decision, and submitter owner")
    release_decision = normalize_release_decision(raw_decision)
    if release_decision not in RELEASE_DECISIONS:
        raise ValueError(f"Invalid release_decision: {release_decision}")
    doc_target = normalize_doc_target(doc_target)
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可新增 app")
    intended_action = "new_app_release" if release_decision == "release" else "new_app_non_release"
    if not phase_policy.can(release, intended_action):
        if release_decision == "release":
            raise RuntimeError("已过 app 冻结 deadline，不可再新增以 release 状态进入本期的 app")
        raise RuntimeError("当前阶段不允许新增 app")
    duplicate = apps_repo.find_by_identity(conn, git_url, git_branch)
    if duplicate:
        raise RuntimeError(
            f"该 Gerrit URL + branch 已登记为 app（id={duplicate['id']}）："
            f"无需重复新增；如需让它参与本 release，请联系现有 owner 或 RM"
        )
    base_id = normalize_name(official_name)
    if not base_id:
        raise ValueError(f"无法由名称生成有效的 app id：{official_name!r}")
    used_ids = apps_repo.all_app_ids(conn)
    app_id = base_id if base_id not in used_ids else variant_app_id(base_id, "", git_branch, used_ids)
    ts = beijing_timestamp()
    app = {
        "id": app_id,
        "git_url": git_url,
        "git_branch": git_branch,
        "aliases": [official_name],
        "created_by": owner,
        "created_at": ts,
    }
    snapshot = base_snapshot(
        app_id,
        official_name=official_name,
        doc_target=doc_target,
        owners=[owner],
    )
    snapshot["release_decision"] = release_decision
    source_name = release["name"]
    with transaction(conn):
        apps_repo.save_app(conn, app)
        for target_release_id in releases_repo.future_unlocked_release_ids(conn, release_id):
            target_release = release_reads.get_release(conn, target_release_id)
            if app_id in target_release["snapshots"]:
                continue
            if target_release_id == release_id:
                snapshots_repo.save_snapshot(conn, target_release_id, app_id, snapshot)
                log_audit(
                    conn,
                    f"新增 app：{official_name}（owner={owner}，初始决策={release_decision}）",
                    ts=ts, user=owner, role="Owner", app_id=app_id,
                    release_id=target_release_id, event="create_app",
                )
            else:
                snapshots_repo.save_snapshot(
                    conn, target_release_id, app_id,
                    _initial_snapshot_for_future_release(snapshot, target_release),
                )
                log_audit(
                    conn,
                    f"本 app 在「{source_name}」新增后，同步到本 release",
                    ts=ts, user=owner, role="Owner", app_id=app_id,
                    release_id=target_release_id, event="sync_app",
                )
    return app_id


def add_new_app(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    user: str,
    **payload,
) -> dict:
    """Add a new app to a release (find-or-create with git identity dedup).

    Mirrors server.py:759-773. Returns {"app_id": app_id}.
    """
    app_id = add_new_app_request(
        conn,
        release_id,
        official_name=payload["official_name"],
        git_url=payload["git_url"],
        git_branch=payload["git_branch"],
        release_decision=payload["release_decision"],
        owner=user,
        doc_target=payload.get("doc_target", "manual"),
    )
    apps_repo.update_cicd_config(
        conn,
        app_id,
        {key: payload.get(key, "") for key in CICD_APP_CONFIG_FIELDS},
    )
    conn.commit()
    return {"app_id": app_id}


# ---------------------------------------------------------------------------
# Snapshot update (for POST /api/apps/update)
# ---------------------------------------------------------------------------

def update_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    *,
    user: str,
    role: str,
    fields: dict,
) -> dict:
    """Save snapshot fields; optionally sync decision to other releases.

    Mirrors server.py:775-939 (the /api/apps/update handler) exactly.

    *fields* is the full parsed POST body (keys: snapshot, app, sync_decision).

    Returns the response dict: {snapshot, missing_items, qa_status} and
    optionally decision_sync when release_decision actually changed and either
    body.sync_decision is truthy or the change crosses the CICD Running/Stopped
    boundary.

    Ruling D: when release_decision changes, cicd_service.sync_decision_to_cicd
    is called INSIDE the same transaction to produce a pending CICD status-modify
    request.  The response includes «cicd_sync» when the decision changed.
    """
    body = fields  # alias for readability — mirrors server.py variable name

    conn_ref = conn  # kept for closure use below
    app = _get_app_or_raise(conn, app_id)
    release = release_reads.get_release(conn, release_id)

    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定")

    snap_now = release["snapshots"].get(app_id, {})
    require_owner_or_rm_with_owners(snap_now.get("owners"), user, role)

    snap_update = body.get("snapshot", {})

    app_update = body.get("app", {}) if isinstance(body.get("app", {}), dict) else {}
    app_update_keys = set(app_update)
    app_cicd_keys = app_update_keys & CICD_APP_CONFIG_FIELDS
    app_repo_identity_keys = app_update_keys & APP_REPO_IDENTITY_FIELDS
    app_owner_allowed_keys = CICD_APP_CONFIG_FIELDS | APP_REPO_IDENTITY_FIELDS
    app_owner_forbidden_keys = app_update_keys - app_owner_allowed_keys

    if role == "Owner":
        owner_content_keys = set(snap_update) - {"release_decision", "owner_confirmed"}
        if (app_update_keys or owner_content_keys) and snap_update.get("owner_confirmed") is not True:
            raise AuthzError("Owner edits must be saved with Owner confirmation")
        if app_owner_forbidden_keys:
            raise AuthzError("Owner 只能修改 App CICD 配置和 Gerrit URL / Branch")
        if "owner_confirmed" in snap_update and snap_update["owner_confirmed"] is not True:
            raise AuthzError("Owner confirmation can only be submitted, not cleared")

    _require_app_update_phase_permissions(
        release,
        snap_update_keys=set(snap_update),
        app_cicd_keys=app_cicd_keys,
        app_repo_identity_keys=app_repo_identity_keys,
        app_other_keys=app_update_keys - CICD_APP_CONFIG_FIELDS - APP_REPO_IDENTITY_FIELDS,
    )
    phase = phase_policy.current_phase(release)
    past_app_freeze = phase in {"after_app_freeze", "after_doc_deadline", "released_locked"}
    past_doc_deadline = phase in {"after_doc_deadline", "released_locked"}

    current_decision = normalize_release_decision(
        snap_now.get("release_decision", "release")
    )
    new_decision = snap_update.get("release_decision")
    requested_decision = current_decision
    defer_decision_until_cicd_delivery = False
    if new_decision is not None:
        new_decision_norm = normalize_release_decision(new_decision)
        requested_decision = new_decision_norm
        if new_decision_norm != current_decision:
            if new_decision_norm == "release" and (past_app_freeze or past_doc_deadline):
                raise RuntimeError(
                    "已过 app freeze/doc deadline，不可将 release 决策切换为 release，"
                    "只能选择 cicd_only 或 stopped"
                )
            defer_decision_until_cicd_delivery = decision_sync_domain.is_running_upgrade(
                current_decision,
                new_decision_norm,
            )
    if (
        requested_decision != current_decision
        and decision_sync_domain.crosses_runtime_boundary(current_decision, requested_decision)
    ):
        from app.services import cicd_service as _cicd_svc

        _cicd_svc.ensure_can_open_cicd_modify_request(conn, app_id)

    owner_meta = {"type", "official_url", "description"}
    doc_labels = {
        "intro": "基本介绍",
        "image_usage": "镜像使用方法",
        "binary_usage": "二进制包使用方法",
        "env_setup": "环境搭建",
        "limitations": "已知限制",
    }

    # Capture snap_now for use inside the closures (mirrors server.py variable)
    aid = app_id
    rid = release_id
    actor = user
    ts = beijing_timestamp()

    def update_app_if_needed() -> None:
        if not app_update_keys:
            return
        repo_changed = False
        repo_before = {
            "git_url": app.get("git_url", ""),
            "git_branch": app.get("git_branch", ""),
        }
        for key in ("git_url", "git_branch"):
            if key in app_update and app.get(key) != app_update[key]:
                app[key] = str(app_update[key] or "").strip()
                repo_changed = True
        if repo_changed:
            collision = conn_ref.execute(
                "SELECT id FROM apps WHERE git_url = ? AND git_branch = ? AND id != ?",
                (app.get("git_url", ""), app.get("git_branch", ""), aid),
            ).fetchone()
            if collision:
                raise RuntimeError(
                    f"该 Gerrit URL + branch 已被 app {collision['id']} 占用，不能改成相同值"
                )
            apps_repo.save_app(conn_ref, app)
            log_audit(
                conn_ref,
                "修改 Gerrit 信息",
                ts=ts,
                user=actor,
                role=role,
                app_id=aid,
                release_id=rid,
                event="update_app_repo",
                detail=field_diff(
                    repo_before,
                    app,
                    {"git_url": "Gerrit URL", "git_branch": "Branch"},
                ),
            )

        cicd_update = {key: app_update.get(key, "") for key in app_cicd_keys}
        if cicd_update:
            cicd_before = {key: app.get(key, "") for key in CICD_APP_CONFIG_FIELDS}
            apps_repo.update_cicd_config(conn_ref, aid, cicd_update)
            app.update({key: str(value or "").strip() for key, value in cicd_update.items()})
            cicd_after = {key: app.get(key, "") for key in CICD_APP_CONFIG_FIELDS}
            changes = field_diff(cicd_before, cicd_after, CICD_APP_CONFIG_LABELS)
            if changes:
                log_audit(
                    conn_ref,
                    "修改 App CICD 配置",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_app_cicd_config",
                    detail=changes,
                )

    def normalize_community_update(snapshot: dict) -> None:
        if _community_required(app):
            return
        snap_update["community"] = {
            "release_status": "",
            "python_version": "",
            "framework_version": "",
        }

    def mutate(snapshot: dict) -> None:
        name_for_msg = snapshot.get("official_name") or aid
        if "release_decision" in snap_update:
            decision = normalize_release_decision(snap_update["release_decision"])
            if decision not in RELEASE_DECISIONS:
                raise ValueError(f"Invalid release_decision: {snap_update['release_decision']}")
            if defer_decision_until_cicd_delivery:
                pass
            elif decision != snapshot.get("release_decision"):
                log_audit(
                    conn_ref,
                    (
                        f"修改 release 决策：{name_for_msg} "
                        f"{snapshot.get('release_decision')} -> {decision}"
                    ),
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_release_decision",
                    detail=field_diff(
                        {"release_decision": snapshot.get("release_decision")},
                        {"release_decision": decision},
                        {"release_decision": "release 决策"},
                    ),
                )
                snapshot["release_decision"] = decision

        meta_before: dict = {}
        meta_after: dict = {}
        for key in SNAPSHOT_META_FIELDS:
            if key not in snap_update:
                continue
            value = snap_update[key]
            if key == "doc_target":
                value = normalize_doc_target(value)
            elif key == "description":
                value = normalize_app_description(value)
            elif key == "owners":
                value = sorted(
                    {str(o).strip() for o in (value or []) if str(o).strip()}
                )
            else:
                value = (value or "").strip()
            if snapshot.get(key) == value:
                continue
            if key not in owner_meta and role != "RM":
                raise AuthzError(f"仅 RM 可修改{APP_META_LABELS.get(key, key)}")
            meta_before[key] = snapshot.get(key)
            meta_after[key] = value
            snapshot[key] = value

        if meta_after:
            log_audit(
                conn_ref,
                f"修改 app 基本信息：{name_for_msg}",
                ts=ts,
                user=actor,
                role=role,
                app_id=aid,
                release_id=rid,
                event="update_app_meta",
                detail=field_diff(meta_before, meta_after, APP_META_LABELS),
            )

        if "owner_confirmed" in snap_update:
            if role != "Owner":
                raise AuthzError("Owner confirmation must be submitted by an Owner")
            if snap_update["owner_confirmed"] and not snapshot.get("owner_confirmed"):
                log_audit(
                    conn_ref,
                    f"提交 Owner 确认：{name_for_msg}",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="owner_confirm",
                    detail=[
                        {
                            "field": "owner_confirmed",
                            "label": "Owner 确认",
                            "old": "未确认",
                            "new": "已确认",
                        }
                    ],
                )
            snapshot["owner_confirmed"] = snap_update["owner_confirmed"]

        if "doc" in snap_update:
            doc_update = snap_update["doc"]
            current_doc = snapshot.get("doc", {})
            doc_changes = field_diff(
                current_doc,
                doc_update,
                {k: doc_labels.get(k, k) for k in doc_update},
            )
            if doc_changes:
                log_audit(
                    conn_ref,
                    f"修改文档字段：{name_for_msg}",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_doc_fields",
                    detail=doc_changes,
                )
            snapshot.setdefault("doc", {}).update(doc_update)

        if "community" in snap_update:
            comm_update = snap_update["community"]
            comm_labels = {
                "release_status": "社区发布情况",
                "python_version": "社区包 Python 版本",
                "framework_version": "社区包框架及版本",
            }
            comm_before = {
                k: (snapshot.get("community") or {}).get(k, "") for k in comm_labels
            }
            comm_changes = field_diff(comm_before, comm_update, comm_labels)
            if comm_changes:
                log_audit(
                    conn_ref,
                    f"修改社区发布信息：{name_for_msg}",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_community",
                    detail=comm_changes,
                )
            snapshot.setdefault("community", {}).update(comm_update)

        if "sanity" in snap_update:
            sanity_update = snap_update["sanity"]
            sanity_labels = {
                "arm_kylin": "ARM / Kylin Sanity",
                "ubuntu": "Ubuntu / 兼容性 Sanity",
            }
            sanity_before = {
                k: bool((snapshot.get("sanity") or {}).get(k)) for k in sanity_labels
            }
            sanity_changes = field_diff(sanity_before, sanity_update, sanity_labels)
            if sanity_changes:
                if role != "RM":
                    raise AuthzError("仅 RM 可修改 Sanity 信息")
                log_audit(
                    conn_ref,
                    f"修改 Sanity 信息：{name_for_msg}",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_sanity",
                    detail=sanity_changes,
                )
                snapshot.setdefault("sanity", {}).update(sanity_update)

        if "test_docs" in snap_update:
            before_docs = [dict(d) for d in snapshot.get("test_docs", [])]
            by_id = {doc["id"]: doc for doc in snapshot.get("test_docs", [])}
            for item in snap_update["test_docs"]:
                if item.get("id") in by_id:
                    by_id[item["id"]].update(item)
                elif item.get("owner_added"):
                    item.setdefault("id", new_id("testdoc"))
                    item.setdefault("path", f"owner_added.{len(by_id) + 1}")
                    snapshot.setdefault("test_docs", []).append(item)
            td_changes = test_docs_diff(before_docs, snapshot.get("test_docs", []))
            if td_changes:
                log_audit(
                    conn_ref,
                    f"修改测试说明：{name_for_msg}",
                    ts=ts,
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_test_docs",
                    detail=td_changes,
                )

    # Execute inside a single transaction (mirrors server.py:929-939)
    response: dict = {}
    with transaction(conn):
        update_app_if_needed()
        normalize_community_update(snap_now)
        # Inline core.py:update_snapshot — release re-read + lock/phase check
        # happened above; mutate + save under the same transaction.
        if not past_doc_deadline:
            phase_policy.require_can(
                release, "edit_snapshot", "已过 doc deadline，不可再修改文档/表单信息"
            )
        updated = release["snapshots"][aid]
        mutate(updated)
        updated["missing_items"] = _missing_items_for(app, updated)
        snapshots_repo.save_snapshot(conn, rid, aid, updated)
        response = {
            "snapshot": updated,
            "missing_items": updated.get("missing_items", []),
            "qa_status": updated.get("qa_status"),
        }
        cicd_req: dict | None = None
        if requested_decision != current_decision:
            # Ruling D: unconditionally sync decision → CICD task status inside
            # the same transaction, so the sync request and the snapshot save are
            # atomic. Phase gate already ran above (raises before we get here).
            from app.services import cicd_service as _cicd_svc
            cicd_req = _cicd_svc.sync_decision_to_cicd(
                conn,
                aid,
                requested_decision,
                submitter=actor,
                current_status_override=_cicd_svc._DECISION_TO_CICD_STATUS.get(
                    current_decision, ""
                ),
                release_id=rid,
                apply_release_decision_on_delivery=defer_decision_until_cicd_delivery,
            )
            response["cicd_sync"] = {
                "created": cicd_req is not None,
                "request": cicd_req,
            }
        forced_decision_sync = (
            requested_decision != current_decision
            and decision_sync_domain.crosses_runtime_boundary(current_decision, requested_decision)
        )
        if (
            (body.get("sync_decision") or forced_decision_sync)
            and requested_decision != current_decision
        ):
            # R3: use the new app-layer gating rule (NOT core's).
            if defer_decision_until_cicd_delivery and cicd_req is None:
                raise RuntimeError(
                    "已有未完成 CICD 运行状态变更申请，不能继续同步 release 决策"
                )
            response["decision_sync"] = sync_decision_to_later_releases(
                conn,
                rid,
                aid,
                requested_decision,
                user=actor,
                role=role,
                scope="all_unlocked" if forced_decision_sync else "later",
                defer_apply=defer_decision_until_cicd_delivery,
            )
            response["decision_sync"]["forced"] = forced_decision_sync
            if defer_decision_until_cicd_delivery and cicd_req is not None:
                from app.services import cicd_service as _cicd_svc
                deferred_entries = [
                    {
                        "release_id": item["release_id"],
                        "target_decision": item["resulting_decision"],
                    }
                    for item in response["decision_sync"].get("applied", [])
                    if item.get("changed")
                ]
                cicd_req = _cicd_svc.attach_deferred_release_decisions(
                    conn,
                    cicd_req["id"],
                    deferred_entries,
                )
                response["cicd_sync"]["request"] = cicd_req
    return response


# ---------------------------------------------------------------------------
# Decision sync to related releases (R3 gating rule + dry-run preview)
# ---------------------------------------------------------------------------

def sync_decision_to_later_releases(
    conn: sqlite3.Connection,
    from_release_id: str,
    app_id: str,
    decision: str,
    *,
    user: str = "system",
    role: str = "system",
    scope: str = "later",
    defer_apply: bool = False,
) -> dict:
    """Apply a release_decision to related releases.

    R3 reimplementation of ``core.sync_decision_to_later_releases`` with the
    changed gating rule (see ``app.domain.decision_sync``):
      - locked release → skipped (reason "已最终锁定")
      - app absent → skipped (reason "本 release 无此 app")
      - otherwise apply ``resolve_synced_decision(decision, phase)``: an upgrade
        to ``release`` on a release past app-freeze OR doc-deadline becomes
        ``cicd_only`` rather than being skipped.
      - ``scope="later"`` keeps the legacy optional behavior.
      - ``scope="all_unlocked"`` is for Running/Stopped boundary changes and
        visits every other release, including earlier ones.

    When ``defer_apply`` is true, the same target list is returned but snapshots
    are left unchanged so CICD delivery can apply every release at once.

    Response shape mirrors core ({"applied": [...], "skipped": [...]}) but each
    applied entry is extended with its ``resulting_decision``.
    """
    decision = normalize_release_decision(decision)
    result: dict[str, list] = {"applied": [], "skipped": []}
    releases = release_reads.list_releases(conn)
    idx = next((i for i, r in enumerate(releases) if r["id"] == from_release_id), None)
    if idx is None:
        return result
    if scope not in {"later", "all_unlocked"}:
        raise ValueError(f"Invalid decision sync scope: {scope}")
    target_releases = (
        [r for r in releases if r["id"] != from_release_id]
        if scope == "all_unlocked"
        else releases[idx + 1:]
    )
    ts = beijing_timestamp()
    with transaction(conn):
        for r in target_releases:
            rid = r["id"]
            if r.get("released_locked"):
                result["skipped"].append(
                    {"release_id": rid, "release_name": r["name"], "reason": "已最终锁定"}
                )
                continue
            release = release_reads.get_release(conn, rid)
            snapshot = release["snapshots"].get(app_id)
            if snapshot is None:
                result["skipped"].append(
                    {"release_id": rid, "release_name": r["name"], "reason": "本 release 无此 app"}
                )
                continue
            phase = phase_policy.current_phase(release)
            resulting = decision_sync_domain.resolve_synced_decision(decision, phase)
            previous = normalize_release_decision(
                snapshot.get("release_decision", "release")
            )
            changed = previous != resulting
            if changed and not defer_apply:
                snapshot["release_decision"] = resulting
                snapshot["missing_items"] = _missing_items_for(
                    _get_app_or_raise(conn, app_id), snapshot
                )
                snapshots_repo.save_snapshot(conn, rid, app_id, snapshot)
            result["applied"].append(
                {
                    "release_id": rid,
                    "release_name": r["name"],
                    "previous_decision": previous,
                    "resulting_decision": resulting,
                    "changed": changed,
                }
            )
        if result["applied"] and not defer_apply:
            target_label = "所有未锁定 release" if scope == "all_unlocked" else "后续 release"
            log_audit(
                conn,
                f"同步 release 决策（{decision}）到 {len(result['applied'])} 个{target_label}",
                ts=ts,
                user=user,
                role=role,
                app_id=app_id,
                release_id=from_release_id,
                event="sync_decision",
            )
    return result


def preview_decision_sync(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    app_id: str,
    decision: str,
) -> dict:
    """Dry-run of ``sync_decision_to_later_releases`` — NO writes.

    Returns ``{"decision": <normalized>, "releases": [row, ...], "forced": bool,
    "scope": "later"|"all_unlocked"}`` where each row is one target release:
    ``{release_id, release_name, phase_label, resulting_decision, skipped,
    reason?}``. ``resulting_decision`` is ``None`` for skipped rows. Drives the
    owner-choice dialog table before applying.
    """
    decision = normalize_release_decision(decision)
    releases = release_reads.list_releases(conn)
    idx = next((i for i, r in enumerate(releases) if r["id"] == release_id), None)
    rows: list[dict] = []
    if idx is None:
        return {"decision": decision, "releases": rows, "forced": False, "scope": "later"}
    current_release = release_reads.get_release(conn, release_id)
    current_snapshot = current_release.get("snapshots", {}).get(app_id, {})
    current_decision = normalize_release_decision(
        current_snapshot.get("release_decision", "release")
    )
    forced = decision_sync_domain.crosses_runtime_boundary(current_decision, decision)
    scope = "all_unlocked" if forced else "later"
    target_releases = (
        [r for r in releases if r["id"] != release_id]
        if scope == "all_unlocked"
        else releases[idx + 1:]
    )
    for r in target_releases:
        rid = r["id"]
        if r.get("released_locked"):
            rows.append(
                {
                    "release_id": rid,
                    "release_name": r["name"],
                    "phase_label": decision_sync_domain.phase_label("released_locked"),
                    "resulting_decision": None,
                    "skipped": True,
                    "reason": "已最终锁定",
                }
            )
            continue
        release = release_reads.get_release(conn, rid)
        phase = phase_policy.current_phase(release)
        if app_id not in release.get("snapshots", {}):
            rows.append(
                {
                    "release_id": rid,
                    "release_name": r["name"],
                    "phase_label": decision_sync_domain.phase_label(phase),
                    "resulting_decision": None,
                    "skipped": True,
                    "reason": "本 release 无此 app",
                }
            )
            continue
        resulting = decision_sync_domain.resolve_synced_decision(decision, phase)
        rows.append(
            {
                "release_id": rid,
                "release_name": r["name"],
                "phase_label": decision_sync_domain.phase_label(phase),
                "resulting_decision": resulting,
                "skipped": False,
            }
        )
    return {"decision": decision, "releases": rows, "forced": forced, "scope": scope}


# ---------------------------------------------------------------------------
# App info apply / fetch (for POST /api/app-info, /fetch, /fetch-all)
# ---------------------------------------------------------------------------

def _apply_app_info_core(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    raw: str | dict[str, Any],
    *,
    source: str = "upload",
    source_type: str = "owner_upload",
    commit_id: str = "",
    uploaded_by: str = "",
    role: str = "Owner",
) -> dict[str, Any]:
    """Parse + apply an app_info payload to a snapshot — mirrors core.py:apply_app_info."""
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可上传 app_info")
    phase_policy.require_can(release, "edit_app_info", "已过 doc deadline，不可再上传 app_info")
    snapshot = release["snapshots"][app_id]
    was_confirmed = bool(snapshot.get("owner_confirmed"))
    snapshot_parsed = (snapshot.get("app_info") or {}).get("parsed")
    parsed = app_info_domain.parse_app_info(raw)
    # Owner confirmation should only become invalid when the snapshot's own
    # app_info content actually changes — a re-upload of the same file, a
    # clone from a previous release, or a fetch that returns identical
    # content must not silently force the owner to re-confirm.
    content_modified = snapshot_parsed is not None and bool(
        app_info_domain.diff_app_info(snapshot_parsed, parsed)
    )
    if snapshot.get("release_decision") == "release" and not phase_policy.can(release, "expand_qa_scope"):
        current_parsed = (snapshot.get("app_info") or {}).get("parsed")
        if current_parsed is not None:
            additions = app_info_domain.qa_scope_additions(current_parsed, parsed)
            if additions:
                raise RuntimeError(
                    "已过 app 冻结 deadline，新 app_info 会扩大 QA 范围（"
                    + "；".join(additions)
                    + "）。如确需新增，请联系 RM 调整 app 冻结 deadline。"
                )
    previous_id = releases_repo.previous_release_id(conn, release_id)
    old_parsed = None
    if previous_id:
        previous_snapshot = snapshots_repo.get_snapshot(conn, previous_id, app_id)
        if previous_snapshot:
            old_parsed = (previous_snapshot.get("app_info") or {}).get("parsed")
    if old_parsed is None:
        old_parsed = (snapshot.get("app_info") or {}).get("parsed")
    diffs = app_info_domain.diff_app_info(old_parsed, parsed) if old_parsed is not None else []
    ts = beijing_timestamp()
    snapshot["app_info"] = {
        "source": source,
        "source_type": source_type,
        "synced_at": ts,
        "commit_id": commit_id,
        "uploaded_by": uploaded_by,
        "raw": parsed["raw"],
        "parsed": parsed,
    }
    snapshot["app_info_diffs"] = diffs
    snapshot["version"] = parsed.get("app_version") or snapshot.get("version", "")
    snapshot["x86_chips"] = ",".join(parsed.get("x86_chips", []))
    snapshot["arm_chips"] = ",".join(parsed.get("arm_chips", []))
    snapshot["python_labels"] = ",".join(parsed.get("python_labels", []))
    snapshot["pytorch_labels"] = ",".join(parsed.get("pytorch_labels", []))
    snapshot["build_os"] = ",".join(parsed.get("build_os", []))
    snapshot["build_arches"] = ",".join(parsed.get("build_arches", []))
    app_info_domain.ensure_test_docs(snapshot, parsed, diffs)
    if was_confirmed and content_modified:
        snapshot["owner_confirmed"] = False
    # build_targets / test_targets are coarse list-of-dict aggregates; the
    # readable per-field diffs (version, chips, test_cmd*) cover the same ground.
    detail = [
        {
            "field": d.get("field", ""),
            "label": f"{d.get('type', '')}（{d.get('field', '')}）",
            "old": fmt_audit_value(d.get("old_value")),
            "new": fmt_audit_value(d.get("new_value")),
        }
        for d in diffs
        if d.get("field") not in ("build_targets", "test_targets")
    ]
    with transaction(conn):
        snapshots_repo.save_snapshot(conn, release_id, app_id, snapshot)
        log_audit(
            conn,
            f"{app_id} 更新 app_info.json，差异 {len(diffs)} 项",
            ts=ts,
            user=uploaded_by or "system",
            role=role,
            app_id=app_id,
            release_id=release_id,
            event="upload_app_info",
            detail=detail,
        )
        if was_confirmed and content_modified:
            log_audit(
                conn,
                f"{app_id} Owner 确认因 app_info 更新自动失效",
                ts=ts,
                user=uploaded_by or "system",
                role=role,
                app_id=app_id,
                release_id=release_id,
                event="owner_confirm_invalidated",
                detail=[{"field": "owner_confirmed", "label": "Owner 确认",
                         "old": "已确认", "new": "未确认（app_info 更新自动失效）"}],
            )
    return snapshot


def apply_app_info(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    app_id: str,
    app_info: object,
    source: str = "owner upload",
    source_type: str = "owner_upload",
    uploaded_by: str,
    role: str,
) -> dict:
    """Apply app_info JSON to a snapshot.

    Mirrors server.py:1200-1216. Returns {"snapshot": snapshot}.
    """
    snapshot = _apply_app_info_core(
        conn,
        release_id,
        app_id,
        app_info,
        source=source,
        source_type=source_type,
        uploaded_by=uploaded_by,
        role=role,
    )
    return {"snapshot": snapshot}


def _app_info_fetch_target(app: dict) -> tuple[str, str, str]:
    """Return (fetch_url, fetch_branch, source_label) for Gerrit app_info fetch.

    App Workbench stores normal git apps as a repo URL/branch, but repo-style
    apps may store an APP/.../*.xml manifest path.  Manifest paths must be
    resolved through the same identity seam used by CICD/cutover before
    fetching app_info.json from the real underlying Gerrit project.
    """
    original_url = str(app.get("git_url") or "").strip()
    original_branch = str(app.get("git_branch") or "").strip()
    if not original_url or not original_branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")

    fetch_url = original_url
    fetch_branch = original_branch
    source = f"{original_url} {original_branch}:app_info.json"

    if original_url.endswith(".xml"):
        from app.identity import repo_to_git_identity

        resolved_url, resolved_branch = repo_to_git_identity(
            "repo", original_url, original_branch
        )
        if not resolved_url or not resolved_branch:
            raise RuntimeError(
                "无法解析 repo manifest，不能拉取 app_info.json："
                f"{original_url} @ {original_branch}"
            )
        fetch_url = resolved_url
        fetch_branch = resolved_branch
        source = (
            f"{original_url} {original_branch}:app_info.json"
            f" -> {fetch_url} {fetch_branch}:app_info.json"
        )

    return fetch_url, fetch_branch, source


def fetch_app_info(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    app_id: str,
    uploaded_by: str,
    role: str,
) -> dict:
    """Fetch app_info from Gerrit and apply it.

    Mirrors server.py:1218-1237.
    Returns {"snapshot": snapshot, "commit_id": commit_id, "source": source}.
    """
    from app.config import settings
    from app.integrations.gerrit import fetch_app_info as gerrit_fetch

    app = _get_app_or_raise(conn, app_id)
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可上传 app_info")
    phase_policy.require_can(release, "edit_app_info", "已过 doc deadline，不可再上传 app_info")
    fetch_url, fetch_branch, source = _app_info_fetch_target(app)
    # project_root = cwd for git subprocess (mirrors server.py ROOT)
    _project_root = settings.db_path.parent
    raw, commit_id = gerrit_fetch(
        fetch_url,
        fetch_branch,
        project_root=_project_root,
        hpc_gerrit_prefix=settings.hpc_gerrit_prefix,
        hpc_gerrit_root=settings.hpc_gerrit_root,
    )
    snapshot = _apply_app_info_core(
        conn,
        release_id,
        app_id,
        raw,
        source=source,
        source_type="gerrit_fetch",
        commit_id=commit_id,
        uploaded_by=uploaded_by,
        role=role,
    )
    return {
        "snapshot": snapshot,
        "commit_id": commit_id,
        "source": snapshot.get("app_info", {}).get("source", ""),
        "fetch_git_url": fetch_url,
        "fetch_git_branch": fetch_branch,
    }


def fetch_all_app_infos(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    uploaded_by: str,
    role: str = "RM",
) -> dict:
    """Fetch app_info from Gerrit for every app in a release.

    Mirrors server.py:1239-1243 + fetch_all_app_infos_from_gerrit.
    """
    from app.config import settings
    from app.integrations.gerrit import fetch_app_info as gerrit_fetch

    _project_root = settings.db_path.parent
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可上传 app_info")
    phase_policy.require_can(release, "edit_app_info", "已过 doc deadline，不可再上传 app_info")
    results = []
    for app_id in sorted(release.get("snapshots", {})):
        try:
            app = _get_app_or_raise(conn, app_id)
            fetch_url, fetch_branch, source = _app_info_fetch_target(app)
            raw, commit_id = gerrit_fetch(
                fetch_url,
                fetch_branch,
                project_root=_project_root,
                hpc_gerrit_prefix=settings.hpc_gerrit_prefix,
                hpc_gerrit_root=settings.hpc_gerrit_root,
            )
            snapshot = _apply_app_info_core(
                conn,
                release_id,
                app_id,
                raw,
                source=source,
                source_type="gerrit_fetch",
                commit_id=commit_id,
                uploaded_by=uploaded_by,
                role=role,
            )
            results.append({
                "app_id": app_id,
                "ok": True,
                "commit_id": commit_id,
                "source": snapshot.get("app_info", {}).get("source", ""),
                "fetch_git_url": fetch_url,
                "fetch_git_branch": fetch_branch,
            })
        except Exception as exc:
            results.append({"app_id": app_id, "ok": False, "error": str(exc)})

    succeeded = sum(1 for item in results if item["ok"])
    return {
        "ok": True,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }
