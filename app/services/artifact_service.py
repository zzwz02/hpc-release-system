"""Artifact generation service.

Full port of the core.py artifact pipeline with the FastAPI runtime rules
applied directly (they used to be monkeypatch overlays on the frozen legacy
module):
  - release note includes QA 状态/QA问题说明 columns and is gated by
    ``qualifies_for_docs`` (QA status is displayed, not a gate);
  - guide known-limitations merge QA notes for has_issues AND cannot_release;
  - Manager Review CSV blanks selected fields for non-release rows;
  - all timestamps are naive Beijing strings (§5.4).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import re
import sqlite3
from typing import Any

from app.db.connection import transaction
from app.domain import gates
from app.domain.decisions import normalize_release_decision
from app.domain.markdown import (
    guide_test_doc_field,
    inline_code,
    markdown_fences_on_new_lines,
    md_cell,
    md_title,
    owner_markdown_block,
)
from app.domain.snapshots import app_view, normalize_doc_target
from app.repositories import releases_repo, snapshots_repo, users_repo
from app.repositories import apps_repo
from app.repositories.audit_repo import log_audit
from app.repositories.base import dumps_json
from app.services import release_reads
from app.timeutil import BEIJING_TZ, beijing_now, beijing_timestamp

_ISSUE_NOTE_STATUSES = {"has_issues", "cannot_release"}
_NON_RELEASE_MANAGER_REVIEW_BLANK_FIELDS = {
    "chip_support",
    "qa_issue_note",
    "releasable",
    "known_limitations",
}

MANAGER_REVIEW_FIELDS = [
    ("app_name", "App"),
    ("official_name", "官方名称"),
    ("doc_target", "文档类型"),
    ("app_type", "App类型"),
    ("version", "版本号"),
    ("owners", "Owner"),
    ("chip_support", "支持芯片类型"),
    ("qa_issue_note", "QA问题"),
    ("x86_chips", "X86支持芯片"),
    ("arm_chips", "ARM支持芯片"),
    ("release_decision", "Release决策"),
    ("qa_status", "QA状态"),
    ("owner_confirmed", "Owner确认"),
    ("releasable", "是否可发布"),
    ("not_releasable_reason", "不可发布原因"),
    ("known_limitations", "已知限制"),
    ("gerrit_url", "Gerrit URL"),
    ("git_branch", "Branch"),
]
DEFAULT_MANAGER_REVIEW_FIELDS = [
    "app_name",
    "owners",
    "chip_support",
    "qa_issue_note",
    "releasable",
    "not_releasable_reason",
    "known_limitations",
]


# ---------------------------------------------------------------------------
# QA display helpers
# ---------------------------------------------------------------------------

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


def _owner_limitations(snapshot: dict[str, Any]) -> str:
    return (snapshot.get("doc", {}) or {}).get("limitations", "") or ""


def _merged_limitations_with_qa_note(snapshot: dict[str, Any]) -> str:
    text = _owner_limitations(snapshot)
    note = _qa_issue_note(snapshot)
    if not note:
        return text
    prefix = "QA 不可发布" if snapshot.get("qa_status") == "cannot_release" else "QA 备注"
    qa_text = f"{prefix}：{note}"
    return f"{text}\n\n{qa_text}".strip() if text else qa_text


def _chip_support_text(snapshot: dict[str, Any]) -> str:
    parts = []
    if snapshot.get("x86_chips"):
        parts.append(f"X86: {snapshot['x86_chips']}")
    if snapshot.get("arm_chips"):
        parts.append(f"ARM: {snapshot['arm_chips']}")
    if snapshot.get("hpcc_chip"):
        parts.append(f"HPCC: {snapshot['hpcc_chip']}")
    return "; ".join(parts)


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


# ---------------------------------------------------------------------------
# Row selection
# ---------------------------------------------------------------------------

def _release_rows(
    conn: sqlite3.Connection,
    release: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Rows for the release note.

    Preview and final Markdown both include only apps that currently qualify
    (decision=release, owner-confirmed, no doc-gate items).  Unfinished apps
    stay visible in missing_items, not generated Markdown.
    """
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    rows = []
    for app_id, snapshot in release["snapshots"].items():
        app = apps.get(app_id)
        if not app or snapshot.get("release_decision") != "release":
            continue
        if not gates.qualifies_for_docs(snapshot):
            continue
        rows.append((app_view(app, snapshot), snapshot))
    return sorted(rows, key=lambda item: item[0]["name"].lower())


