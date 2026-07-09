"""QA service — status batch updates, log upload/download, and LLM analysis.

Most orchestration delegates to release_system.core.  QA status updates live
here because FastAPI has runtime-only release-note rules that intentionally
differ from the frozen legacy reference.
"""
from __future__ import annotations

import base64
import itertools
import re
import sqlite3
from pathlib import Path
from typing import Any

import release_system.core as core
from app.db.connection import transaction
from app.domain.decisions import normalize_release_decision
from app.domain import phases as phase_policy
from app.repositories import apps_repo, releases_repo, snapshots_repo, users_repo
from app.repositories.audit_repo import log_audit
from app.repositories.snapshots_repo import save_snapshot
from app.timeutil import beijing_timestamp

_ISSUE_NOTE_REQUIRED_STATUSES = {"has_issues", "cannot_release"}
_QA_STATUS_LABELS = {
    "has_issues": "存在问题",
    "cannot_release": "不可发布",
}
QA_RELEASE_REPORT_COLUMNS = [
    "类别", "名称", "Owner", "类型", "描述", "官方URL", "git_url", "git_branch",
    "对应官方版本", "OS", "Python version", "PyTorch version",
    "X86支持芯片系列", "ARM支持芯片类型", "对比",
    "开发者社区发布情况", "开发者社区发布包支持python版本",
    "开发者社区发布包支持的底层框架及版本",
    "ARM / Kylin sanity", "Ubuntu / 兼容性 sanity",
]

QA_TEST_CMD_COLUMNS = [
    "app_name", "git_branch", "app_version", "arch",
    "maca_version", "test_name", "docker_cmd",
]

# ---------------------------------------------------------------------------
# QA status batch update — POST /api/qa/status-batch
# ---------------------------------------------------------------------------

def set_qa_status_batch(
    conn: sqlite3.Connection,
    release_id: str,
    items: list[dict],
    *,
    user: str,
    role: str,
) -> dict:
    """Apply several QA-status updates atomically.

    ``cannot_release`` follows the same note requirement as ``has_issues``.
    Returns {"ok": True, "updated": n}.
    """
    release = core.get_release(conn, release_id)
    phase_policy.require_can(release, "edit_qa_status", "Release 已最终锁定，不可修改 QA 状态")

    # (app_id, snapshot, status, issue_note, old_status, old_note)
    prepared: list[tuple[str, dict[str, Any], str, str, str, str]] = []
    errors: list[str] = []
    for item in items:
        app_id = item.get("app_id", "")
        status = item.get("status", "")
        issue_note = (item.get("issue_note") or "").strip()
        if status not in core.QA_STATUSES:
            errors.append(f"{app_id}：无效的 QA 状态 {status!r}")
            continue
        snapshot = release["snapshots"].get(app_id)
        if not snapshot:
            errors.append(f"{app_id}：不在本 release 中")
            continue
        if snapshot.get("release_decision") != "release":
            errors.append(f"{app_id}：仅 release 决策的 app 可标注 QA 状态")
            continue
        if status in _ISSUE_NOTE_REQUIRED_STATUSES and not issue_note:
            label = _QA_STATUS_LABELS.get(status, status)
            errors.append(f"{app_id}：标注「{label}」时必须填写问题说明")
            continue
        prepared.append((
            app_id,
            snapshot,
            status,
            issue_note,
            snapshot.get("qa_status", "not_checked"),
            snapshot.get("qa_issue_note", ""),
        ))

    if errors:
        raise ValueError("；".join(errors))

    ts = beijing_timestamp()
    with transaction(conn):
        for app_id, snapshot, status, issue_note, old_status, old_note in prepared:
            snapshot["qa_status"] = status
            snapshot["qa_issue_note"] = (
                issue_note if status in _ISSUE_NOTE_REQUIRED_STATUSES else ""
            )
            save_snapshot(conn, release_id, app_id, snapshot)
            detail = [{"field": "qa_status", "label": "QA 状态", "old": old_status, "new": status}]
            if old_note or snapshot["qa_issue_note"]:
                detail.append({
                    "field": "qa_issue_note",
                    "label": "问题说明",
                    "old": old_note,
                    "new": snapshot["qa_issue_note"],
                })
            log_audit(
                conn,
                f"QA 标注 {app_id} 为 {status}" + (f"：{issue_note}" if issue_note else ""),
                ts=ts,
                user=user,
                role=role,
                app_id=app_id,
                release_id=release_id,
                event="qa_set_status",
                detail=detail,
            )
    updated = {app_id: snapshot for app_id, snapshot, *_ in prepared}
    return {"ok": True, "updated": len(updated)}


