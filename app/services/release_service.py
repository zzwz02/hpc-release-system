"""Release lifecycle service — import, create, lock, deadlines, schedule.

Port of the release-related handlers in server.py (lines 669-722, 995-1018)
and the schedule handlers, with the business logic from core.py fully ported
onto the app repositories.  Final lock goes through artifact_service so
current FastAPI artifact rules apply.

Services take conn: sqlite3.Connection first, pure (no HTTP), own transactions.
All timestamps are naive Beijing strings (§5.4).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db.connection import transaction
from app.domain import import_csv, phases
from app.domain.audit_diff import field_diff
from app.domain.snapshots import base_snapshot, variant_app_id
from app.domain.textutil import join_list, normalize_name, order_chips, split_list
from app.repositories import (
    apps_repo,
    artifacts_repo,
    releases_repo,
    schedule_repo,
    snapshots_repo,
)
from app.repositories.audit_repo import log_audit
from app.repositories.base import new_id
from app.services import release_reads
from app.timeutil import beijing_timestamp, normalize_deadline, validate_deadline_order

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_release(release: dict) -> dict:
    """Add `released_locked` (bool) and `phase` to a release dict.

    Mirrors server.py:_serialize_release (server.py:1384-1388).
    """
    out = dict(release)
    out["released_locked"] = bool(out.get("released_locked"))
    out["phase"] = phases.current_phase(out)
    return out


# ---------------------------------------------------------------------------
# Import / create
# ---------------------------------------------------------------------------

def import_initial_rows(
    conn: sqlite3.Connection,
    rows: list[dict[str, str]],
    *,
    release_name: str | None = None,
    maca_version: str | None = None,
    app_freeze_deadline: str = "",
    doc_deadline: str = "",
) -> str:
    """Import a single init CSV: one app per (git_url, git_branch) pair.

    Supports the legacy release CSV columns plus the rich hpc_app.csv shape:
    类别, id, 名称, Owner, 类型, 描述, git_url, git_branch, 对应官方版本,
    X86支持芯片系列, ARM支持芯片类型, 开发者社区发布*, *sanity.
    Rows sharing a (git_url, git_branch) pair form one app; rows without a
    git repo (e.g. 停止发布 entries) are skipped rather than failing import.

    Mirrors core.py:import_initial_rows.
    """
    validate_deadline_order(app_freeze_deadline, doc_deadline)
    if not rows:
        raise ValueError("初始化 CSV 为空")
    if conn.execute("SELECT 1 FROM releases LIMIT 1").fetchone():
        raise RuntimeError("已存在 release，初始化导入只能在空库执行；如需开启新一轮，请使用「克隆 release」")

    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = ((row.get("git_url") or "").strip(), (row.get("git_branch") or "").strip())
        if not key[0] or not key[1]:
            # no repo -> nothing buildable; skip instead of aborting import
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    if not order:
        raise ValueError("初始化 CSV 没有可导入的行（每行需要 git_url 和 git_branch）")

    base_counts: dict[str, int] = {}
    for key in order:
        bid = normalize_name(import_csv.init_csv_official_name(groups[key][0])) or "app"
        base_counts[bid] = base_counts.get(bid, 0) + 1

    used_ids: set[str] = set()
    built: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key in order:
        group = groups[key]
        first = group[0]
        official = import_csv.init_csv_official_name(first)
        bid = normalize_name(official) or "app"
        if base_counts[bid] > 1 or bid in used_ids:
            app_id = variant_app_id(bid, first.get("app_version", ""), key[1], used_ids)
        else:
            app_id = bid
        used_ids.add(app_id)

        x86: list[str] = []
        arm: list[str] = []
        hpcc: list[str] = []
        owners: list[str] = []
        for r in group:
            # new hpc_app.csv carries explicit X86 / ARM chip columns;
            # the legacy release CSV splits maca_chip by the arch column.
            x86.extend(split_list(import_csv.csv_value(r, "X86支持芯片系列")))
            arm.extend(split_list(import_csv.csv_value(r, "ARM支持芯片类型")))
            arch = (r.get("arch") or "").lower()
            chips = split_list(r.get("maca_chip"))
            (arm if "arm" in arch or "aarch" in arch else x86).extend(chips)
            hpcc.extend(split_list(r.get("hpcc_chip")))
            owners.extend(split_list(r.get("Owner")))

        app = {
            "id": app_id,
            "git_url": key[0],
            "git_branch": key[1],
            "aliases": sorted({official} if official else set()),
            "created_by": "import",
            "created_at": beijing_timestamp(),
        }
        snapshot = base_snapshot(
            app_id,
            official_name=official,
            app_type=import_csv.init_csv_app_type(first),
            description=import_csv.csv_value(first, "描述"),
            doc_target=import_csv.infer_doc_target(
                import_csv.init_csv_doc_category(first), import_csv.init_csv_app_type(first)
            ),
            owners=sorted(set(owners)),
        )
        snapshot["version"] = import_csv.csv_value(first, "对应官方版本", "app_version")
        snapshot["x86_chips"] = ",".join(order_chips(x86))
        snapshot["arm_chips"] = ",".join(order_chips(arm))
        snapshot["hpcc_chip"] = join_list(hpcc)
        snapshot["arch"] = join_list((r.get("arch") or "") for r in group)
        snapshot["maca_version"] = (first.get("maca_version") or "").strip()
        snapshot["community"] = {
            "release_status": import_csv.csv_value(first, "开发者社区发布情况"),
            "python_version": import_csv.csv_value(first, "开发者社区发布包支持python版本"),
            "framework_version": import_csv.csv_value(first, "开发者社区发布包支持的底层框架及版本"),
        }
        snapshot["sanity"] = {
            "arm_kylin": import_csv.csv_checkmark(import_csv.csv_value(first, "ARM / Kylin sanity")),
            "ubuntu": import_csv.csv_checkmark(
                import_csv.csv_value(first, "Ubuntu sanity / 兼容性sanity", "Ubuntu / 兼容性 sanity")
            ),
        }
        built.append((app, snapshot))

    release_id = new_id("rel")
    csv_maca = (rows[0].get("maca_version") or "").strip()
    ts = beijing_timestamp()
    with transaction(conn):
        for app, _ in built:
            apps_repo.save_app(conn, app)
            log_audit(conn, f"导入 app：{app['id']}", ts=ts, user="import", role="system",
                      app_id=app["id"], release_id=release_id, event="create_app")

        releases_repo.save_release(conn, {
            "id": release_id,
            "name": release_name or maca_version or csv_maca or "initial-release",
            "maca_version": maca_version or csv_maca,
            "app_freeze_deadline": normalize_deadline(app_freeze_deadline),
            "doc_deadline": normalize_deadline(doc_deadline),
            "released_locked": 0,
            "released_locked_at": "",
            "released_locked_by": "",
            "created_at": ts,
            "source": "initial_csv",
            "cloned_from": "",
        })
        for app, snapshot in built:
            snapshots_repo.save_snapshot(conn, release_id, app["id"], snapshot)
        log_audit(conn, f"首次初始化导入完成：{len(built)} 个 app", ts=ts,
                  release_id=release_id, event="import_initial")
    return release_id


def import_initial(
    conn: sqlite3.Connection,
    *,
    csv: str,
    release_name: str | None,
    maca_version: str | None,
    app_freeze_deadline: str,
    doc_deadline: str,
) -> dict:
    """Import a CSV to bootstrap the first release.

    Returns {"release_id": ...}.
    Mirrors server.py:669-682.
    """
    release_id = import_initial_rows(
        conn,
        import_csv.parse_csv_text(csv),
        release_name=release_name or None,
        maca_version=maca_version or None,
        app_freeze_deadline=app_freeze_deadline,
        doc_deadline=doc_deadline,
    )
    return {"release_id": release_id}


def create_release(
    conn: sqlite3.Connection,
    *,
    name: str,
    maca_version: str,
    app_freeze_deadline: str,
    doc_deadline: str,
    user: str,
    role: str,
) -> dict:
    """Clone the most recent release into a new one.

    Returns {"release_id": ...}.
    Mirrors server.py:682-695 and core.py:create_release_from_previous.
    """
    if not (name or "").strip():
        raise ValueError("新 Release 名称不能为空")
    validate_deadline_order(app_freeze_deadline, doc_deadline)
    releases = releases_repo.list_release_rows(conn)
    previous = release_reads.get_release(conn, releases[-1]["id"]) if releases else None
    release_id = new_id("rel")
    ts = beijing_timestamp()
    release = {
        "id": release_id,
        "name": name,
        "maca_version": maca_version,
        "app_freeze_deadline": normalize_deadline(app_freeze_deadline),
        "doc_deadline": normalize_deadline(doc_deadline),
        "released_locked": 0,
        "released_locked_at": "",
        "released_locked_by": "",
        "created_at": ts,
        "source": "cloned_from_previous" if previous else "empty",
        "cloned_from": previous["id"] if previous else "",
    }
    previous_name = previous["name"] if previous else ""
    summary = f"创建 release「{name}」，从「{previous_name}」克隆" if previous else f"创建 release「{name}」"
    with transaction(conn):
        releases_repo.save_release(conn, release)
        for app in apps_repo.list_apps(conn):
            old = previous["snapshots"].get(app["id"]) if previous else None
            if not old:
                continue
            snapshot = json.loads(json.dumps(old))
            snapshot.pop("locked_in_release", None)
            snapshot.update(
                {
                    "qa_status": "not_checked",
                    "qa_issue_note": "",
                    "missing_items": [],
                }
            )
            snapshots_repo.save_snapshot(conn, release_id, app["id"], snapshot)
            log_audit(
                conn,
                f"本 app 随 release 从「{previous_name}」克隆而来，已继承上一版的发布信息",
                ts=ts,
                user=user,
                role=role,
                app_id=app["id"],
                release_id=release_id,
                event="clone_app",
            )
        log_audit(conn, summary, ts=ts, user=user, role=role, release_id=release_id, event="create_release")
    return {"release_id": release_id}


# ---------------------------------------------------------------------------
# Deadline update
# ---------------------------------------------------------------------------

def update_deadlines(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    name: str | None,
    app_freeze_deadline: str | None,
    doc_deadline: str | None,
    user: str,
    role: str,
) -> dict:
    """Update name and/or deadline fields on a release.

    Returns {"release": <serialized release>}.
    Mirrors server.py:696-709 and core.py:update_release_deadlines.
    """
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改 release 设置")
    new_name = (name or "").strip() if name is not None else release.get("name", "")
    if not new_name:
        raise ValueError("Release 名称不能为空")
    new_freeze = (
        normalize_deadline(app_freeze_deadline)
        if app_freeze_deadline is not None
        else release.get("app_freeze_deadline", "")
    )
    new_doc = normalize_deadline(doc_deadline) if doc_deadline is not None else release.get("doc_deadline", "")
    validate_deadline_order(new_freeze, new_doc)
    with transaction(conn):
        releases_repo.update_deadlines(
            conn,
            release_id,
            name=new_name,
            app_freeze_deadline=new_freeze,
            doc_deadline=new_doc,
        )
        log_audit(
            conn,
            f"更新 release 设置：{release['name']} -> {new_name}，app_freeze={new_freeze or '空'}, doc={new_doc or '空'}",
            ts=beijing_timestamp(),
            user=user,
            role=role,
            release_id=release_id,
            event="update_release_settings",
        )
    return {"release": _serialize_release(release_reads.get_release(conn, release_id))}


# ---------------------------------------------------------------------------
# Lock / unlock
# ---------------------------------------------------------------------------

def final_lock(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    user: str,
    role: str,
) -> dict:
    """Final-lock a release and generate final artifacts.

    Returns {"artifacts": [...]}.
    Mirrors server.py:710-715 except for current FastAPI artifact rules.
    """
    from app.services import artifact_service

    artifacts = artifact_service.final_lock_release(
        conn,
        release_id,
        user=user,
        role=role,
    )
    return {"artifacts": list(artifacts)}


def final_unlock(
    conn: sqlite3.Connection,
    *,
    release_id: str,
    user: str,
    role: str,
) -> dict:
    """Reverse a final lock: clear the locked flag, delete final artifacts.

    Returns {"ok": True}.
    Mirrors server.py:716-721 and core.py:final_unlock_release.
    """
    release = release_reads.get_release(conn, release_id)
    if not release.get("released_locked"):
        raise RuntimeError("Release 未锁定，无需解锁")
    with transaction(conn):
        releases_repo.unlock_release(conn, release_id)
        for app_id, snapshot in release["snapshots"].items():
            snapshot.pop("app_meta", None)
            snapshot.pop("locked_in_release", None)
            snapshots_repo.save_snapshot(conn, release_id, app_id, snapshot)
        artifacts_repo.delete_final_artifacts(conn, release_id)
        log_audit(
            conn,
            f"Release 解锁：{release['name']}",
            ts=beijing_timestamp(),
            user=user,
            role=role,
            release_id=release_id,
            event="final_unlock",
        )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Release schedule
# ---------------------------------------------------------------------------

def _normalize_schedule_date(value: str | None) -> str:
    """Normalize a schedule date string to ``YYYY-MM-DD``.

    Schedule entries store calendar dates only (no time-of-day); strip any
    trailing time component so the table renders cleanly.
    """
    import datetime as dt

    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ").split(" ")[0]
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid date: {value!r}; expected YYYY-MM-DD") from exc


def upsert_schedule_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str | None,
    version: str,
    branch_cut_at: str,
    release_at: str,
    note: str,
    user: str,
    role: str,
) -> dict:
    """Create or update a release schedule entry.

    Returns {"entry": <entry dict>}.
    Mirrors server.py:995-1009 and core.py:upsert_release_schedule.
    """
    version = (version or "").strip()
    if not version:
        raise ValueError("版本号不能为空")
    branch_cut = _normalize_schedule_date(branch_cut_at)
    release_date = _normalize_schedule_date(release_at)
    if branch_cut and release_date and branch_cut > release_date:
        raise ValueError("拉 branch 时间不能晚于发布时间")
    note = (note or "").strip()
    ts = beijing_timestamp()
    with transaction(conn):
        existing = schedule_repo.get_schedule_entry(conn, entry_id) if entry_id else None
        if existing:
            schedule_repo.update_schedule_entry(
                conn,
                entry_id,
                version=version,
                branch_cut_at=branch_cut,
                release_at=release_date,
                note=note,
                updated_at=ts,
                updated_by=user,
            )
            log_audit(
                conn,
                f"更新发布时间线：{version}",
                ts=ts,
                user=user,
                role=role,
                event="update_release_schedule",
                detail=field_diff(
                    existing,
                    {"version": version, "branch_cut_at": branch_cut, "release_at": release_date, "note": note},
                    {"version": "版本号", "branch_cut_at": "拉 branch 时间", "release_at": "发布时间", "note": "备注"},
                ),
            )
            final_id = entry_id
        else:
            final_id = entry_id or new_id("sched")
            schedule_repo.insert_schedule_entry(
                conn,
                entry_id=final_id,
                version=version,
                branch_cut_at=branch_cut,
                release_at=release_date,
                note=note,
                created_at=ts,
                created_by=user,
            )
            log_audit(
                conn,
                f"新增发布时间线：{version}",
                ts=ts,
                user=user,
                role=role,
                event="create_release_schedule",
                detail={"version": version, "branch_cut_at": branch_cut, "release_at": release_date, "note": note},
            )
        entry = schedule_repo.get_schedule_entry(conn, final_id)
    return {"entry": entry}


def delete_schedule_entry(
    conn: sqlite3.Connection,
    *,
    entry_id: str,
    user: str,
    role: str,
) -> dict:
    """Delete a release schedule entry.

    Returns {"ok": True} on success; raises RuntimeError if not found.
    Mirrors server.py:1010-1017 and core.py:delete_release_schedule.
    """
    if not entry_id:
        raise ValueError("id is required")
    with transaction(conn):
        existing = schedule_repo.get_schedule_entry(conn, entry_id)
        if not existing:
            raise RuntimeError("entry not found")
        schedule_repo.delete_schedule_entry(conn, entry_id)
        log_audit(
            conn,
            f"删除发布时间线：{existing['version']}",
            ts=beijing_timestamp(),
            user=user,
            role=role,
            event="delete_release_schedule",
            detail={"id": entry_id, "version": existing["version"]},
        )
    return {"ok": True}