def _guide_rows(
    conn: sqlite3.Connection,
    release: dict[str, Any],
    doc_target: str,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Return current release-decision app rows for a manual/ai4sci guide."""
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    stopped: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for app_id, snapshot in release["snapshots"].items():
        app = apps.get(app_id)
        if not app:
            continue
        if normalize_doc_target(snapshot.get("doc_target")) != doc_target:
            continue
        if gates.qualifies_for_docs(snapshot):
            active.append((app_view(app, snapshot), snapshot))

    active.sort(key=lambda item: item[0]["name"].lower())
    return active, stopped


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_release_note(release: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    out = md_title(f"MACA HPC 发布列表 - {release['name']}")
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
        out += "| " + " | ".join(md_cell(c) for c in cells) + " |\n"
    out += "\n"
    return out


def _render_guide_entries(rows: list[tuple[dict[str, Any], dict[str, Any]]], *, stopped: bool = False) -> str:
    out = ""
    for app, snapshot in rows:
        doc = snapshot.get("doc", {})
        heading = f"{app['name']}（已停止支持）" if stopped else app["name"]
        out += md_title(heading, 2)
        out += f"{doc.get('intro') or app.get('description') or ''}\n\n"
        out += f"版本：{snapshot.get('version') or ''}\n\n"
        if app.get("official_url"):
            out += f"官方网址：{app['official_url']}\n\n"
        out += "**镜像使用方法：**\n\n" + owner_markdown_block(doc.get("image_usage", ""))
        out += "**二进制包使用方法：**\n\n" + owner_markdown_block(doc.get("binary_usage", ""))
        out += "**环境搭建：**\n\n" + owner_markdown_block(doc.get("env_setup", ""))
        out += "**测试方法：**\n\n"
        for test_doc in snapshot.get("test_docs", []):
            if test_doc.get("obsolete"):
                continue
            out += f"- {test_doc['path']}\n"
            out += guide_test_doc_field("测试内容", test_doc.get("content", ""), quote=True)
            if test_doc.get("command"):
                out += guide_test_doc_field("测试命令", inline_code(test_doc["command"]))
            out += guide_test_doc_field("测试数据集", test_doc.get("dataset", ""), quote=True)
            out += guide_test_doc_field("结果查看", test_doc.get("result_view", ""), quote=True)
            out += guide_test_doc_field("通过标准", test_doc.get("pass_criteria", ""), quote=True)
            out += "\n"
        limits = _merged_limitations_with_qa_note(snapshot)
        if limits:
            out += f"**已知限制：**\n\n{limits}\n\n"
    return out


def _render_guide(
    title: str,
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    stopped_rows: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> str:
    out = md_title(title)
    out += _render_guide_entries(rows)
    if stopped_rows:
        out += _render_guide_entries(stopped_rows, stopped=True)
    return markdown_fences_on_new_lines(out)


def _is_non_release_decision(snapshot: dict[str, Any]) -> bool:
    return normalize_release_decision(snapshot.get("release_decision")) != "release"


def _render_manager_review_csv(
    conn: sqlite3.Connection,
    release: dict[str, Any],
    fields: list[str] | None = None,
) -> str:
    """Render Manager Review CSV with FastAPI non-release row handling."""
    field_labels = dict(MANAGER_REVIEW_FIELDS)
    selected = fields or DEFAULT_MANAGER_REVIEW_FIELDS
    if not selected:
        raise ValueError("至少选择一个输出字段")
    invalid = [field for field in selected if field not in field_labels]
    if invalid:
        raise ValueError(f"未知 Manager Review 字段: {', '.join(invalid)}")

    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    display_names = users_repo.display_name_map(conn)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([field_labels[field] for field in selected])
    rows: list[tuple[bool, dict[str, Any], dict[str, Any]]] = []
    for app_id, snapshot in release["snapshots"].items():
        app = apps.get(app_id)
        if not app:
            continue
        rows.append((_is_non_release_decision(snapshot), app_view(app, snapshot), snapshot))
    rows.sort(key=lambda item: (1 if item[0] else 0, item[1].get("name", "").lower()))

    for is_non_release, app, snapshot in rows:
        values = {
            "app_name": app.get("name", ""),
            "official_name": app.get("official_name", ""),
            "doc_target": "AI4Sci" if normalize_doc_target(app.get("doc_target")) == "ai4sci" else "HPC",
            "app_type": app.get("type", ""),
            "version": snapshot.get("version", ""),
            "owners": _owner_display_text(app.get("owners", []), display_names),
            "chip_support": _chip_support_text(snapshot),
            "qa_issue_note": snapshot.get("qa_issue_note", ""),
            "x86_chips": snapshot.get("x86_chips", ""),
            "arm_chips": snapshot.get("arm_chips", ""),
            "release_decision": normalize_release_decision(snapshot.get("release_decision")),
            "qa_status": snapshot.get("qa_status", "not_checked"),
            "owner_confirmed": "是" if snapshot.get("owner_confirmed") else "否",
            "releasable": "是" if gates.qualifies_for_final(snapshot) else "否",
            "not_releasable_reason": gates.not_releasable_reason(snapshot),
            "known_limitations": _owner_limitations(snapshot),
            "gerrit_url": app.get("git_url", ""),
            "git_branch": app.get("git_branch", ""),
        }
        if is_non_release:
            for field in _NON_RELEASE_MANAGER_REVIEW_BLANK_FIELDS:
                values[field] = ""
            values["not_releasable_reason"] = "不发布"
        writer.writerow([values[field] for field in selected])
    return out.getvalue()


# ---------------------------------------------------------------------------
# CSV filename helpers
# ---------------------------------------------------------------------------

def _csv_filename_component(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", text).strip("._")
    return text or "release"


def _csv_filename_timestamp(generated_at: str | None = None) -> str:
    if generated_at:
        try:
            parsed = dt.datetime.fromisoformat(generated_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=BEIJING_TZ)
            return parsed.astimezone(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
        except ValueError:
            pass
    return beijing_now().strftime("%Y%m%d_%H%M%S")


def artifact_csv_filename(prefix: str, release: dict[str, Any], generated_at: str | None = None) -> str:
    version = _csv_filename_component(release.get("name") or release.get("maca_version") or release.get("id"))
    return f"{prefix}_{version}_{_csv_filename_timestamp(generated_at)}.csv"


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------

def _upsert_artifact(
    conn: sqlite3.Connection,
    release_id: str,
    kind: str,
    *,
    name: str,
    content: str,
    final: bool,
    generated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(release_id, kind, name, content, final, generated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id, kind) DO UPDATE SET
          name=excluded.name,
          content=excluded.content,
          final=excluded.final,
          generated_at=excluded.generated_at
        """,
        (release_id, kind, name, content, int(final), generated_at),
    )


