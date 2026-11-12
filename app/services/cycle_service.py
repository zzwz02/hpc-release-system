"""Monthly cycles: dates, environment, base images, signups and publication.

Business rules (monthly-release plan, section 二):

* A cycle starts empty; an App takes part only through an Owner signup.
* Signup requires the App to be CICD ``running`` with no open stop request
  (rules 1–2); the check and the insert share one transaction.  The CICD side
  refuses a stop request while the App has an active signup in an unpublished
  cycle (rule 3, see ``has_open_signup``).
* Each entry locks one app_info version.  Before the signup deadline entries
  follow every new version; afterwards they follow only versions that do not
  widen scope, and widening opens a QA scope request.
* Publication turns ``qa_passed`` / ``has_issues`` entries into official
  versions; other entries are marked failed and the App keeps its previous
  official version.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from app.api.errors import AuthzError
from app.db.connection import run_after_commit, transaction
from app.domain import cycles as cycles_domain
from app.domain import phases
from app.domain import qa as qa_domain
from app.domain.container_image import parse_base_image_reference
from app.domain.permissions import has_capability
from app.repositories import (
    app_info_repo,
    apps_repo,
    artifacts_repo,
    entries_repo,
    official_repo,
    releases_repo,
)
from app.repositories.audit_repo import log_audit
from app.repositories.base import new_id
from app.services import catalog_service
from app.timeutil import beijing_timestamp

ENTRY_FIELD_LABELS = {
    "base_image_id": "基础镜像",
    "target_version": "目标版本",
    "planned_features": "计划 feature",
    "excluded_tests": "去掉的测试项",
    "actual_delivery": "实际交付",
    "known_issues": "已知问题",
    "locked_version_id": "锁定的 app_info 版本",
    "status": "报名状态",
}


def _detail(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Audit detail rows ({field, label, old, new}) for changed entry fields."""
    return [
        {"field": key, "label": ENTRY_FIELD_LABELS.get(key, key), "old": old.get(key), "new": value}
        for key, value in new.items()
        if old.get(key) != value
    ]


# ---------------------------------------------------------------------------
# Cycle reads
# ---------------------------------------------------------------------------


def _with_phase(release: dict[str, Any]) -> dict[str, Any]:
    release["phase"] = phases.current_phase(release)
    return release


def get_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise KeyError(f"Unknown release: {release_id}")
    return _with_phase(release)


def list_releases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """All cycles, oldest first, each with phase and entry counts."""
    counts = {
        row["release_id"]: dict(row)
        for row in conn.execute(
            """
            SELECT release_id,
                   SUM(status = 'active') AS entry_count,
                   SUM(result = 'released') AS released_count
            FROM entries GROUP BY release_id
            """
        )
    }
    result = []
    for release in releases_repo.list_release_rows(conn):
        stats = counts.get(release["id"], {})
        release["entry_count"] = int(stats.get("entry_count") or 0)
        release["released_count"] = int(stats.get("released_count") or 0)
        result.append(_with_phase(release))
    return result


