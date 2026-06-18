"""App workbench service — app CRUD, snapshot update, and app_info fetch.

Faithful 1:1 port of the matching server.py handlers + core.py functions.
All transaction boundaries, audit messages, and error text match the old
server exactly.  Ruling D (decision↔CICD status sync) is wired into
update_snapshot via cicd_service.sync_decision_to_cicd (Phase 4 Wave 2).

Per the brief (DA finding P1): snapshots stay loose dicts — no strict
Pydantic field validation.
"""
from __future__ import annotations

import sqlite3

import release_system.core as core
from app.api.errors import AuthzError
from app.domain import decision_sync as decision_sync_domain
from app.repositories import apps_repo
from app.repositories.audit_repo import app_audit_log as repo_app_audit_log
from app.services.authz import (
    require_app_audit_access as authz_require_app_audit_access,
)
from app.services.authz import (
    require_owner_or_rm_with_owners,
)

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


def _community_required(app: dict) -> bool:
    return bool((app.get("cicd_community_artifact") or "").strip())


def _missing_items_for(app: dict, snapshot: dict) -> list[dict[str, str]]:
    items = list(core.missing_items_for(app, snapshot))
    if core.normalize_release_decision(snapshot.get("release_decision")) != "release":
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
    out["phase"] = core.current_phase(out)
    return out


