"""QA service — status batch updates, log upload/download, and LLM analysis.

QA status updates live here because FastAPI has runtime-only release-note
rules that intentionally differ from the frozen legacy reference.
"""
from __future__ import annotations

import base64
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain import cycles as cycles_domain
from app.domain import phases as phase_policy
from app.domain import test_commands
from app.domain import delivery_images
from app.domain.test_platforms import build_alias_is_redundant
from app.domain.permissions import has_capability
from app.domain.qa import (
    QA_STATUSES,
    issue_note_required,
    qa_status_label,
)
from app.domain.textutil import order_chips
from app.repositories import (
    app_info_repo,
    apps_repo,
    entries_repo,
    qa_repo,
    releases_repo,
    users_repo,
)
from app.repositories.audit_repo import log_audit
from app.services import catalog_service, cycle_service, delivery_validation_service
from app.timeutil import beijing_timestamp

QA_RELEASE_REPORT_COLUMNS = [
    "类别", "名称", "Owner", "类型", "描述", "官方URL", "git_url", "git_branch",
    "对应官方版本", "OS", "Python version", "PyTorch version",
    "X86支持芯片系列", "ARM支持芯片类型", "对比",
    "开发者社区发布情况", "开发者社区一级菜单", "开发者社区说明文档链接",
    "开发者社区描述", "开发者社区发布包支持python版本",
    "开发者社区发布包支持的底层框架及版本",
    "ARM / Kylin sanity", "Ubuntu / 兼容性 sanity", "基础镜像", "计划 feature",
]

QA_TEST_CMD_COLUMNS = [
    "app_name", "git_branch", "app_version", "arch",
    "OS", "构建平台", "支持芯片", "commit_id", "test_name", "docker_cmd",
    "perf_golden", "perf_unit", "Python label", "PyTorch label",
]

# ---------------------------------------------------------------------------
# QA status batch update — POST /api/qa/status-batch
# ---------------------------------------------------------------------------

_DELIVERABLE_FIELDS = ("deliverable_image", "deliverable_digest", "deliverable_commit")
_QA_FIELD_LABELS = {
    "deliverable_images": "按构建交付的镜像",
    "qa_status": "QA 状态",
    "qa_note": "问题说明",
    "deliverable_image": "交付镜像",
    "deliverable_digest": "交付镜像 digest",
    "deliverable_commit": "源码 commit",
}


def set_qa_status_batch(
    conn: sqlite3.Connection,
    release_id: str,
    items: list[dict],
    *,
    user: str,
    role: str,
) -> dict:
    """Apply several QA verdicts (and delivered-image digests) atomically.

    Each item: ``{app_id, status, issue_note, deliverable_images?,
    delivery_revision?}``. Validate Harbor artifacts before opening the write
    transaction, then recheck mutable state. ``has_issues`` and
    ``cannot_release`` require a note.
    """
    if not has_capability(role, "qa.edit"):
        raise AuthzError("只有 QA、RM 可以修改 QA 结论")
    prepared = _prepare_qa_status_batch(conn, release_id, items)
    artifacts: dict[tuple[str, str, str], dict] = {}
    for entry, update in prepared:
        if "deliverable_images" in update:
            try:
                delivery_validation_service.validate(update["deliverable_images"], artifacts=artifacts)
            except ApiError as exc:
                raise ApiError(exc.status_code, f'{entry["app_id"]}：{exc}') from exc
            except ValueError as exc:
                raise ValueError(f'{entry["app_id"]}：{exc}') from exc
    with transaction(conn, immediate=True):
        current = _prepare_qa_status_batch(conn, release_id, items)
        before = [(entry["id"], delivery_images.revision(entry), update) for entry, update in prepared]
        after = [(entry["id"], delivery_images.revision(entry), update) for entry, update in current]
        if before != after:
            raise ValueError("QA 结论、交付镜像或报名版本已变化，请刷新后重试")
        return _apply_qa_status_batch(conn, release_id, current, user=user, role=role)


