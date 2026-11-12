"""Release documents for a monthly cycle.

* ``release_note`` — only the Apps released in this cycle.  Before
  publication it previews the entries whose QA verdict is releasable.
* ``manual`` / ``ai4sci`` — cumulative guides covering the current official
  version of every running App, refreshed each month.  Before publication the
  preview assumes this cycle's releasable entries are published.
* ``data`` — machine-readable JSON of the cycle.

Draft documents can be regenerated until publication; publication writes the
final, immutable set (``generate_final_artifacts``).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import sqlite3
from typing import Any

from app.api.errors import AuthzError
from app.db.connection import transaction
from app.domain import cycles as cycles_domain
from app.domain import manager_review, qa
from app.domain.app_profile import display_name, normalize_doc_target
from app.domain.markdown import (
    guide_test_doc_field,
    inline_code,
    markdown_fences_on_new_lines,
    md_cell,
    md_title,
    owner_markdown_block,
)
from app.domain.permissions import has_capability, roles_for_tab
from app.domain.textutil import join_list
from app.repositories import (
    app_info_repo,
    apps_repo,
    entries_repo,
    official_repo,
    releases_repo,
    users_repo,
)
from app.repositories.audit_repo import log_audit
from app.repositories.base import dumps_json
from app.services import catalog_service, cycle_service
from app.timeutil import BEIJING_TZ, beijing_now, beijing_timestamp

ARTIFACT_NAMES = {
    "release_note": "release_note.md",
    "manual": "hpc_manual_apps.md",
    "ai4sci": "ai4sci_user_guide_apps.md",
    "data": "release_data.json",
}
PUBLIC_KINDS = frozenset(ARTIFACT_NAMES)


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------


def _owner_display_text(owners: list[str] | None, display_names: dict[str, str]) -> str:
    return ",".join(display_names.get(o, o) for o in owners or [] if str(o).strip())


def _chips(summary: dict[str, Any] | None) -> tuple[str, str]:
    summary = summary or {}
    return join_list(summary.get("x86_chips", [])), join_list(summary.get("arm_chips", []))


def _chip_support_text(summary: dict[str, Any] | None) -> str:
    x86, arm = _chips(summary)
    parts = []
    if x86:
        parts.append(f"X86: {x86}")
    if arm:
        parts.append(f"ARM: {arm}")
    return "; ".join(parts)


def _entry_rows(conn: sqlite3.Connection, release: dict[str, Any]) -> list[dict[str, Any]]:
    """Entries that appear in the release note (released, or releasable before publish)."""
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    rows = []
    for entry in entries_repo.list_for_release(conn, release["id"]):
        if release["released_locked"]:
            if entry["result"] != cycles_domain.RESULT_RELEASED:
                continue
        elif not qa.is_releasable(entry["qa_status"]):
            continue
        app = apps.get(entry["app_id"])
        if not app:
            continue
        summary = catalog_service.version_summary(app_info_repo.get_version(conn, entry["locked_version_id"]))
        known = entry["known_issues"]
        if not release["released_locked"]:
            known = cycles_domain.known_issues_at_publish(known, entry["qa_status"], entry["qa_note"])
        rows.append({"app": app, "entry": entry, "summary": summary, "known_issues": known})
    rows.sort(key=lambda row: catalog_service.app_name(row["app"]).lower())
    return rows


def _guide_rows(conn: sqlite3.Connection, release: dict[str, Any], doc_target: str) -> list[dict[str, Any]]:
    """Running Apps of *doc_target* with their (prospective) official version."""
    officials = official_repo.current_by_app(conn)
    prospective: dict[str, dict[str, Any]] = {}
    if not release["released_locked"]:
        for row in _entry_rows(conn, release):
            prospective[row["app"]["id"]] = row
    rows = []
    for app in apps_repo.list_apps(conn):
        if app["cicd_status"] != cycles_domain.CICD_RUNNING:
            continue
        if normalize_doc_target(app.get("doc_target")) != doc_target:
            continue
        if app["id"] in prospective:
            row = prospective[app["id"]]
            version, version_id, known = row["entry"]["target_version"], row["entry"]["locked_version_id"], row["known_issues"]
        elif app["id"] in officials:
            official = officials[app["id"]]
            version, version_id = official["version"], official["app_info_version_id"]
            entry = entries_repo.get_entry(conn, official["entry_id"]) if official.get("entry_id") else None
            known = (entry or {}).get("known_issues", "")
        else:
            continue
        summary = catalog_service.version_summary(app_info_repo.get_version(conn, version_id))
        rows.append({"app": app, "version": version, "summary": summary, "known_issues": known})
    rows.sort(key=lambda row: catalog_service.app_name(row["app"]).lower())
    return rows


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _render_release_note(release: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    out = md_title(f"MACA HPC 发布列表 - {release['name']}")
    headers = ["名称", "类型", "描述", "版本", "X86支持芯片系列", "ARM支持芯片类型", "本期交付", "已知问题"]
    out += "| " + " | ".join(headers) + " |\n"
    out += "| " + " | ".join("---" for _ in headers) + " |\n"
    for row in rows:
        app, entry = row["app"], row["entry"]
        x86, arm = _chips(row["summary"])
        cells = [
            display_name(catalog_service.app_name(app), entry["target_version"]),
            app.get("type") or "",
            app.get("description") or "",
            entry["target_version"],
            x86,
            arm,
            entry["actual_delivery"] or entry["planned_features"],
            row["known_issues"],
        ]
        out += "| " + " | ".join(md_cell(c) for c in cells) + " |\n"
    return out + "\n"


def _render_guide(title: str, rows: list[dict[str, Any]]) -> str:
    out = md_title(title)
    for row in rows:
        app = row["app"]
        doc = app.get("doc") or {}
        out += md_title(display_name(catalog_service.app_name(app), row["version"]), 2)
        out += f"{doc.get('intro') or app.get('description') or ''}\n\n"
        out += f"版本：{row['version']}\n\n"
        if app.get("official_url"):
            out += f"官方网址：{app['official_url']}\n\n"
        out += "**镜像使用方法：**\n\n" + owner_markdown_block(doc.get("image_usage", ""))
        out += "**二进制包使用方法：**\n\n" + owner_markdown_block(doc.get("binary_usage", ""))
        out += "**环境搭建：**\n\n" + owner_markdown_block(doc.get("env_setup", ""))
        out += "**测试方法：**\n\n"
        test_docs = {d.get("path"): d for d in app.get("test_docs") or []}
        for test in (row["summary"] or {}).get("tests", []):
            test_doc = test_docs.get(test["path"], {})
            out += f"- {test['path']}\n"
            out += guide_test_doc_field("测试内容", test_doc.get("content", ""), quote=True)
            if test.get("command"):
                out += guide_test_doc_field("测试命令", inline_code(test["command"]))
            out += guide_test_doc_field("测试数据集", test_doc.get("dataset", ""), quote=True)
            out += guide_test_doc_field("结果查看", test_doc.get("result_view", ""), quote=True)
            out += guide_test_doc_field("通过标准", test_doc.get("pass_criteria", ""), quote=True)
            out += "\n"
        limits = "\n\n".join(part for part in ((doc.get("limitations") or "").strip(), row["known_issues"].strip()) if part)
        if limits:
            out += f"**已知限制：**\n\n{limits}\n\n"
    return markdown_fences_on_new_lines(out)


def _render_data(conn: sqlite3.Connection, release: dict[str, Any], ts: str, final: bool) -> str:
    return dumps_json(
        {
            "release": cycle_service.release_detail(conn, release["id"]),
            "entries": cycle_service.list_entry_views(conn, release["id"], include_withdrawn=False),
            "apps": catalog_service.list_app_views(conn),
            "generated_at": ts,
            "final": final,
        }
    )


def _render_all(conn: sqlite3.Connection, release: dict[str, Any], ts: str, final: bool) -> dict[str, str]:
    return {
        "release_note": _render_release_note(release, _entry_rows(conn, release)),
        "manual": _render_guide("HPC Manual App 章节", _guide_rows(conn, release, "manual")),
        "ai4sci": _render_guide("AI4Sci User Guide App 章节", _guide_rows(conn, release, "ai4sci")),
        "data": _render_data(conn, release, ts, final),
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _upsert_artifact(conn, release_id, kind, *, name, content, final, generated_at) -> None:
    conn.execute(
        """
        INSERT INTO artifacts(release_id, kind, name, content, final, generated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(release_id, kind) DO UPDATE SET
          name=excluded.name, content=excluded.content,
          final=excluded.final, generated_at=excluded.generated_at
        """,
        (release_id, kind, name, content, int(final), generated_at),
    )


def generate_final_artifacts(conn: sqlite3.Connection, release_id: str) -> dict[str, str]:
    """Final documents; called by cycle_service.publish inside its transaction."""
    release = cycle_service.get_release(conn, release_id)
    if not release["released_locked"]:
        raise RuntimeError("Final artifacts require a published release")
    ts = beijing_timestamp()
    artifacts = _render_all(conn, release, ts, True)
    for kind, content in artifacts.items():
        _upsert_artifact(conn, release_id, kind, name=ARTIFACT_NAMES[kind], content=content,
                         final=True, generated_at=ts)
    return artifacts


def generate_artifacts(conn: sqlite3.Connection, release_id: str, *, user: str, role: str) -> dict[str, str]:
    """Regenerate the draft documents of an unpublished cycle."""
    if not has_capability(role, "artifact.generate"):
        raise AuthzError("只有 RM、Owner 可刷新发布文档")
    release = cycle_service.get_release(conn, release_id)
    if release["released_locked"]:
        raise RuntimeError("版本已发布，发布文档不可重新生成")
    ts = beijing_timestamp()
    with transaction(conn):
        artifacts = _render_all(conn, release, ts, False)
        for kind, content in artifacts.items():
            _upsert_artifact(conn, release_id, kind, name=ARTIFACT_NAMES[kind], content=content,
                             final=False, generated_at=ts)
        log_audit(conn, "刷新发布文档", ts=ts, user=user, role=role, release_id=release_id,
                  event="generate_artifacts")
    return artifacts


def get_artifact(conn: sqlite3.Connection, release_id: str, kind: str, *, role: str) -> dict:
    if role not in roles_for_tab("artifacts"):
        raise AuthzError("无权访问发布文档")
    if not has_capability(role, "artifact.view.internal") and kind not in PUBLIC_KINDS:
        raise AuthzError("只有 RM 可查看该 artifact")
    row = conn.execute(
        "SELECT name, content, generated_at FROM artifacts WHERE release_id = ? AND kind = ?",
        (release_id, kind),
    ).fetchone()
    if not row:
        raise KeyError(f"artifact not found: {kind}")
    return dict(row)


# ---------------------------------------------------------------------------
# CSV exports
# ---------------------------------------------------------------------------


def _csv_filename_component(value: str | None) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(value or "").strip()).strip("._")
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
    version = _csv_filename_component(release.get("name") or release.get("id"))
    return f"{prefix}_{version}_{_csv_filename_timestamp(generated_at)}.csv"


def _render_manager_review_csv(conn: sqlite3.Connection, release: dict[str, Any], fields: list[str] | None) -> str:
    labels = manager_review.FIELD_LABELS
    selected = list(fields or manager_review.DEFAULT_FIELDS)
    if not selected:
        raise ValueError("至少选择一个输出字段")
    invalid = [field for field in selected if field not in labels]
    if invalid:
        raise ValueError(f"未知 Manager Review 字段: {', '.join(invalid)}")
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    images = {image["id"]: image for image in releases_repo.list_base_images(conn, release["id"])}
    display_names = users_repo.display_name_map(conn)
    result_labels = {"": "待发布", "released": "已发布", "failed": "发布失败"}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([labels[field] for field in selected])
    rows = []
    for entry in entries_repo.list_for_release(conn, release["id"]):
        app = apps.get(entry["app_id"])
        if app:
            rows.append((catalog_service.app_name(app).lower(), app, entry))
    for _, app, entry in sorted(rows, key=lambda item: item[0]):
        summary = catalog_service.version_summary(app_info_repo.get_version(conn, entry["locked_version_id"]))
        values = {
            "app_name": display_name(catalog_service.app_name(app), entry["target_version"]),
            "official_name": app.get("official_name", ""),
            "doc_target": "AI4Sci" if normalize_doc_target(app.get("doc_target")) == "ai4sci" else "HPC",
            "app_type": app.get("type", ""),
            "target_version": entry["target_version"],
            "owners": _owner_display_text(app.get("owners"), display_names),
            "chip_support": _chip_support_text(summary),
            "base_image": (images.get(entry["base_image_id"]) or {}).get("name", ""),
            "planned_features": entry["planned_features"],
            "actual_delivery": entry["actual_delivery"],
            "qa_status": qa.qa_status_label(entry["qa_status"]),
            "qa_note": entry["qa_note"],
            "known_issues": entry["known_issues"],
            "result": result_labels.get(entry["result"], entry["result"]),
            "deliverable_image": entry["deliverable_image"],
            "deliverable_digest": entry["deliverable_digest"],
            "gerrit_url": app.get("git_url", ""),
            "git_branch": app.get("git_branch", ""),
        }
        writer.writerow([values[field] for field in selected])
    return out.getvalue()


def generate_manager_review(
    conn: sqlite3.Connection,
    release_id: str,
    fields: list[str] | None = None,
    *,
    user: str,
    role: str,
) -> str:
    release = cycle_service.get_release(conn, release_id)
    generated_at = beijing_timestamp()
    with transaction(conn):
        content = _render_manager_review_csv(conn, release, fields)
        _upsert_artifact(conn, release_id, "manager_review",
                         name=artifact_csv_filename("manager_review", release, generated_at),
                         content=content, final=False, generated_at=generated_at)
        log_audit(conn, "生成 Manager Review CSV", ts=generated_at, user=user, role=role,
                  release_id=release_id, event="generate_manager_review")
    return content


def get_test_scope_csv(conn: sqlite3.Connection, release_id: str) -> tuple[str, str]:
    """One row per (entry, in-scope test) of the cycle's active signups."""
    release = cycle_service.get_release(conn, release_id)
    apps = {app["id"]: app for app in apps_repo.list_apps(conn)}
    display_names = users_repo.display_name_map(conn)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["app_name", "version", "gerrit_url", "branch", "owners", "test", "chips", "command"])
    for view in sorted(
        cycle_service.list_entry_views(conn, release_id, include_withdrawn=False),
        key=lambda v: catalog_service.app_name(apps.get(v["app_id"], {"id": v["app_id"]})).lower(),
    ):
        app = apps.get(view["app_id"])
        if not app:
            continue
        base = [
            catalog_service.app_name(app),
            view["target_version"],
            app["git_url"],
            app["git_branch"],
            _owner_display_text(app.get("owners"), display_names),
        ]
        tests = [t for t in view["scope_tests"] if not t["excluded"]] or [None]
        for test in tests:
            if test is None:
                writer.writerow([*base, "", "", ""])
            else:
                writer.writerow([*base, test["path"], join_list(test["supported_chips"]), test["command"]])
    return out.getvalue(), f"test_scope_{release['name']}.csv"