# ---------------------------------------------------------------------------
# QA log upload — POST /api/qa/upload-log
# ---------------------------------------------------------------------------

def upload_qa_log(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    content_b64: str,
    filename: str,
    db_path: Path,
    user: str,
    role: str,
) -> dict:
    """Decode and store a base64-encoded QA log.

    Mirrors server.py:954-971.  Returns {"ok": True, **meta}.
    """
    if not content_b64:
        raise ValueError("content_base64 required")
    release = core.get_release(conn, release_id)
    phase_policy.require_can(release, "upload_qa_log", "Release 已最终锁定，不可上传 QA log")
    content = base64.b64decode(content_b64)
    meta = core.qa_upload_log(
        conn,
        db_path,
        release_id,
        content,
        filename,
        user=user,
        role=role,
    )
    return {"ok": True, **meta}


# ---------------------------------------------------------------------------
# QA log download — GET /api/qa-log/download
# ---------------------------------------------------------------------------

def get_qa_log_download(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[bytes, str]:
    """Return (file_bytes, filename) for a QA log download.

    Mirrors server.py:416-433.
    Raises RuntimeError (→ 400) when no log or file is missing.
    The router converts these to the right HTTP responses.
    """
    if not release_id:
        raise ValueError("release_id is required")
    meta = core.get_qa_log(conn, release_id)
    if not meta:
        # Old server sends HTTP 404 directly; service raises RuntimeError so
        # the router can map it to a 404 Response.
        raise RuntimeError("no qa log")
    path = Path(meta["storage_path"])
    if not path.exists():
        raise RuntimeError("qa log file missing")
    return path.read_bytes(), meta["filename"]


# ---------------------------------------------------------------------------
# QA reports — GET /api/qa-reports
# ---------------------------------------------------------------------------

def _normalize_doc_target(value: str | None) -> str:
    target = (value or "manual").strip()
    aliases = {"HPC": "manual", "hpc": "manual", "manual": "manual", "AI4Sci": "ai4sci"}
    return aliases.get(target, target or "manual")


def _display_name(official_name: str | None, version: str | None = "") -> str:
    official = (official_name or "").strip()
    ver = (version or "").strip()
    return f"{official} {ver}".strip() if ver else official


def _app_view(app: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = snapshot or {}
    view = {
        "id": app.get("id", ""),
        "git_url": app.get("git_url", ""),
        "git_branch": app.get("git_branch", ""),
        "official_name": snapshot.get("official_name", ""),
        "type": snapshot.get("type", ""),
        "official_url": snapshot.get("official_url", ""),
        "description": snapshot.get("description", ""),
        "doc_target": _normalize_doc_target(snapshot.get("doc_target")),
        "owners": list(snapshot.get("owners", []) or []),
        "version": snapshot.get("version", ""),
    }
    view["name"] = _display_name(view["official_name"], view["version"])
    return view


def _order_chips(values: str | list[str] | set[str] | tuple[str, ...] | None) -> list[str]:
    if isinstance(values, str):
        items: list[Any] = re.split(r"[,，、;；/]+", values)
    else:
        items = list(values or [])
    seen: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.append(text)
    rest = sorted((c for c in seen if c.lower() != "x201"), key=str.lower)
    tail = [c for c in seen if c.lower() == "x201"]
    return rest + tail


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
    maca_version: str,
) -> list[list[str]]:
    version_value = raw.get("app_version")
    app_version = str(version_value).strip() if version_value not in (None, "") else ""
    app_arches = _report_build_arches(raw)

    rows: list[list[str]] = []
    for test_name, test_cfg in (raw.get("app_test") or {}).items():
        if not isinstance(test_cfg, dict) or not test_cfg.get("enabled"):
            continue
        if str(test_cfg.get("test_period") or "").strip().lower() == "weekly":
            continue
        if test_cfg.get("ignore_release"):
            continue
        expanded_cmds = _report_expanded_docker_cmds(raw, test_cfg)
        if expanded_cmds:
            for arch, docker_cmd in expanded_cmds:
                rows.append([
                    app_name, git_branch, app_version, arch, maca_version, str(test_name), docker_cmd
                ])
            continue
        docker_cmd = _report_docker_cmd(test_cfg)
        for arch in sorted(a for a in (_report_test_arches(test_cfg) or app_arches) if a):
            if _report_test_skip(test_cfg, arch):
                continue
            rows.append([
                app_name,
                git_branch,
                app_version,
                _report_denormalize_arch(arch),
                maca_version,
                str(test_name),
                docker_cmd,
            ])
    return rows


def _compare_summary(snapshot: dict[str, Any], base_snapshot: dict[str, Any] | None) -> str:
    cur_decision = normalize_release_decision(snapshot.get("release_decision"))
    if base_snapshot is None:
        return "新增发布" if cur_decision == "release" else ""

    tags: list[str] = []
    base_decision = normalize_release_decision(base_snapshot.get("release_decision"))
    if cur_decision == "release" and base_decision != "release":
        tags.append("新增发布")
    elif cur_decision != "release" and base_decision == "release":
        tags.append("停止发布")

    def _chip_set(value: Any) -> tuple[str, ...]:
        return tuple(_order_chips(value or ""))

    if (_chip_set(snapshot.get("x86_chips")) != _chip_set(base_snapshot.get("x86_chips"))
            or _chip_set(snapshot.get("arm_chips")) != _chip_set(base_snapshot.get("arm_chips"))):
        tags.append("支持芯片修改")

    def _test_cmd_set(snap: dict[str, Any]) -> set[tuple[str, str]]:
        raw = (snap.get("app_info") or {}).get("raw")
        if not isinstance(raw, dict):
            return set()
        out: set[tuple[str, str]] = set()
        for name, cfg in (raw.get("app_test") or {}).items():
            if not isinstance(cfg, dict) or not cfg.get("enabled"):
                continue
            if str(cfg.get("test_period") or "").strip().lower() == "weekly":
                continue
            if cfg.get("ignore_release"):
                continue
            expanded_cmds = _report_expanded_docker_cmds(raw, cfg)
            if expanded_cmds:
                for _, cmd in expanded_cmds:
                    out.add((str(name), cmd))
            else:
                out.add((str(name), _report_docker_cmd(cfg)))
        return out

    if _test_cmd_set(snapshot) != _test_cmd_set(base_snapshot):
        tags.append("测试范围变更")

    def _test_doc_map(snap: dict[str, Any]) -> dict[str, tuple[str, str, str, str, str]]:
        out: dict[str, tuple[str, str, str, str, str]] = {}
        for doc in snap.get("test_docs") or []:
            if not isinstance(doc, dict) or doc.get("obsolete"):
                continue
            path = str(doc.get("path") or "")
            if not path:
                continue
            out[path] = (
                str(doc.get("dataset") or ""),
                str(doc.get("content") or ""),
                str(doc.get("result_view") or ""),
                str(doc.get("pass_criteria") or ""),
                str(doc.get("command") or ""),
            )
        return out

    if _test_doc_map(snapshot) != _test_doc_map(base_snapshot):
        tags.append("测试说明变更")

    if (snapshot.get("version") or "") != (base_snapshot.get("version") or ""):
        tags.append("版本变更")

    return "; ".join(tags)


def _user_display_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        user["username"]: str(user.get("display_name") or "").strip()
        for user in users_repo.list_users(conn)
        if str(user.get("display_name") or "").strip()
    }