def _prepare_qa_status_batch(conn, release_id, items):
    release = cycle_service.get_release(conn, release_id)
    phase_policy.require_can(release, "edit_qa", "版本已发布，不可修改 QA 结论")
    prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
    errors: list[str] = []
    for item in items:
        app_id = item.get("app_id", "")
        status = item.get("status", "")
        note = (item.get("issue_note") or "").strip()
        if status not in QA_STATUSES:
            errors.append(f"{app_id}：无效的 QA 状态 {status!r}")
            continue
        entry = entries_repo.find_entry(conn, release_id, app_id)
        if not entry or entry["status"] != cycles_domain.ENTRY_ACTIVE:
            errors.append(f"{app_id}：未报名本版本")
            continue
        if issue_note_required(status) and not note:
            errors.append(f"{app_id}：标注「{qa_status_label(status)}」时必须填写问题说明")
            continue
        update = {"qa_status": status, "qa_note": note if issue_note_required(status) else ""}
        if 'deliverable_images' in item:
            if item.get('delivery_revision') != delivery_images.revision(entry):
                errors.append(f'{app_id}：QA 结论、交付镜像或报名版本已变化，请刷新后重试')
                continue
            version = app_info_repo.get_version(conn, entry['locked_version_id'])
            try:
                images = delivery_images.normalize(item['deliverable_images'], (version or {}).get('raw', {}), entry)
            except ValueError as exc:
                errors.append(f'{app_id}：{exc}')
                continue
            first = images[0] if images else {}
            update.update(deliverable_images=images, deliverable_image=first.get('image', ''),
                          deliverable_digest=first.get('digest', ''), deliverable_commit=first.get('commit_id', ''))
        for key in _DELIVERABLE_FIELDS:
            if key in item and 'deliverable_images' not in item:
                if entry['deliverable_images'] and str(item.get(key) or '').strip() != entry[key]:
                    errors.append(f'{app_id}：已有多镜像交付记录，请按构建配置保存镜像')
                    continue
                if str(item.get(key) or '').strip() and str(item[key]).strip() != entry[key]:
                    errors.append(f'{app_id}：交付镜像需校验 OS/CPU 架构，请按构建配置保存镜像')
                    continue
                update[key] = str(item.get(key) or "").strip()
        prepared.append((entry, update))
    if errors:
        raise ValueError("；".join(errors))
    return prepared


def _apply_qa_status_batch(conn, release_id, prepared, *, user, role):
    ts = beijing_timestamp()
    with transaction(conn):
        for entry, update in prepared:
            changes = {key: {"old": entry.get(key), "new": value}
                       for key, value in update.items() if entry.get(key) != value}
            if not changes:
                continue
            entries_repo.update_entry(conn, entry["id"], update, updated_at=ts, updated_by=user)
            log_audit(
                conn,
                f"QA 标注 {entry['app_id']} 为 {update['qa_status']}"
                + (f"：{update['qa_note']}" if update["qa_note"] else ""),
                ts=ts,
                user=user,
                role=role,
                app_id=entry["app_id"],
                release_id=release_id,
                event="qa_set_status",
                detail=[{"field": key, "label": _QA_FIELD_LABELS.get(key, key), **value}
                        for key, value in changes.items()],
            )
        # Saving the verdicts is the review of any AI suggestions.
        qa_repo.delete_ai_suggestions(conn, release_id)
    return {"ok": True, "updated": len(prepared)}


# ---------------------------------------------------------------------------
# QA log upload — POST /api/qa/upload-log
# ---------------------------------------------------------------------------

