"""Artifact generation service.

Most rendering still reuses the frozen release_system.core implementation.
FastAPI overlays current release-note rules here without editing the legacy
reference files.
"""
from __future__ import annotations

import contextlib
import csv
import io
import sqlite3
import threading
from collections.abc import Iterator
from typing import Any

from release_system import core as _core
from app.timeutil import beijing_timestamp

_core_artifact_patch_lock = threading.RLock()
_ISSUE_NOTE_STATUSES = {"has_issues", "cannot_release"}
_NON_RELEASE_MANAGER_REVIEW_BLANK_FIELDS = {
    "chip_support",
    "qa_issue_note",
    "releasable",
    "known_limitations",
}


def _qa_status_label(snapshot: dict[str, Any]) -> str:
    status = snapshot.get("qa_status", "not_checked")
    return {
        "qa_passed": "通过",
        "has_issues": "存在问题",
        "cannot_release": "不可发布",
        "not_checked": "未测试",
    }.get(status, str(status))


def _qa_issue_note(snapshot: dict[str, Any]) -> str:
    if snapshot.get("qa_status") in _ISSUE_NOTE_STATUSES:
        return str(snapshot.get("qa_issue_note") or "")
    return ""


def _qualifies_for_release_note(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("release_decision") != "release":
        return False
    if not snapshot.get("owner_confirmed"):
        return False
    if _core._docs_gate_items(snapshot):  # noqa: SLF001 - service overlays frozen core rules.
        return False
    return snapshot.get("qa_status") in {"qa_passed", "has_issues", "cannot_release"}


def _merged_limitations_with_qa_note(snapshot: dict[str, Any]) -> str:
    text = _core._owner_limitations(snapshot)  # noqa: SLF001 - service overlays frozen core rules.
    note = _qa_issue_note(snapshot)
    if not note:
        return text
    prefix = "QA 不可发布" if snapshot.get("qa_status") == "cannot_release" else "QA 备注"
    qa_text = f"{prefix}：{note}"
    return f"{text}\n\n{qa_text}".strip() if text else qa_text


def _render_release_note_with_qa(release: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    out = _core.md_title(f"MACA HPC 发布列表 - {release['name']}")
    headers = [
        "名称",
        "类型",
        "描述",
        "对应官方版本",
        "X86支持芯片系列",
        "ARM支持芯片类型",
        "QA状态",
        "QA问题说明",
    ]
    out += "| " + " | ".join(headers) + " |\n"
    out += "| " + " | ".join("---" for _ in headers) + " |\n"
    for app, snapshot in rows:
        cells = [
            app["name"],
            app.get("type") or "",
            app.get("description") or "",
            snapshot.get("version") or "",
            snapshot.get("x86_chips") or "",
            snapshot.get("arm_chips") or "",
            _qa_status_label(snapshot),
            _qa_issue_note(snapshot),
        ]
        out += "| " + " | ".join(_core._md_cell(c) for c in cells) + " |\n"  # noqa: SLF001
    out += "\n"
    return out


def _is_non_release_decision(snapshot: dict[str, Any]) -> bool:
    return _core.normalize_release_decision(snapshot.get("release_decision")) != "release"


def _render_manager_review_csv(
    conn: sqlite3.Connection,
    release: dict[str, Any],
    fields: list[str] | None = None,
) -> str:
    """Render Manager Review CSV with FastAPI-only non-release row handling."""
    field_labels = dict(_core.MANAGER_REVIEW_FIELDS)
    selected = fields or _core.DEFAULT_MANAGER_REVIEW_FIELDS
    if not selected:
        raise ValueError("至少选择一个输出字段")
    invalid = [field for field in selected if field not in field_labels]
    if invalid:
        raise ValueError(f"未知 Manager Review 字段: {', '.join(invalid)}")

    apps = {app["id"]: app for app in _core.list_apps(conn)}
    display_names = _core._user_display_names(conn)  # noqa: SLF001
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([field_labels[field] for field in selected])
    rows: list[tuple[bool, dict[str, Any], dict[str, Any]]] = []
    for app_id, snapshot in release["snapshots"].items():
        app = apps.get(app_id)
        if not app:
            continue
        rows.append((_is_non_release_decision(snapshot), _core.app_view(app, snapshot), snapshot))
    rows.sort(key=lambda item: (1 if item[0] else 0, item[1].get("name", "").lower()))

    for is_non_release, app, snapshot in rows:
        values = {
            "app_name": app.get("name", ""),
            "official_name": app.get("official_name", ""),
            "doc_target": "AI4Sci" if _core.normalize_doc_target(app.get("doc_target")) == "ai4sci" else "HPC",
            "app_type": app.get("type", ""),
            "version": snapshot.get("version", ""),
            "owners": _core._owner_display_text(app.get("owners", []), display_names),  # noqa: SLF001
            "chip_support": _core._chip_support_text(snapshot),  # noqa: SLF001
            "qa_issue_note": snapshot.get("qa_issue_note", ""),
            "x86_chips": snapshot.get("x86_chips", ""),
            "arm_chips": snapshot.get("arm_chips", ""),
            "release_decision": _core.normalize_release_decision(snapshot.get("release_decision")),
            "qa_status": snapshot.get("qa_status", "not_checked"),
            "owner_confirmed": "是" if snapshot.get("owner_confirmed") else "否",
            "releasable": "是" if _core._qualifies_for_final(snapshot) else "否",  # noqa: SLF001
            "not_releasable_reason": _core._not_releasable_reason(snapshot),  # noqa: SLF001
            "known_limitations": _core._owner_limitations(snapshot),  # noqa: SLF001
            "gerrit_url": app.get("git_url", ""),
            "git_branch": app.get("git_branch", ""),
        }
        if is_non_release:
            for field in _NON_RELEASE_MANAGER_REVIEW_BLANK_FIELDS:
                values[field] = ""
            values["not_releasable_reason"] = "不发布"
        writer.writerow([values[field] for field in selected])
    return out.getvalue()


@contextlib.contextmanager
def _runtime_artifact_rules() -> Iterator[None]:
    """Apply FastAPI-only artifact rules without editing the frozen core file."""
    with _core_artifact_patch_lock:
        original_qualifies = _core._qualifies_for_final  # noqa: SLF001
        original_merged_limitations = _core._merged_limitations  # noqa: SLF001
        original_render_release_note = _core.render_release_note
        original_now = _core.now
        _core._qualifies_for_final = _qualifies_for_release_note  # type: ignore[attr-defined]  # noqa: SLF001
        _core._merged_limitations = _merged_limitations_with_qa_note  # type: ignore[attr-defined]  # noqa: SLF001
        _core.render_release_note = _render_release_note_with_qa  # type: ignore[assignment]
        _core.now = beijing_timestamp
        try:
            yield
        finally:
            _core._qualifies_for_final = original_qualifies  # type: ignore[attr-defined]  # noqa: SLF001
            _core._merged_limitations = original_merged_limitations  # type: ignore[attr-defined]  # noqa: SLF001
            _core.render_release_note = original_render_release_note  # type: ignore[assignment]
            _core.now = original_now


@contextlib.contextmanager
def _runtime_manager_review_rules() -> Iterator[None]:
    """Apply FastAPI-only Manager Review CSV rules."""
    with _core_artifact_patch_lock:
        original_render_manager_review = _core.render_manager_review_csv
        original_now = _core.now
        _core.render_manager_review_csv = _render_manager_review_csv  # type: ignore[assignment]
        _core.now = beijing_timestamp
        try:
            yield
        finally:
            _core.render_manager_review_csv = original_render_manager_review  # type: ignore[assignment]
            _core.now = original_now


def get_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
    *,
    role: str,
) -> dict:
    """Return one artifact row for download.

    Mirrors server.py:501-525 logic.
    Returns dict with keys: name, content, generated_at.
    Raises AuthzError if the user's role cannot see this artifact kind.
    Raises KeyError if the artifact does not exist (caller should return 404).
    """
    from app.api.errors import AuthzError

    # Non-RM can only see the four public kinds (mirrors server.py:504-505)
    _public_kinds = {"release_note", "manual", "ai4sci", "data"}
    if role != "RM" and kind not in _public_kinds:
        raise AuthzError("只有 RM 可查看该 artifact")

    row = conn.execute(
        "SELECT name, content, generated_at FROM artifacts"
        " WHERE release_id = ? AND kind = ?",
        (release_id, kind),
    ).fetchone()
    if not row:
        raise KeyError(f"artifact not found: {kind}")
    return dict(row)


def generate_artifacts(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """(Re)generate all artifacts for a release.

    Mirrors server.py:722-730 except for current FastAPI release-note rules.
    Raises AuthzError if the caller is not RM or Owner.
    Raises RuntimeError if caller passes final=True (server blocks this).
    Returns dict of kind→content (same as core.generate_artifacts).
    """
    from app.api.errors import AuthzError

    if role not in {"RM", "Owner"}:
        raise AuthzError("只有 RM、Owner 可刷新发布文档")

    # final=True is blocked at router level; service enforces the same check
    with _runtime_artifact_rules():
        return _core.generate_artifacts(conn, release_id, final=False)


def final_lock_release(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict[str, str]:
    """Final-lock a release using the FastAPI artifact rules."""
    from app.services import cicd_service

    cicd_service.ensure_no_open_deferred_release_decision_for_release(conn, release_id)
    with _runtime_artifact_rules():
        return _core.final_lock_release(conn, release_id, user=user, role=role)


def generate_manager_review(
    conn: sqlite3.Connection,
    release_id: str,
    fields: list[str] | None = None,
    *,
    user: str,
    role: str,
) -> str:
    """Generate (and persist) the manager-review CSV.

    Mirrors server.py:732-742 except for current FastAPI CSV ordering rules.
    Returns the CSV content string.
    """
    with _runtime_manager_review_rules():
        return _core.generate_manager_review_csv(
            conn,
            release_id,
            fields,
            user=user,
            role=role,
        )


def get_test_scope_csv(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[str, str]:
    """Return (csv_text, filename) for the test-scope CSV.

    Mirrors server.py:402-413.
    """
    csv_text = _core.export_test_scope_csv(conn, release_id)
    release = _core.get_release(conn, release_id)
    filename = f"test_scope_{release['name']}.csv"
    return csv_text, filename


def gerrit_push_plan(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """Return the Gerrit push plan for the given release.

    Mirrors server.py:744-747.
    """
    return _core.gerrit_push_plan(conn, release_id)