def _community_report_values(app: dict[str, Any], snapshot: dict[str, Any]) -> tuple[str, str, str]:
    if not str(app.get("cicd_community_artifact") or "").strip():
        return "", "", ""
    community = snapshot.get("community") or {}
    return (
        community.get("release_status", ""),
        community.get("python_version", ""),
        community.get("framework_version", ""),
    )


def get_qa_reports(
    conn: sqlite3.Connection,
    release_id: str,
    compare_release_id: str = "",
) -> dict:
    """Build and return QA release-report and test-command tables."""
    if not release_id:
        raise ValueError("release_id is required")
    release = releases_repo.get_release_row(conn, release_id)
    if not release:
        raise KeyError(f"Unknown release: {release_id}")

    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    snapshots = snapshots_repo.get_all_for_release(conn, release_id)
    maca_version = release.get("maca_version", "")

    base_snapshots: dict[str, dict[str, Any]] = {}
    base_release_name = ""
    if compare_release_id and compare_release_id != release_id:
        base_release = releases_repo.get_release_row(conn, compare_release_id)
        if base_release:
            base_snapshots = snapshots_repo.get_all_for_release(conn, compare_release_id)
            base_release_name = base_release.get("name", "")

    items = []
    for app_id, snapshot in snapshots.items():
        app = apps.get(app_id)
        if app:
            items.append((_app_view(app, snapshot), app, snapshot, app_id))
    items.sort(key=lambda item: (
        0 if normalize_release_decision(item[2].get("release_decision")) == "release" else 1,
        (item[0]["name"] or "").lower(),
    ))

    release_rows: list[list[str]] = []
    release_rows_meta: list[dict[str, Any]] = []
    test_rows: list[list[str]] = []
    compare_active = bool(compare_release_id) and compare_release_id != release_id
    display_names = _user_display_names(conn)
    for view, app, snapshot, app_id in items:
        sanity = snapshot.get("sanity") or {}
        compare_value = _compare_summary(snapshot, base_snapshots.get(app_id)) if compare_active else ""
        decision = normalize_release_decision(snapshot.get("release_decision"))
        is_release = decision == "release"
        if not is_release and not compare_value:
            continue
        if is_release:
            x86_chips = ",".join(_order_chips(snapshot.get("x86_chips", "")))
            arm_chips = ",".join(_order_chips(snapshot.get("arm_chips", "")))
            display_compare = compare_value
            community_release, community_python, community_framework = _community_report_values(
                app, snapshot
            )
            arm_kylin_sanity = "✔" if sanity.get("arm_kylin") else ""
            ubuntu_sanity = "✔" if sanity.get("ubuntu") else ""
        else:
            x86_chips = ""
            arm_chips = ""
            display_compare = compare_value
            community_release = ""
            community_python = ""
            community_framework = ""
            arm_kylin_sanity = ""
            ubuntu_sanity = ""
        release_rows_meta.append({"release_decision": decision, "is_release": decision == "release"})
        release_rows.append([
            "AI4Sci" if view["doc_target"] == "ai4sci" else "HPC",
            view["official_name"],
            _owner_display_text(view["owners"], display_names),
            view["type"],
            view["description"],
            view["official_url"],
            app.get("git_url", ""),
            app.get("git_branch", ""),
            snapshot.get("version", ""),
            snapshot.get("build_os", ""),
            snapshot.get("python_labels", ""),
            snapshot.get("pytorch_labels", ""),
            x86_chips,
            arm_chips,
            display_compare,
            community_release,
            community_python,
            community_framework,
            arm_kylin_sanity,
            ubuntu_sanity,
        ])
        if is_release:
            raw = (snapshot.get("app_info") or {}).get("raw")
            if isinstance(raw, dict):
                test_rows.extend(
                    _report_test_cmd_rows(
                        raw, view["official_name"], app.get("git_branch", ""), maca_version
                    )
                )
    test_rows.sort(key=lambda row: (row[0].lower(), row[1].lower(), row[2].lower(), row[3].lower()))

    return {
        "release_name": release.get("name", ""),
        "maca_version": maca_version,
        "compare_release_id": compare_release_id or "",
        "compare_release_name": base_release_name,
        "release_report": {
            "columns": QA_RELEASE_REPORT_COLUMNS,
            "rows": release_rows,
            "rows_meta": release_rows_meta,
        },
        "test_cmd": {"columns": QA_TEST_CMD_COLUMNS, "rows": test_rows},
        "generated_at": beijing_timestamp(),
    }


# ---------------------------------------------------------------------------
# QA analyze-log (synchronous) — POST /api/qa/analyze-log
# ---------------------------------------------------------------------------

def analyze_qa_log_sync(
    conn: sqlite3.Connection,
    release_id: str,
    db_path: Path,
) -> dict:
    """Run LLM analysis synchronously and return results.

    Mirrors server.py:973-982.  Synchronous — blocks until analysis completes.
    """
    return core.qa_analyze_log(conn, db_path, release_id)


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

def get_qa_analysis_status(
    job_id: str,
    *,
    user: str,
    role: str,
) -> dict | None:
    """Return job status snapshot or None if unknown/expired.

    Mirrors server.py:344-355.  AuthzError raised inside poll_job if caller
    isn't RM and doesn't own the job.
    """
    from app.services.qa_jobs import get_registry

    return get_registry().poll_job(job_id, user=user, role=role)
