"""QA service — status batch updates, log upload/download, and LLM analysis.

QA status updates live here because FastAPI has runtime-only release-note
rules that intentionally differ from the frozen legacy reference.
"""
from __future__ import annotations

import base64
import itertools
import re
import sqlite3
from pathlib import Path
from typing import Any

from app.api.errors import AuthzError
from app.db.connection import transaction
from app.domain import cycles as cycles_domain
from app.domain import phases as phase_policy
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
from app.services import catalog_service, cycle_service
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
    "base_image", "test_name", "docker_cmd", "perf_golden", "perf_unit",
]

# ---------------------------------------------------------------------------
# QA status batch update — POST /api/qa/status-batch
# ---------------------------------------------------------------------------

_DELIVERABLE_FIELDS = ("deliverable_image", "deliverable_digest", "deliverable_commit")


def set_qa_status_batch(
    conn: sqlite3.Connection,
    release_id: str,
    items: list[dict],
    *,
    user: str,
    role: str,
) -> dict:
    """Apply several QA verdicts (and delivered-image digests) atomically.

    Each item: ``{app_id, status, issue_note, deliverable_image?,
    deliverable_digest?, deliverable_commit?}``.  ``has_issues`` and
    ``cannot_release`` require a note.
    """
    if not has_capability(role, "qa.edit"):
        raise AuthzError("只有 QA、RM 可以修改 QA 结论")
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
        for key in _DELIVERABLE_FIELDS:
            if key in item:
                update[key] = str(item.get(key) or "").strip()
        prepared.append((entry, update))
    if errors:
        raise ValueError("；".join(errors))
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
                detail=[{"field": key, "label": key, **value} for key, value in changes.items()],
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


def _report_normalize_arch(value: Any) -> str:
    arch = str(value or "").strip().lower()
    if arch in ("arm", "arm64", "aarch64"):
        return "arm64"
    if arch in ("x86", "x86_64", "amd64"):
        return "amd64"
    return arch


def _report_denormalize_arch(value: Any) -> str:
    arch = _report_normalize_arch(value)
    if arch == "arm64":
        return "arm"
    if arch == "amd64":
        return "x86"
    return arch


def _report_build_arches(app_info: dict[str, Any]) -> set[str]:
    arches: set[str] = set()
    for cfg in (app_info.get("app_build") or {}).values():
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        arch = _report_normalize_arch(cfg.get("arch"))
        if arch:
            arches.add(arch)
    return arches


def _report_test_arches(test_cfg: dict[str, Any]) -> set[str]:
    arches: set[str] = set()
    supported_chip = test_cfg.get("supported_chip")
    if isinstance(supported_chip, dict):
        for build_keys in supported_chip.values():
            if not isinstance(build_keys, list):
                continue
            for key in build_keys:
                arch = _report_normalize_arch(str(key).rsplit("_", 1)[-1])
                if arch:
                    arches.add(arch)
    return arches


def _report_test_skip(test_cfg: dict[str, Any], arch: str) -> bool:
    supported_chip = test_cfg.get("supported_chip")
    target = _report_normalize_arch(arch)
    if not (isinstance(supported_chip, dict) and supported_chip) or not target:
        return False
    for build_keys in supported_chip.values():
        keys = build_keys if isinstance(build_keys, list) else []
        if any(_report_normalize_arch(str(k).rsplit("_", 1)[-1]) == target for k in keys):
            return False
    return True


_REPORT_IMAGE_TARGET_SUFFIX = {
    "release": "maca",
    "dbg": "maca-dbg",
}


def _report_split_image_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw_values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,，、;；/]+", str(value))
    values: list[str] = []
    for item in raw_values:
        text = str(item or "").strip()
        if text and text not in values:
            values.append(text)
    return values


def _report_image_field_options(raw: dict[str, Any], build_cfg: dict[str, Any], field: str) -> list[str]:
    if field == "sdk_version":
        return ["<hpc_version>"]
    if field in ("app_name", "app_version"):
        value = raw.get(field)
        if value in (None, ""):
            value = build_cfg.get(field)
    else:
        value = build_cfg.get(field)
        if value in (None, ""):
            value = raw.get(field)
    values = _report_split_image_values(value)
    return values or [""]


def _report_build_target_matches(build_cfg: dict[str, Any], image_target: str) -> bool:
    targets = {v.lower() for v in _report_split_image_values(build_cfg.get("build_target"))}
    if not targets:
        return image_target == "release"
    return image_target in targets


def _report_image_values_for_build(
    raw: dict[str, Any],
    build_cfg: dict[str, Any],
    image_target: str,
) -> list[tuple[str, str]]:
    suffix = _REPORT_IMAGE_TARGET_SUFFIX.get(image_target)
    if not suffix:
        return []
    name_fields = [
        _report_image_field_options(raw, build_cfg, "app_name"),
        _report_image_field_options(raw, build_cfg, "spec"),
        [suffix],
    ]
    app_versions = _report_image_field_options(raw, build_cfg, "app_version")
    sdk_versions = _report_image_field_options(raw, build_cfg, "sdk_version")
    pytorch_labels = _report_image_field_options(raw, build_cfg, "pytorch_label")
    python_labels = _report_image_field_options(raw, build_cfg, "python_label")
    os_values = _report_image_field_options(raw, build_cfg, "os")
    arch_values = _report_image_field_options(raw, build_cfg, "arch")

    images: list[tuple[str, str]] = []
    for name_parts in itertools.product(*name_fields):
        image_name = "-".join(part for part in name_parts if part)
        if not image_name:
            continue
        for app_version, sdk_version, pytorch_label, python_label, os_value, arch in itertools.product(
            app_versions, sdk_versions, pytorch_labels, python_labels, os_values, arch_values
        ):
            tag_parts = [app_version, sdk_version, pytorch_label, python_label, os_value, arch]
            tag = "-".join(part for part in tag_parts if part)
            image = f"{image_name}:{tag}" if tag else image_name
            images.append((_report_denormalize_arch(arch), image))
    return images