def get_state(
    conn: sqlite3.Connection,
    *,
    user: dict,
    release_id_param: str = "",
) -> dict:
    """Build the full page-state payload.

    Mirrors server.py:state_payload exactly.
    """
    releases = core.list_releases(conn)
    release_ids = {r["id"] for r in releases}
    latest = releases[-1]["id"] if releases else ""
    requested = release_id_param or latest
    release_id = requested if requested in release_ids else latest

    apps = core.list_apps(conn)
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
        "release_schedule": core.list_release_schedule(conn),
    }
    if release_id:
        core.refresh_missing_items(conn, release_id)
        release = core.get_release(conn, release_id)
        payload["release"] = _serialize_release(release)
        payload["artifacts"] = [
            dict(row)
            for row in conn.execute(
                "SELECT kind, name, final, generated_at FROM artifacts WHERE release_id = ?",
                (release_id,),
            )
        ]
        payload["qa_log"] = core.get_qa_log(conn, release_id)
        if user["role"] in {"QA", "RM", "Owner", "Guest"}:
            payload["qa_audit_logs"] = core.release_qa_audit_logs(conn, release_id)
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
    app_id = core.add_new_app_request(
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
    """Save snapshot fields; optionally sync decision to later releases.

    Mirrors server.py:775-939 (the /api/apps/update handler) exactly.

    *fields* is the full parsed POST body (keys: snapshot, app, sync_decision).

    Returns the response dict: {snapshot, missing_items, qa_status} and
    optionally decision_sync when body.sync_decision is truthy and the
    release_decision actually changed.

    Ruling D: when release_decision changes, cicd_service.sync_decision_to_cicd
    is called INSIDE the same transaction to produce a pending CICD status-modify
    request.  The response includes «cicd_sync» when the decision changed.
    """
    body = fields  # alias for readability — mirrors server.py variable name

    conn_ref = conn  # kept for closure use below
    app = core.get_app(conn, app_id)
    release = core.get_release(conn, release_id)

    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定")

    snap_now = release["snapshots"].get(app_id, {})
    require_owner_or_rm_with_owners(snap_now.get("owners"), user, role)

    snap_update = body.get("snapshot", {})

    app_update = body.get("app", {}) if isinstance(body.get("app", {}), dict) else {}
    app_update_keys = set(app_update)
    app_cicd_keys = app_update_keys & CICD_APP_CONFIG_FIELDS
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

    past_doc_deadline = not core.is_before(release.get("doc_deadline", ""))
    if past_doc_deadline:
        if "app" in body or any(key != "release_decision" for key in snap_update):
            raise RuntimeError(
                "已过 doc deadline，只能下调 release 决策，不能再修改文档/表单/app_info"
            )

    current_decision = snap_now.get("release_decision", "release")
    new_decision = snap_update.get("release_decision")
    if new_decision is not None:
        new_decision_norm = core.normalize_release_decision(new_decision)
        if new_decision_norm != current_decision:
            if new_decision_norm == "release" and not core.is_before(
                release.get("app_freeze_deadline", "")
            ):
                raise RuntimeError("已过 app 冻结 deadline，不可再切换为 release")

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
            core.save_app(conn_ref, app)
            core.audit(
                conn_ref,
                "修改 Gerrit 信息",
                user=actor,
                role=role,
                app_id=aid,
                release_id=rid,
                event="update_app_repo",
                detail=core.field_diff(
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
            changes = core.field_diff(cicd_before, cicd_after, CICD_APP_CONFIG_LABELS)
            if changes:
                core.audit(
                    conn_ref,
                    "修改 App CICD 配置",
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
            decision = core.normalize_release_decision(snap_update["release_decision"])
            if decision not in core.RELEASE_DECISIONS:
                raise ValueError(f"Invalid release_decision: {snap_update['release_decision']}")
            if decision != snapshot.get("release_decision"):
                core.audit(
                    conn_ref,
                    (
                        f"修改 release 决策：{name_for_msg} "
                        f"{snapshot.get('release_decision')} -> {decision}"
                    ),
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_release_decision",
                    detail=core.field_diff(
                        {"release_decision": snapshot.get("release_decision")},
                        {"release_decision": decision},
                        {"release_decision": "release 决策"},
                    ),
                )
            snapshot["release_decision"] = decision

        meta_before: dict = {}
        meta_after: dict = {}
        for key in core.SNAPSHOT_META_FIELDS:
            if key not in snap_update:
                continue
            value = snap_update[key]
            if key == "doc_target":
                value = core.normalize_doc_target(value)
            elif key == "description":
                value = core.normalize_app_description(value)
            elif key == "owners":
                value = sorted(
                    {str(o).strip() for o in (value or []) if str(o).strip()}
                )
            else:
                value = (value or "").strip()
            if snapshot.get(key) == value:
                continue
            if key not in owner_meta and role != "RM":
                raise AuthzError(f"仅 RM 可修改{core.APP_META_LABELS.get(key, key)}")
            meta_before[key] = snapshot.get(key)
            meta_after[key] = value
            snapshot[key] = value

        if meta_after:
            core.audit(
                conn_ref,
                f"修改 app 基本信息：{name_for_msg}",
                user=actor,
                role=role,
                app_id=aid,
                release_id=rid,
                event="update_app_meta",
                detail=core.field_diff(meta_before, meta_after, core.APP_META_LABELS),
            )

        if "owner_confirmed" in snap_update:
            if role != "Owner":
                raise AuthzError("Owner confirmation must be submitted by an Owner")
            if snap_update["owner_confirmed"] and not snapshot.get("owner_confirmed"):
                core.audit(
                    conn_ref,
                    f"提交 Owner 确认：{name_for_msg}",
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
            doc_changes = core.field_diff(
                current_doc,
                doc_update,
                {k: doc_labels.get(k, k) for k in doc_update},
            )
            if doc_changes:
                core.audit(
                    conn_ref,
                    f"修改文档字段：{name_for_msg}",
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
            comm_changes = core.field_diff(comm_before, comm_update, comm_labels)
            if comm_changes:
                core.audit(
                    conn_ref,
                    f"修改社区发布信息：{name_for_msg}",
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
            sanity_changes = core.field_diff(sanity_before, sanity_update, sanity_labels)
            if sanity_changes:
                if role != "RM":
                    raise AuthzError("仅 RM 可修改 Sanity 信息")
                core.audit(
                    conn_ref,
                    f"修改 Sanity 信息：{name_for_msg}",
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
                    item.setdefault("id", core.new_id("testdoc"))
                    item.setdefault("path", f"owner_added.{len(by_id) + 1}")
                    snapshot.setdefault("test_docs", []).append(item)
            td_changes = core.test_docs_diff(before_docs, snapshot.get("test_docs", []))
            if td_changes:
                core.audit(
                    conn_ref,
                    f"修改测试说明：{name_for_msg}",
                    user=actor,
                    role=role,
                    app_id=aid,
                    release_id=rid,
                    event="update_test_docs",
                    detail=td_changes,
                )

    # Execute inside a single transaction (mirrors server.py:929-939)
    response: dict = {}
    with core.transaction(conn):
        update_app_if_needed()
        normalize_community_update(snap_now)
        updated = core.update_snapshot(conn, rid, aid, mutate, skip_doc_deadline=past_doc_deadline)
        updated["missing_items"] = _missing_items_for(app, updated)
        core.save_snapshot(conn, rid, aid, updated)
        response = {
            "snapshot": updated,
            "missing_items": updated.get("missing_items", []),
            "qa_status": updated.get("qa_status"),
        }
        new_decision = updated.get("release_decision", current_decision)
        if new_decision != current_decision:
            # Ruling D: unconditionally sync decision → CICD task status inside
            # the same transaction, so the sync request and the snapshot save are
            # atomic. Phase gate already ran above (raises before we get here).
            from app.services import cicd_service as _cicd_svc
            cicd_req = _cicd_svc.sync_decision_to_cicd(
                conn,
                aid,
                new_decision,
                submitter=actor,
                current_status_override=_cicd_svc._DECISION_TO_CICD_STATUS.get(
                    current_decision, ""
                ),
            )
            response["cicd_sync"] = {
                "created": cicd_req is not None,
                "request": cicd_req,
            }
        if body.get("sync_decision") and new_decision != current_decision:
            # R3: use the new app-layer gating rule (NOT core's). core stays frozen.
            response["decision_sync"] = sync_decision_to_later_releases(
                conn, rid, aid, new_decision, user=actor, role=role
            )
    return response


# ---------------------------------------------------------------------------
# Decision sync to later releases (R3 gating rule + dry-run preview)
# ---------------------------------------------------------------------------

def sync_decision_to_later_releases(
    conn: sqlite3.Connection,
    from_release_id: str,
    app_id: str,
    decision: str,
    *,
    user: str = "system",
    role: str = "system",
) -> dict:
    """Apply a release_decision to every later (by created_at) release.

    R3 reimplementation of ``core.sync_decision_to_later_releases`` with the
    changed gating rule (see ``app.domain.decision_sync``):
      - locked release → skipped (reason "已最终锁定")
      - app absent → skipped (reason "本 release 无此 app")
      - otherwise apply ``resolve_synced_decision(decision, phase)``: an upgrade
        to ``release`` on a release past app-freeze OR doc-deadline becomes
        ``cicd_only`` rather than being skipped.

    Response shape mirrors core ({"applied": [...], "skipped": [...]}) but each
    applied entry is extended with its ``resulting_decision``.
    """
    decision = core.normalize_release_decision(decision)
    result: dict[str, list] = {"applied": [], "skipped": []}
    releases = core.list_releases(conn)
    idx = next((i for i, r in enumerate(releases) if r["id"] == from_release_id), None)
    if idx is None:
        return result
    with core.transaction(conn):
        for r in releases[idx + 1:]:
            rid = r["id"]
            if r.get("released_locked"):
                result["skipped"].append(
                    {"release_id": rid, "release_name": r["name"], "reason": "已最终锁定"}
                )
                continue
            release = core.get_release(conn, rid)
            snapshot = release["snapshots"].get(app_id)
            if snapshot is None:
                result["skipped"].append(
                    {"release_id": rid, "release_name": r["name"], "reason": "本 release 无此 app"}
                )
                continue
            phase = core.current_phase(release)
            resulting = decision_sync_domain.resolve_synced_decision(decision, phase)
            if snapshot.get("release_decision") != resulting:
                snapshot["release_decision"] = resulting
                snapshot["missing_items"] = _missing_items_for(
                    core.get_app(conn, app_id), snapshot
                )
                core.save_snapshot(conn, rid, app_id, snapshot)
            result["applied"].append(
                {
                    "release_id": rid,
                    "release_name": r["name"],
                    "resulting_decision": resulting,
                }
            )
        if result["applied"]:
            core.audit(
                conn,
                f"同步 release 决策（{decision}）到 {len(result['applied'])} 个后续 release",
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

    Returns ``{"decision": <normalized>, "releases": [row, ...]}`` where each row
    is one later release: ``{release_id, release_name, phase_label,
    resulting_decision, skipped, reason?}``. ``resulting_decision`` is ``None``
    for skipped rows. Drives the owner-choice dialog table before applying.
    """
    decision = core.normalize_release_decision(decision)
    releases = core.list_releases(conn)
    idx = next((i for i, r in enumerate(releases) if r["id"] == release_id), None)
    rows: list[dict] = []
    if idx is None:
        return {"decision": decision, "releases": rows}
    for r in releases[idx + 1:]:
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
        release = core.get_release(conn, rid)
        phase = core.current_phase(release)
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
    return {"decision": decision, "releases": rows}


# ---------------------------------------------------------------------------
# App info apply / fetch (for POST /api/app-info, /fetch, /fetch-all)
# ---------------------------------------------------------------------------

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
    snapshot = core.apply_app_info(
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

    app = core.get_app(conn, app_id)
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
    snapshot = core.apply_app_info(
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
    release = core.get_release(conn, release_id)
    results = []
    for app_id in sorted(release.get("snapshots", {})):
        try:
            app = core.get_app(conn, app_id)
            fetch_url, fetch_branch, source = _app_info_fetch_target(app)
            raw, commit_id = gerrit_fetch(
                fetch_url,
                fetch_branch,
                project_root=_project_root,
                hpc_gerrit_prefix=settings.hpc_gerrit_prefix,
                hpc_gerrit_root=settings.hpc_gerrit_root,
            )
            snapshot = core.apply_app_info(
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


# ---------------------------------------------------------------------------
# Admin-only app deletion and snapshot transfer (for Wave 2 impl-3 / admin)
# ---------------------------------------------------------------------------

def delete_app(conn: sqlite3.Connection, app_id: str, *, user: str, role: str) -> None:
    """Delete an app globally (Admin-only system maintenance).

    Mirrors core.py:delete_app. Called by admin router.
    """
    core.delete_app(conn, app_id, user=user, role=role)


def transfer_owner(
    conn: sqlite3.Connection,
    app_id: str,
    release_id: str,
    *,
    new_owner: str,
    user: str,
) -> None:
    """Transfer app ownership within a release snapshot.

    # TODO Phase 2 — implement when admin/transfer-owner router is built.
    """
    raise NotImplementedError