def upload_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    content_b64: str,
    filename: str,
    user: str,
    role: str,
) -> dict:
    """Decode a base64-encoded QA log and embed it in SQLite."""
    if not content_b64:
        raise ValueError("content_base64 required")
    release = cycle_service.get_release(conn, release_id)
    phase_policy.require_can(release, "upload_qa_log", "版本已发布，不可上传 QA log")
    content = base64.b64decode(content_b64)
    if not filename:
        raise ValueError("filename required")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "qa_log"
    ts = beijing_timestamp()
    with transaction(conn):
        # Suggestions drawn from the previous log no longer apply.
        qa_repo.delete_ai_suggestions(conn, release_id)
        qa_repo.upsert_qa_log(
            conn,
            release_id,
            filename=safe_name,
            content=content,
            uploaded_at=ts,
            uploaded_by=user,
        )
        log_audit(
            conn,
            f"QA 上传 log：{safe_name}",
            ts=ts,
            user=user,
            role=role,
            release_id=release_id,
            event="qa_upload_log",
        )
    meta = qa_repo.get_qa_log(conn, release_id)
    if meta is None:  # Defensive: the upsert above must produce metadata.
        raise RuntimeError("QA log 保存失败")
    return {"ok": True, **meta}


# ---------------------------------------------------------------------------
# QA log download — GET /api/qa-log/download
# ---------------------------------------------------------------------------