def _generate_artifacts(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    final: bool = False,
    from_lock: bool = False,
) -> dict[str, str]:
    """(Re)generate the four release artifacts — mirrors core.py:generate_artifacts."""
    from app.services import app_service

    release = release_reads.get_release(conn, release_id)
    if final and not from_lock:
        raise RuntimeError("Final artifacts 只能通过 final_lock_release 生成")
    if from_lock and not final:
        raise RuntimeError("Lock generation must create final artifacts")
    if from_lock and not release.get("released_locked"):
        raise RuntimeError("Final artifacts require a locked release")
    if release.get("released_locked") and not from_lock:
        raise RuntimeError("Release 已最终锁定，artifacts 不可重新生成")
    if final:
        existing = conn.execute(
            "SELECT 1 FROM artifacts WHERE release_id = ? AND final = 1 LIMIT 1", (release_id,)
        ).fetchone()
        if existing:
            raise RuntimeError("Final artifacts already exist and are immutable")
    ts = beijing_timestamp()
    with transaction(conn):
        if not final:
            app_service.refresh_missing_items(conn, release_id)
            release = release_reads.get_release(conn, release_id)
        rows = _release_rows(conn, release)
        manual_active, manual_stopped = _guide_rows(conn, release, "manual")
        ai4sci_active, ai4sci_stopped = _guide_rows(conn, release, "ai4sci")
        artifacts = {
            "release_note": _render_release_note(release, rows),
            "manual": _render_guide("HPC Manual App 章节", manual_active, manual_stopped),
            "ai4sci": _render_guide("AI4Sci User Guide App 章节", ai4sci_active, ai4sci_stopped),
            "data": dumps_json({
                "release": release,
                "apps": apps_repo.list_apps(conn),
                "generated_at": ts,
                "final": final,
            }),
        }
        names = {
            "release_note": "release_note.md",
            "manual": "hpc_manual_apps.md",
            "ai4sci": "ai4sci_user_guide_apps.md",
            "data": "release_data.json",
        }
        for kind, content in artifacts.items():
            _upsert_artifact(
                conn, release_id, kind,
                name=names[kind], content=content, final=final, generated_at=ts,
            )
        log_audit(
            conn,
            "生成最终 artifacts" if final else "刷新文档 artifacts",
            ts=ts,
            release_id=release_id,
            event="generate_artifacts",
        )
    return artifacts


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------

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

    Mirrors server.py:722-730 with the FastAPI release-note rules.
    Raises AuthzError if the caller is not RM or Owner.
    Returns dict of kind→content (same as core.generate_artifacts).
    """
    from app.api.errors import AuthzError

    if role not in {"RM", "Owner"}:
        raise AuthzError("只有 RM、Owner 可刷新发布文档")

    # final=True is blocked at router level; service enforces the same check
    return _generate_artifacts(conn, release_id, final=False)


def final_lock_release(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict[str, str]:
    """Final lock: freeze all writes, generate final artifacts.

    Mirrors core.py:final_lock_release with the FastAPI artifact rules
    (locked_in_release marking uses the docs gate, not the QA gate).
    """
    from app.services import app_service, cicd_service

    cicd_service.ensure_no_open_deferred_release_decision_for_release(conn, release_id)
    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定")
    ts = beijing_timestamp()
    with transaction(conn):
        app_service.refresh_missing_items(conn, release_id)
        release = release_reads.get_release(conn, release_id)
        for app_id, snapshot in release["snapshots"].items():
            if gates.qualifies_for_docs(snapshot):
                snapshot["locked_in_release"] = True
            snapshots_repo.save_snapshot(conn, release_id, app_id, snapshot)
        releases_repo.lock_release(conn, release_id, locked_at=ts, locked_by=user)
        artifacts = _generate_artifacts(conn, release_id, final=True, from_lock=True)
        log_audit(
            conn,
            f"Release 最终锁定：{release['name']}",
            ts=ts,
            user=user,
            role=role,
            release_id=release_id,
            event="final_lock",
        )
    return artifacts


def generate_manager_review(
    conn: sqlite3.Connection,
    release_id: str,
    fields: list[str] | None = None,
    *,
    user: str,
    role: str,
) -> str:
    """Generate (and persist) the manager-review CSV.

    Mirrors server.py:732-742 with the FastAPI CSV ordering rules.
    Returns the CSV content string.
    """
    from app.services import app_service

    release = release_reads.get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，Manager Review CSV 不可重新生成")
    with transaction(conn):
        app_service.refresh_missing_items(conn, release_id)
        release = release_reads.get_release(conn, release_id)
        generated_at = beijing_timestamp()
        content = _render_manager_review_csv(conn, release, fields)
        artifact_name = artifact_csv_filename("manager_review", release, generated_at)
        _upsert_artifact(
            conn, release_id, "manager_review",
            name=artifact_name, content=content, final=False, generated_at=generated_at,
        )
        log_audit(
            conn,
            "生成 Manager Review CSV",
            ts=generated_at,
            user=user,
            role=role,
            release_id=release_id,
            event="generate_manager_review",
        )
    return content


def get_test_scope_csv(
    conn: sqlite3.Connection,
    release_id: str,
) -> tuple[str, str]:
    """Return (csv_text, filename) for the test-scope CSV.

    Mirrors server.py:402-413 and core.py:export_test_scope_csv.
    """
    release = release_reads.get_release(conn, release_id)
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    display_names = users_repo.display_name_map(conn)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["app_name", "version", "gerrit_url", "branch", "owners"])
    rows = []
    for app_id, snapshot in release["snapshots"].items():
        if snapshot.get("release_decision") != "release":
            continue
        app = apps.get(app_id)
        if not app:
            continue
        view = app_view(app, snapshot)
        rows.append(
            (
                view["name"],
                snapshot.get("version", ""),
                view["git_url"],
                view["git_branch"],
                _owner_display_text(view["owners"], display_names),
            )
        )
    rows.sort(key=lambda r: r[0].lower())
    for row in rows:
        writer.writerow(row)
    filename = f"test_scope_{release['name']}.csv"
    return out.getvalue(), filename


def gerrit_push_plan(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    user: str,
    role: str,
) -> dict:
    """Return the Gerrit push plan for the given release.

    Mirrors server.py:744-747 and core.py:gerrit_push_plan.
    """
    release = release_reads.get_release(conn, release_id)
    if not release.get("released_locked"):
        raise RuntimeError("Gerrit push 要求 release 已最终锁定")
    docs_remote = os.environ.get("HPC_DOCS_GERRIT_REMOTE", "")
    data_remote = os.environ.get("HPC_RELEASE_DATA_GERRIT_REMOTE", "")
    if not docs_remote or not data_remote:
        return {
            "ready": False,
            "reason": "Missing HPC_DOCS_GERRIT_REMOTE or HPC_RELEASE_DATA_GERRIT_REMOTE",
            "required_env": ["HPC_DOCS_GERRIT_REMOTE", "HPC_RELEASE_DATA_GERRIT_REMOTE"],
        }
    branch = f"release-{release['name']}"
    return {
        "ready": True,
        "docs_remote": docs_remote,
        "data_remote": data_remote,
        "branch": branch,
        "commands": [
            f"git clone {docs_remote} docs-worktree",
            f"git -C docs-worktree checkout -b {branch}",
            "copy generated Markdown artifacts into docs-worktree",
            f"git -C docs-worktree push origin HEAD:refs/for/{branch}",
            f"git clone {data_remote} release-data-worktree",
            f"git -C release-data-worktree checkout -b {branch}",
            "copy release_data.json into release-data-worktree",
            f"git -C release-data-worktree push origin HEAD:refs/for/{branch}",
        ],
    }