def _report_docker_images(raw: dict[str, Any], image_target: str) -> list[tuple[str, str]]:
    image_target = str(image_target or "").strip().lower()
    if image_target not in _REPORT_IMAGE_TARGET_SUFFIX:
        return []

    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    app_build = raw.get("app_build") or {}
    build_items = app_build.values() if isinstance(app_build, dict) else []
    for cfg in build_items:
        if not isinstance(cfg, dict) or cfg.get("enabled") is False:
            continue
        if not _report_build_target_matches(cfg, image_target):
            continue
        for row in _report_image_values_for_build(raw, cfg, image_target):
            if row not in seen:
                seen.add(row)
                rows.append(row)

    if not rows and not app_build:
        for row in _report_image_values_for_build(raw, {}, image_target):
            if row not in seen:
                seen.add(row)
                rows.append(row)
    return rows


def _report_docker_cmd(test_cfg: dict[str, Any], docker_image: str | None = None) -> str:
    container_args = str(test_cfg.get("container_args") or "").strip()
    test_cmd = str(test_cfg.get("test_cmd") or "").strip()
    img_target = str(test_cfg.get("img_target") or "").strip().lower()
    if docker_image is not None:
        image = docker_image
    else:
        image = f"[docker_image_{img_target}]" if img_target else "[docker_image]"
    parts = ["docker run --pull always --rm -e MACA_PERF_DIR=/tmp"]
    if test_cfg.get("mount_dataset"):
        parts.append("-v /pde_hpc/dataset:/hpc_dataset:ro")
    if container_args:
        parts.append(container_args)
    parts.append(image)
    parts.append(f"sh -c '{test_cmd}'")
    return " ".join(parts)


def _report_expanded_docker_cmds(raw: dict[str, Any], test_cfg: dict[str, Any]) -> list[tuple[str, str]]:
    img_target = str(test_cfg.get("img_target") or "").strip().lower()
    return [
        (arch, _report_docker_cmd(test_cfg, image))
        for arch, image in _report_docker_images(raw, img_target)
    ]


def _report_test_cmd_rows(
    raw: dict[str, Any],
    app_name: str,
    git_branch: str,
    base_image: str,
    excluded: set[str],
) -> list[list[str]]:
    version_value = raw.get("app_version")
    app_version = str(version_value).strip() if version_value not in (None, "") else ""
    app_arches = _report_build_arches(raw)

    rows: list[list[str]] = []
    for test_name, test_cfg in (raw.get("app_test") or {}).items():
        if not isinstance(test_cfg, dict) or test_cfg.get("enabled") is False:
            continue
        if str(test_name) in excluded:
            continue
        if str(test_cfg.get("test_period") or "").strip().lower() == "weekly":
            continue
        if test_cfg.get("ignore_release"):
            continue
        perf_golden = str(test_cfg.get("perf_golden") or "").strip()
        perf_unit = str(test_cfg.get("perf_unit") or "").strip()
        expanded_cmds = _report_expanded_docker_cmds(raw, test_cfg)
        if expanded_cmds:
            for arch, docker_cmd in expanded_cmds:
                rows.append([app_name, git_branch, app_version, arch, base_image, str(test_name),
                             docker_cmd, perf_golden, perf_unit])
            continue
        docker_cmd = _report_docker_cmd(test_cfg)
        for arch in sorted(a for a in (_report_test_arches(test_cfg) or app_arches) if a):
            if _report_test_skip(test_cfg, arch):
                continue
            rows.append([app_name, git_branch, app_version, _report_denormalize_arch(arch), base_image,
                         str(test_name), docker_cmd, perf_golden, perf_unit])
    return rows


def entry_test_commands(conn: sqlite3.Connection, entry: dict[str, Any]) -> list[dict[str, str]]:
    """Docker test commands of an entry's locked scope (used by digest lookup)."""
    version = app_info_repo.get_version(conn, entry.get("locked_version_id"))
    if not version:
        return []
    app = apps_repo.get_app(conn, entry["app_id"]) or {"id": entry["app_id"]}
    image = releases_repo.get_base_image(conn, entry["base_image_id"]) if entry.get("base_image_id") else None
    rows = _report_test_cmd_rows(
        version["raw"], catalog_service.app_name(app), app.get("git_branch", ""),
        (image or {}).get("name", ""), set(entry.get("excluded_tests") or []),
    )
    return [{"arch": row[3], "test": row[5], "command": row[6]} for row in rows]


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
                    image_name, set(entry["excluded_tests"]),
                ))
    test_rows.sort(key=lambda row: (row[0].lower(), row[1].lower(), row[2].lower(), row[3].lower()))
    return {
        "release_name": release["name"],
        "compare_release_id": compare_release_id or "",
        "compare_release_name": base_release_name,
        "release_report": {"columns": QA_RELEASE_REPORT_COLUMNS, "rows": release_rows, "rows_meta": rows_meta},
        "test_cmd": {"columns": QA_TEST_CMD_COLUMNS, "rows": test_rows},
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