def get_qa_log_download(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[bytes, str]:
    """Return ``(file_bytes, filename)`` from SQLite for download."""
    if not release_id:
        raise ValueError("release_id is required")
    meta = qa_repo.get_qa_log(conn, release_id)
    if not meta:
        raise RuntimeError("no qa log")
    stored = qa_repo.get_qa_log_content(conn, release_id)
    if stored is None:
        raise RuntimeError("qa log content missing")
    return stored


# ---------------------------------------------------------------------------
# QA reports — GET /api/qa-reports
# ---------------------------------------------------------------------------

def _owner_display_text(
    owners: list[str] | tuple[str, ...] | set[str] | None,
    display_names: dict[str, str],
) -> str:
    labels = []
    for owner in owners or []:
        username = str(owner or "").strip()
        if username:
            labels.append(display_names.get(username, username))
    return ",".join(labels)


def _report_test_cmd_rows(
    raw: dict[str, Any],
    app_name: str,
    git_branch: str,
    commit_id: str,
    excluded: set[str],
) -> list[list[str]]:
    return [
        [app_name, git_branch, str(raw.get("app_version") or ""), test_commands.denormalize_arch(row["arch"]),
         row["os"], row["build"], ",".join(row["chips"]), commit_id, row["test"], row["command"],
         row["perf_golden"], row["perf_unit"], row["python_label"], row["pytorch_label"]]
        for row in test_commands.command_rows(raw, excluded)
    ]


def entry_test_commands(conn: sqlite3.Connection, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Docker test commands of an entry's locked scope (used by digest lookup)."""
    version = app_info_repo.get_version(conn, entry.get("locked_version_id"))
    if not version:
        return []
    return test_commands.command_rows(version["raw"], set(entry.get("excluded_tests") or []))


def _compare_summary(
    current: dict[str, Any] | None,
    base: dict[str, Any] | None,
) -> str:
    """Differences between an App's entry in two cycles (QA scope view)."""
    if current and not base:
        return "新增发布"
    if base and not current:
        return "本期未报名"
    if not current or not base:
        return ""
    tags: list[str] = []
    cur, old = current.get("locked_app_info") or {}, base.get("locked_app_info") or {}
    if (tuple(order_chips(cur.get("x86_chips") or [])), tuple(order_chips(cur.get("arm_chips") or []))) != (
        tuple(order_chips(old.get("x86_chips") or [])), tuple(order_chips(old.get("arm_chips") or []))
    ):
        tags.append("支持芯片修改")

    def scope(view: dict[str, Any]) -> set[tuple[str, str]]:
        return {(t["path"], t["command"]) for t in view.get("scope_tests", []) if not t["excluded"]}

    if scope(current) != scope(base):
        tags.append("测试范围变更")
    if (current.get("target_version") or "") != (base.get("target_version") or ""):
        tags.append("版本变更")
    return "; ".join(tags)


def _user_display_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        user["username"]: str(user.get("display_name") or "").strip()
        for user in users_repo.list_users(conn)
        if str(user.get("display_name") or "").strip()
    }


def _community_report_values(app: dict[str, Any], summary: dict[str, Any] | None) -> list[str]:
    derived = (summary or {}).get("community") or {}
    if not derived.get("image_count") and not derived.get("pkg_count"):
        return ["", "", "", "", "", ""]
    community = app.get("community") or {}
    has_pkg = bool(derived.get("pkg_count"))
    return [
        derived.get("release_status", ""),
        community.get("menu_level1", ""),
        community.get("doc_url", ""),
        community.get("description", ""),
        community.get("python_version", "") if has_pkg else "",
        community.get("framework_version", "") if has_pkg else "",
    ]


def get_qa_reports(
    conn: sqlite3.Connection,
    release_id: str,
    compare_release_id: str = "",
) -> dict:
    """QA release report (one row per signup) and test command table."""
    if not release_id:
        raise ValueError("release_id is required")
    release = cycle_service.get_release(conn, release_id)
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    images = {image["id"]: image for image in releases_repo.list_base_images(conn, release_id)}
    entries = {e["app_id"]: e for e in cycle_service.list_entry_views(conn, release_id, include_withdrawn=False)}
    compare_active = bool(compare_release_id) and compare_release_id != release_id
    base_entries: dict[str, dict[str, Any]] = {}
    base_release_name = ""
    if compare_active:
        base_release = releases_repo.get_release_row(conn, compare_release_id)
        if base_release:
            base_release_name = base_release["name"]
            base_entries = {
                e["app_id"]: e
                for e in cycle_service.list_entry_views(conn, compare_release_id, include_withdrawn=False)
            }
    display_names = _user_display_names(conn)
    app_ids = set(entries) | (set(base_entries) if compare_active else set())
    ordered = sorted(
        (app_id for app_id in app_ids if app_id in apps),
        key=lambda app_id: (app_id not in entries, catalog_service.app_name(apps[app_id]).lower()),
    )
    release_rows: list[list[str]] = []
    rows_meta: list[dict[str, Any]] = []
    test_rows: list[list[str]] = []
    for app_id in ordered:
        app = apps[app_id]
        entry = entries.get(app_id)
        compare_value = _compare_summary(entry, base_entries.get(app_id)) if compare_active else ""
        rows_meta.append({"app_id": app_id, "signed_up": entry is not None})
        summary = (entry or {}).get("locked_app_info") or {}
        sanity = app.get("sanity") or {}
        image_name = (images.get((entry or {}).get("base_image_id")) or {}).get("name", "")
        signed = entry is not None
        release_rows.append([
            "AI4Sci" if app.get("doc_target") == "ai4sci" else "HPC",
            catalog_service.app_name(app),
            ",".join(display_names.get(o, o) for o in app.get("owners") or []),
            app.get("type", ""),
            app.get("description", ""),
            app.get("official_url", ""),
            app.get("git_url", ""),
            app.get("git_branch", ""),
            (entry or {}).get("target_version", ""),
            ",".join(summary.get("build_os", [])),
            ",".join(summary.get("python_labels", [])),
            ",".join(summary.get("pytorch_labels", [])),
            ",".join(order_chips(summary.get("x86_chips", []))) if signed else "",
            ",".join(order_chips(summary.get("arm_chips", []))) if signed else "",
            compare_value,
            *(_community_report_values(app, summary) if signed else [""] * 6),
            "✔" if signed and sanity.get("arm_kylin") else "",
            "✔" if signed and sanity.get("ubuntu") else "",
            image_name,
            (entry or {}).get("planned_features", ""),
        ])
        if entry:
            version = app_info_repo.get_version(conn, entry["locked_version_id"])
            if version:
                test_rows.extend(_report_test_cmd_rows(
                    version["raw"], catalog_service.app_name(app), app.get("git_branch", ""),
                    str(version.get("commit_id") or ""), set(entry["excluded_tests"]),
                ))
    test_rows.sort(key=lambda row: (row[0].lower(), row[1].lower(), row[2].lower(), row[3].lower()))
    test_columns = list(QA_TEST_CMD_COLUMNS)
    build_column = test_columns.index("构建平台")
    os_column, arch_column = test_columns.index("OS"), test_columns.index("arch")
    if all(build_alias_is_redundant(row[build_column], row[os_column], row[arch_column]) for row in test_rows):
        test_columns.pop(build_column)
        test_rows = [row[:build_column] + row[build_column + 1:] for row in test_rows]
    return {
        "release_name": release["name"],
        "compare_release_id": compare_release_id or "",
        "compare_release_name": base_release_name,
        "release_report": {"columns": QA_RELEASE_REPORT_COLUMNS, "rows": release_rows, "rows_meta": rows_meta},
        "test_cmd": {"columns": test_columns, "rows": test_rows},
        "generated_at": beijing_timestamp(),
    }


# ---------------------------------------------------------------------------
# QA analyze-log (synchronous) — POST /api/qa/analyze-log
# ---------------------------------------------------------------------------

def get_ai_suggestions(conn: sqlite3.Connection, release_id: str) -> dict | None:
    """The release's saved AI suggestions still waiting for review, or None."""
    return qa_repo.get_ai_suggestions(conn, release_id)


def discard_ai_suggestions(conn: sqlite3.Connection, release_id: str, *, user: str, role: str) -> dict:
    """Drop the release's AI suggestions without applying them."""
    with transaction(conn):
        if qa_repo.delete_ai_suggestions(conn, release_id):
            log_audit(
                conn, "丢弃 QA AI 建议", ts=beijing_timestamp(), user=user, role=role,
                release_id=release_id, event="qa_discard_ai_suggestions",
            )
    return {"ok": True}


def analyze_qa_log_sync(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str = "",
) -> dict:
    """Run LLM analysis synchronously against the DB-backed log body."""
    from app.services.qa_analysis_service import analyze_qa_log
    from app.services.qa_jobs import get_registry

    busy = get_registry().running_job(release_id)
    if busy:
        raise ValueError(f"该发布周期的 AI 分析正在进行中（{busy['user']} 发起），可查看进度或中止")
    return analyze_qa_log(conn, release_id, analyzed_by=user)


# ---------------------------------------------------------------------------
# QA analyze-log/start (async) — POST /api/qa/analyze-log/start
# ---------------------------------------------------------------------------

def start_qa_analysis_job(
    release_id: str,
    *,
    user: str,
    role: str,
    db_path: Path,
) -> dict:
    """Start an async LLM analysis job and return the initial job snapshot.

    Mirrors server.py:984-993.  Delegates to QaJobRegistry.
    """
    from app.services.qa_jobs import get_registry

    registry = get_registry()
    return registry.start_job(release_id, user=user, role=role, db_path=db_path)


# ---------------------------------------------------------------------------
# QA analyze-log/status — GET /api/qa/analyze-log/status
# ---------------------------------------------------------------------------

def get_qa_analysis_status(job_id: str) -> dict | None:
    """Return job status snapshot or None if unknown/expired.

    Mirrors server.py:344-355, except that every QA editor sees every job.
    """
    from app.services.qa_jobs import get_registry

    return get_registry().poll_job(job_id)


def get_running_qa_analysis(release_id: str) -> dict | None:
    """The release's AI analysis still running, if any."""
    from app.services.qa_jobs import get_registry

    return get_registry().running_job(release_id)


def cancel_qa_analysis(job_id: str) -> dict | None:
    """Stop a running AI analysis, whoever started it; None when unknown/expired."""
    from app.services.qa_jobs import get_registry

    return get_registry().cancel_job(job_id)