def release_detail(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    release = get_release(conn, release_id)
    release["environment"] = releases_repo.list_environment(conn, release_id)
    release["base_images"] = releases_repo.list_base_images(conn, release_id)
    return release


def _require_rm(role: str) -> None:
    if not has_capability(role, "release.manage"):
        raise AuthzError("RM role required")


def _require_open(release: dict[str, Any]) -> None:
    if release.get("released_locked"):
        raise RuntimeError(f"版本「{release['name']}」已发布，只读")


# ---------------------------------------------------------------------------
# Cycle writes (RM)
# ---------------------------------------------------------------------------


def create_cycle(
    conn: sqlite3.Connection,
    *,
    name: str,
    signup_deadline: str,
    qa_deadline: str,
    release_date: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Create an empty cycle.  The environment rows of the previous cycle are
    copied as a starting point for RM to edit; nothing else is inherited."""
    _require_rm(role)
    name = (name or "").strip()
    if not name:
        raise ValueError("版本名不能为空")
    if releases_repo.name_exists(conn, name):
        raise ValueError(f"版本名「{name}」已存在")
    dates = cycles_domain.normalize_cycle_dates(signup_deadline, qa_deadline, release_date)
    previous = releases_repo.list_release_rows(conn)
    ts = beijing_timestamp()
    release_id = new_id("rel")
    with transaction(conn):
        releases_repo.insert_release(
            conn,
            {"id": release_id, "name": name, **dates, "created_at": ts, "created_by": user},
        )
        if previous:
            rows = releases_repo.list_environment(conn, previous[-1]["id"])
            releases_repo.replace_environment(conn, release_id, rows)
        log_audit(
            conn,
            f"新建版本「{name}」：报名截止 {dates['signup_deadline']}，QA 截止 {dates['qa_deadline']}，"
            f"发布日 {dates['release_date']}",
            ts=ts,
            user=user,
            role=role,
            release_id=release_id,
            event="create_release",
        )
    return {"release_id": release_id}


def update_cycle(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    name: str,
    signup_deadline: str,
    qa_deadline: str,
    release_date: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    _require_rm(role)
    release = get_release(conn, release_id)
    _require_open(release)
    name = (name or "").strip()
    if not name:
        raise ValueError("版本名不能为空")
    if releases_repo.name_exists(conn, name, exclude_id=release_id):
        raise ValueError(f"版本名「{name}」已存在")
    dates = cycles_domain.normalize_cycle_dates(signup_deadline, qa_deadline, release_date)
    with transaction(conn):
        releases_repo.update_settings(conn, release_id, name=name, **dates)
        log_audit(
            conn,
            f"修改版本设置：{release['name']} -> {name}，报名截止 {dates['signup_deadline']}，"
            f"QA 截止 {dates['qa_deadline']}，发布日 {dates['release_date']}",
            ts=beijing_timestamp(),
            user=user,
            role=role,
            release_id=release_id,
            event="update_release_settings",
        )
    return release_detail(conn, release_id)


def save_environment(
    conn: sqlite3.Connection,
    release_id: str,
    rows: list[dict[str, Any]],
    *,
    user: str,
    role: str,
) -> list[dict[str, Any]]:
    """Replace the cycle environment (only while signup is open)."""
    _require_rm(role)
    release = get_release(conn, release_id)
    phases.require_can(release, "edit_environment", "报名已截止，当月环境已冻结")
    clean = []
    for row in rows:
        component = str(row.get("component") or "").strip()
        if not component:
            continue
        clean.append(
            {
                "component": component,
                "version": str(row.get("version") or "").strip(),
                "note": str(row.get("note") or "").strip(),
            }
        )
    with transaction(conn):
        releases_repo.replace_environment(conn, release_id, clean)
        log_audit(
            conn,
            "更新当月环境：" + "，".join(f"{r['component']} {r['version']}".strip() for r in clean),
            ts=beijing_timestamp(),
            user=user,
            role=role,
            release_id=release_id,
            event="update_environment",
            detail=clean,
        )
    return releases_repo.list_environment(conn, release_id)


def _clean_image(image: dict[str, Any]) -> dict[str, str]:
    if "reference" in image:
        return {**parse_base_image_reference(image["reference"]), "note": str(image.get("note") or "").strip()}
    clean = {key: str(image.get(key) or "").strip() for key in ("name", "image", "digest", "note")}
    if not clean["name"]:
        raise ValueError("基础镜像名称不能为空")
    return clean


def save_base_image(
    conn: sqlite3.Connection,
    release_id: str,
    image: dict[str, Any],
    *,
    user: str,
    role: str,
) -> list[dict[str, Any]]:
    """Add (no ``id``) or update a base image; allowed until publication."""
    _require_rm(role)
    clean = _clean_image(image)
    ts = beijing_timestamp()
    with transaction(conn):
        _require_open(get_release(conn, release_id))
        if image.get("id"):
            current = releases_repo.get_base_image(conn, int(image["id"]))
            if not current or current["release_id"] != release_id:
                raise KeyError(f"Unknown base image: {image['id']}")
            releases_repo.update_base_image(conn, int(image["id"]), clean)
            message = f"修改基础镜像「{clean['name']}」"
        else:
            releases_repo.insert_base_image(conn, release_id, clean)
            message = f"新增基础镜像「{clean['name']}」"
        log_audit(conn, message, ts=ts, user=user, role=role, release_id=release_id,
                  event="base_image", detail=clean)
    return releases_repo.list_base_images(conn, release_id)


def delete_base_image(
    conn: sqlite3.Connection,
    release_id: str,
    image_id: int,
    *,
    user: str,
    role: str,
) -> list[dict[str, Any]]:
    _require_rm(role)
    release = get_release(conn, release_id)
    _require_open(release)
    current = releases_repo.get_base_image(conn, image_id)
    if not current or current["release_id"] != release_id:
        raise KeyError(f"Unknown base image: {image_id}")
    used = conn.execute(
        "SELECT COUNT(*) FROM entries WHERE base_image_id = ? AND status = 'active'", (image_id,)
    ).fetchone()[0]
    if used:
        raise RuntimeError(f"有 {used} 个报名选择了该基础镜像，不能删除")
    with transaction(conn):
        releases_repo.delete_base_image(conn, image_id)
        log_audit(conn, f"删除基础镜像「{current['name']}」", ts=beijing_timestamp(), user=user,
                  role=role, release_id=release_id, event="base_image")
    return releases_repo.list_base_images(conn, release_id)


# ---------------------------------------------------------------------------
# Entries: reads
# ---------------------------------------------------------------------------


def entry_view(
    conn: sqlite3.Connection,
    entry: dict[str, Any],
    *,
    latest_version_id: int | None = None,
) -> dict[str, Any]:
    """Entry with its locked app_info summary and effective test scope."""
    view = dict(entry)
    locked = app_info_repo.get_version(conn, entry.get("locked_version_id"))
    summary = catalog_service.version_summary(locked)
    excluded = set(entry.get("excluded_tests") or [])
    view["locked_app_info"] = summary
    view["scope_tests"] = [
        {**test, "excluded": test["path"] in excluded} for test in (summary or {}).get("tests", [])
    ]
    view["pending_scope_request"] = entries_repo.pending_scope_request(conn, entry["id"])
    if latest_version_id is None:
        latest = app_info_repo.latest_version(conn, entry["app_id"])
        latest_version_id = latest["id"] if latest else None
    view["behind_latest"] = bool(
        latest_version_id and entry.get("locked_version_id") != latest_version_id
    )
    return view


def list_entry_views(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    include_withdrawn: bool = True,
) -> list[dict[str, Any]]:
    latest_ids = app_info_repo.latest_version_ids(conn)
    return [
        entry_view(conn, entry, latest_version_id=latest_ids.get(entry["app_id"]))
        for entry in entries_repo.list_for_release(conn, release_id, include_withdrawn=include_withdrawn)
    ]


def _get_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any]:
    entry = entries_repo.get_entry(conn, entry_id)
    if not entry:
        raise KeyError(f"Unknown entry: {entry_id}")
    return entry


def has_open_signup(conn: sqlite3.Connection, app_id: str) -> list[str]:
    """Names of unpublished cycles where *app_id* has an active signup (rule 3)."""
    names = []
    for entry in entries_repo.open_active_entries_for_app(conn, app_id):
        release = releases_repo.get_release_row(conn, entry["release_id"])
        names.append(release["name"] if release else entry["release_id"])
    return names


# ---------------------------------------------------------------------------
# Entries: Owner actions
# ---------------------------------------------------------------------------


def _validate_base_image(conn: sqlite3.Connection, release_id: str, base_image_id: Any) -> int | None:
    if base_image_id in (None, "", 0):
        return None
    image = releases_repo.get_base_image(conn, int(base_image_id))
    if not image or image["release_id"] != release_id:
        raise ValueError("所选基础镜像不属于该版本")
    return int(image["id"])


def _validate_excluded(summary: dict[str, Any] | None, excluded: list[str]) -> list[str]:
    paths = {test["path"] for test in (summary or {}).get("tests", [])}
    unknown = [path for path in excluded if path not in paths]
    if unknown:
        raise ValueError("未知的测试项：" + "、".join(unknown))
    return sorted(set(excluded))


def signup_batch(
    conn: sqlite3.Connection,
    release_id: str,
    app_ids: list[str],
    *,
    user: str,
    role: str,
) -> list[dict[str, Any]]:
    """Sign up owned Apps together, using each latest app_info's defaults."""
    if not isinstance(app_ids, list) or not app_ids or any(
        not isinstance(app_id, str) or not app_id.strip() for app_id in app_ids
    ):
        raise ValueError("请选择要报名的 App")
    entries = []
    with transaction(conn):
        phases.require_can(get_release(conn, release_id), "signup", "报名已截止，不能新增报名")
        for app_id in dict.fromkeys(app_ids):
            app = apps_repo.get_app(conn, app_id)
            if not app:
                raise ValueError("所选 App 已不存在，请刷新列表")
            catalog_service.require_app_editor(app, user, role)
            if not catalog_service.is_owner(app, user):
                raise AuthzError("批量报名只能选择自己负责的 App")
            latest = catalog_service.version_summary(app_info_repo.latest_version(conn, app_id))
            if not latest or not latest["app_version"].strip():
                raise RuntimeError(f"{catalog_service.app_name(app)}：请先同步包含 app_version 的 app_info")
            try:
                entries.append(signup(
                    conn, release_id, app_id, base_image_id=None,
                    target_version=latest["app_version"], planned_features="", excluded_tests=[],
                    user=user, role=role,
                ))
            except (ValueError, RuntimeError) as exc:
                raise RuntimeError(f"{catalog_service.app_name(app)}：{exc}") from exc
    return entries


def signup(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    *,
    base_image_id: Any,
    target_version: str,
    planned_features: str,
    excluded_tests: list[str],
    user: str,
    role: str,
) -> dict[str, Any]:
    """Sign *app_id* up for the cycle (or re-sign a withdrawn entry)."""
    from app.services import cicd_service

    release = get_release(conn, release_id)
    phases.require_can(release, "signup", "报名已截止，不能新增报名")
    app = catalog_service.get_app_or_raise(conn, app_id)
    catalog_service.require_app_editor(app, user, role)
    if not (target_version or "").strip():
        raise ValueError("请填写目标版本")
    latest = app_info_repo.latest_version(conn, app_id)
    if not latest:
        raise RuntimeError("该 App 还没有 app_info，请先在 App 工作台同步 app_info")
    summary = catalog_service.version_summary(latest)
    excluded = _validate_excluded(summary, list(excluded_tests or []))
    ts = beijing_timestamp()
    with transaction(conn):
        app = catalog_service.get_app_or_raise(conn, app_id)
        if app["cicd_status"] != cycles_domain.CICD_RUNNING:
            raise RuntimeError("该 App 的 CICD 已停止，需先申请启动 CICD 并生效后才能报名")
        stop_request = cicd_service.open_status_request(conn, app_id)
        if stop_request and stop_request["target_status"] == cycles_domain.CICD_STOPPED:
            raise RuntimeError(f"该 App 有未处理完的停止 CICD 申请 #{stop_request['id']}，不能报名")
        fields = {
            "base_image_id": _validate_base_image(conn, release_id, base_image_id),
            "target_version": target_version.strip(),
            "planned_features": (planned_features or "").strip(),
            "excluded_tests": excluded,
            "locked_version_id": latest["id"],
        }
        existing = entries_repo.find_entry(conn, release_id, app_id)
        if existing and existing["status"] == cycles_domain.ENTRY_ACTIVE:
            raise RuntimeError("该 App 已报名本版本")
        if existing:
            entries_repo.update_entry(
                conn,
                existing["id"],
                {
                    **fields,
                    "status": cycles_domain.ENTRY_ACTIVE,
                    "actual_delivery": "",
                    "known_issues": "",
                    "qa_status": qa_domain.QA_STATUS_DEFAULT,
                    "qa_note": "",
                    "deliverable_image": "",
                    "deliverable_digest": "",
                    "deliverable_commit": "",
                    "result": "",
                },
                updated_at=ts,
                updated_by=user,
            )
            entry_id = existing["id"]
        else:
            entry_id = entries_repo.insert_entry(
                conn,
                {"release_id": release_id, "app_id": app_id, **fields, "created_at": ts, "created_by": user},
            )
        log_audit(
            conn,
            f"报名「{release['name']}」：目标版本 {fields['target_version']}",
            ts=ts,
            user=user,
            role=role,
            app_id=app_id,
            release_id=release_id,
            event="signup",
            detail=_detail({}, fields),
        )
    return entry_view(conn, _get_entry(conn, entry_id))


def update_entry(
    conn: sqlite3.Connection,
    entry_id: int,
    changes: dict[str, Any],
    *,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Owner/RM edits to an entry, gated by phase.

    * Signup phase: base image, target version, planned features, scope.
    * After the deadline: only removing tests from scope.
    * Until publication: actual delivery and known issues.
    """
    entry = _get_entry(conn, entry_id)
    if entry["status"] != cycles_domain.ENTRY_ACTIVE:
        raise RuntimeError("该报名已撤回")
    release = get_release(conn, entry["release_id"])
    _require_open(release)
    app = catalog_service.get_app_or_raise(conn, entry["app_id"])
    catalog_service.require_app_editor(app, user, role)
    allowed = {"base_image_id", "target_version", "planned_features", "excluded_tests",
               "actual_delivery", "known_issues"}
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"不支持的字段：{', '.join(sorted(unknown))}")
    update: dict[str, Any] = {}
    plan_keys = {"base_image_id", "target_version", "planned_features"} & set(changes)
    if plan_keys:
        phases.require_can(release, "edit_entry_plan", "报名已截止，基础镜像、目标版本和计划 feature 不能再修改")
        if "base_image_id" in changes:
            update["base_image_id"] = _validate_base_image(conn, release["id"], changes["base_image_id"])
        if "target_version" in changes:
            if not str(changes["target_version"] or "").strip():
                raise ValueError("请填写目标版本")
            update["target_version"] = str(changes["target_version"]).strip()
        if "planned_features" in changes:
            update["planned_features"] = str(changes["planned_features"] or "").strip()
    if "excluded_tests" in changes:
        summary = catalog_service.version_summary(app_info_repo.get_version(conn, entry["locked_version_id"]))
        new_excluded = set(_validate_excluded(summary, list(changes["excluded_tests"] or [])))
        old_excluded = set(entry["excluded_tests"])
        if new_excluded - old_excluded:
            phases.require_can(release, "exclude_tests", "当前阶段不能修改测试范围")
        if old_excluded - new_excluded:
            phases.require_can(release, "include_tests", "报名已截止，测试范围只能删减，不能恢复已去掉的测试项")
        update["excluded_tests"] = sorted(new_excluded)
    for key in ("actual_delivery", "known_issues"):
        if key in changes:
            phases.require_can(release, "edit_deliverable", "当前阶段不能修改交付内容")
            update[key] = str(changes[key] or "").strip()
    if not update:
        return entry_view(conn, entry)
    ts = beijing_timestamp()
    with transaction(conn):
        entries_repo.update_entry(conn, entry_id, update, updated_at=ts, updated_by=user)
        log_audit(
            conn,
            "修改报名：" + "、".join(sorted(update)),
            ts=ts,
            user=user,
            role=role,
            app_id=entry["app_id"],
            release_id=entry["release_id"],
            event="update_entry",
            detail=_detail(entry, update),
        )
    return entry_view(conn, _get_entry(conn, entry_id))


def withdraw(
    conn: sqlite3.Connection,
    entry_id: int,
    *,
    user: str,
    role: str,
) -> dict[str, Any]:
    entry = _get_entry(conn, entry_id)
    if entry["status"] != cycles_domain.ENTRY_ACTIVE:
        raise RuntimeError("该报名已撤回")
    release = get_release(conn, entry["release_id"])
    phases.require_can(release, "withdraw", "版本已发布，不能撤回报名")
    app = catalog_service.get_app_or_raise(conn, entry["app_id"])
    catalog_service.require_app_editor(app, user, role)
    ts = beijing_timestamp()
    with transaction(conn):
        entries_repo.update_entry(conn, entry_id, {"status": cycles_domain.ENTRY_WITHDRAWN},
                                  updated_at=ts, updated_by=user)
        pending = entries_repo.pending_scope_request(conn, entry_id)
        if pending:
            entries_repo.close_scope_request(conn, pending["id"], status=cycles_domain.SCOPE_CLOSED,
                                             decided_by=user, decided_at=ts, note="报名已撤回")
        log_audit(conn, f"撤回「{release['name']}」的报名", ts=ts, user=user, role=role,
                  app_id=entry["app_id"], release_id=entry["release_id"], event="withdraw")
    return entry_view(conn, _get_entry(conn, entry_id))


# ---------------------------------------------------------------------------
# Scope following and scope requests
# ---------------------------------------------------------------------------


def _scope_request_message(conn: sqlite3.Connection, entry: dict[str, Any], additions: list[str]) -> str:
    app = apps_repo.get_app(conn, entry["app_id"]) or {"id": entry["app_id"]}
    release = releases_repo.get_release_row(conn, entry["release_id"]) or {"name": entry["release_id"]}
    from app import runtime_config

    base = runtime_config.section("site").get("PUBLIC_BASE_URL", "").rstrip("/")
    link = f"\n处理入口：{base}/qa?release_id={entry['release_id']}" if base else ""
    return (
        f"【测试范围扩大申请】{catalog_service.app_name(app)}（{release['name']}）"
        f"在报名截止后更新了 app_info，扩大了测试范围：\n- " + "\n- ".join(additions) + link
    )


def on_app_info_version(conn: sqlite3.Connection, app_id: str, version_id: int) -> None:
    """React to a new app_info version of *app_id* (inside the caller's transaction)."""
    new_parsed = catalog_service.parse_version(app_info_repo.get_version(conn, version_id)) or {}
    ts = beijing_timestamp()
    notices: list[str] = []
    for entry in entries_repo.open_active_entries_for_app(conn, app_id):
        release = get_release(conn, entry["release_id"])
        locked = app_info_repo.get_version(conn, entry["locked_version_id"])
        decision, additions = cycles_domain.follow_decision(
            before_signup_deadline=release["phase"] == phases.SIGNUP,
            locked_parsed=catalog_service.parse_version(locked),
            new_parsed=new_parsed,
            excluded=entry["excluded_tests"],
        )
        pending = entries_repo.pending_scope_request(conn, entry["id"])
        if decision == "follow":
            new_paths = {test["path"] for test in new_parsed.get("tests", [])}
            entries_repo.update_entry(
                conn,
                entry["id"],
                {
                    "locked_version_id": version_id,
                    "excluded_tests": [p for p in entry["excluded_tests"] if p in new_paths],
                },
                updated_at=ts,
                updated_by="system",
            )
            if pending:
                entries_repo.close_scope_request(
                    conn, pending["id"], status=cycles_domain.SCOPE_CLOSED, decided_by="system",
                    decided_at=ts, note="新的 app_info 不再扩大测试范围，申请自动关闭",
                )
            log_audit(conn, "报名条目跟随新的 app_info", ts=ts, app_id=app_id,
                      release_id=entry["release_id"], event="entry_follow_app_info",
                      detail={"from_version": entry["locked_version_id"], "to_version": version_id})
            continue
        if pending and pending["additions"] == additions:
            entries_repo.close_scope_request(
                conn, pending["id"], status=cycles_domain.SCOPE_SUPERSEDED, decided_by="system",
                decided_at=ts, note=f"被 app_info 版本 {version_id} 取代",
            )
            entries_repo.insert_scope_request(conn, entry_id=entry["id"], base_version_id=entry["locked_version_id"],
                                              target_version_id=version_id, additions=additions, created_at=ts)
            continue  # same widening already waiting for QA; no new notice
        rejected = entries_repo.last_rejected_request(conn, entry["id"], entry["locked_version_id"])
        if rejected and rejected["additions"] == additions:
            if pending:
                entries_repo.close_scope_request(
                    conn, pending["id"], status=cycles_domain.SCOPE_SUPERSEDED, decided_by="system",
                    decided_at=ts, note=f"被 app_info 版本 {version_id} 取代",
                )
            continue  # QA already rejected exactly this widening
        if pending:
            entries_repo.close_scope_request(
                conn, pending["id"], status=cycles_domain.SCOPE_SUPERSEDED, decided_by="system",
                decided_at=ts, note=f"被 app_info 版本 {version_id} 取代",
            )
        entries_repo.insert_scope_request(conn, entry_id=entry["id"], base_version_id=entry["locked_version_id"],
                                          target_version_id=version_id, additions=additions, created_at=ts)
        log_audit(conn, "app_info 扩大测试范围，已提交 QA 审批：" + "；".join(additions), ts=ts,
                  app_id=app_id, release_id=entry["release_id"], event="scope_request")
        notices.append(_scope_request_message(conn, entry, additions))
    if notices:
        from app.services import notification_channels

        for message in notices:
            run_after_commit(conn, lambda m=message: notification_channels.notify(conn, "scope_request", m))


def decide_scope_request(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    approve: bool,
    note: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    if not has_capability(role, "app.app_info.expand_qa_scope"):
        raise AuthzError("只有 QA 可以审批测试范围扩大")
    request = entries_repo.get_scope_request(conn, request_id)
    if not request:
        raise KeyError(f"Unknown scope request: {request_id}")
    if request["status"] != cycles_domain.SCOPE_PENDING:
        raise RuntimeError("该申请已处理")
    entry = _get_entry(conn, request["entry_id"])
    release = get_release(conn, entry["release_id"])
    phases.require_can(release, "decide_scope_request", "当前阶段不能审批测试范围扩大")
    if not approve and not (note or "").strip():
        raise ValueError("拒绝必须填写理由")
    ts = beijing_timestamp()
    status = cycles_domain.SCOPE_APPROVED if approve else cycles_domain.SCOPE_REJECTED
    with transaction(conn):
        entries_repo.close_scope_request(conn, request_id, status=status, decided_by=user,
                                         decided_at=ts, note=(note or "").strip())
        if approve:
            new_parsed = catalog_service.parse_version(
                app_info_repo.get_version(conn, request["target_version_id"])) or {}
            new_paths = {test["path"] for test in new_parsed.get("tests", [])}
            entries_repo.update_entry(
                conn,
                entry["id"],
                {
                    "locked_version_id": request["target_version_id"],
                    "excluded_tests": [p for p in entry["excluded_tests"] if p in new_paths],
                },
                updated_at=ts,
                updated_by=user,
            )
        log_audit(
            conn,
            f"{'同意' if approve else '拒绝'}测试范围扩大：" + "；".join(request["additions"]),
            ts=ts,
            user=user,
            role=role,
            app_id=entry["app_id"],
            release_id=entry["release_id"],
            event="scope_decision",
            detail={"request_id": request_id, "note": note},
        )
    from app.services import notification_channels

    app = apps_repo.get_app(conn, entry["app_id"]) or {"id": entry["app_id"]}
    notification_channels.notify(
        conn,
        "scope_decision",
        f"【范围扩大审批结果】{catalog_service.app_name(app)}（{release['name']}）："
        f"QA {user} {'同意' if approve else '拒绝'}了测试范围扩大"
        + (f"，理由：{note.strip()}" if (note or "").strip() else ""),
    )
    return entries_repo.get_scope_request(conn, request_id)


def list_scope_requests(conn: sqlite3.Connection, release_id: str) -> list[dict[str, Any]]:
    rows = entries_repo.list_scope_requests(conn, release_id)
    names = {app["id"]: catalog_service.app_name(app) for app in apps_repo.list_apps(conn)}
    for row in rows:
        row["app_name"] = names.get(row["app_id"], row["app_id"])
    return rows


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


def publish(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Record results, write official versions, lock, generate final docs."""
    from app.services import artifact_service

    _require_rm(role)
    release = get_release(conn, release_id)
    phases.require_can(release, "publish", "QA 截止后才能发布")
    ts = beijing_timestamp()
    released, failed = [], []
    with transaction(conn):
        for entry in entries_repo.list_for_release(conn, release_id):
            result = cycles_domain.publish_result(entry["qa_status"])
            known = cycles_domain.known_issues_at_publish(entry["known_issues"], entry["qa_status"], entry["qa_note"])
            entries_repo.update_entry(conn, entry["id"], {"result": result, "known_issues": known},
                                      updated_at=ts, updated_by=user)
            if result == cycles_domain.RESULT_RELEASED:
                official_repo.insert_official(
                    conn,
                    {
                        "app_id": entry["app_id"],
                        "release_id": release_id,
                        "entry_id": entry["id"],
                        "version": entry["target_version"],
                        "app_info_version_id": entry["locked_version_id"],
                        "image": entry["deliverable_image"],
                        "digest": entry["deliverable_digest"],
                        "released_at": ts,
                    },
                )
                released.append(entry["app_id"])
            else:
                failed.append(entry["app_id"])
        for pending in entries_repo.list_scope_requests(conn, release_id):
            if pending["status"] == cycles_domain.SCOPE_PENDING:
                entries_repo.close_scope_request(conn, pending["id"], status=cycles_domain.SCOPE_CLOSED,
                                                 decided_by=user, decided_at=ts, note="版本已发布")
        releases_repo.lock_release(conn, release_id, locked_at=ts, locked_by=user)
        artifacts = artifact_service.generate_final_artifacts(conn, release_id)
        log_audit(
            conn,
            f"发布「{release['name']}」：发布 {len(released)} 个，发布失败 {len(failed)} 个",
            ts=ts,
            user=user,
            role=role,
            release_id=release_id,
            event="publish",
            detail={"released": released, "failed": failed},
        )
    return {"released": released, "failed": failed, "artifacts": artifacts}


def unlock(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    reason: str,
    user: str,
    role: str,
) -> dict[str, Any]:
    """Reopen a published cycle for corrections (audited).

    Results and the cycle's official versions are withdrawn; publishing again
    recomputes them.
    """
    _require_rm(role)
    release = get_release(conn, release_id)
    phases.require_can(release, "unlock", "版本未发布，无需解锁")
    if not (reason or "").strip():
        raise ValueError("解锁必须填写原因")
    ts = beijing_timestamp()
    with transaction(conn):
        for entry in entries_repo.list_for_release(conn, release_id):
            entries_repo.update_entry(conn, entry["id"], {"result": ""}, updated_at=ts, updated_by=user)
        official_repo.withdraw_for_release(conn, release_id)
        artifacts_repo.delete_final_artifacts(conn, release_id)
        releases_repo.unlock_release(conn, release_id)
        log_audit(conn, f"解锁「{release['name']}」以更正：{reason.strip()}", ts=ts, user=user,
                  role=role, release_id=release_id, event="unlock")
    return {"ok": True}
