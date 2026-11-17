"""Freeze the current published component scope, then collect App-level QA verdicts."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from collections import OrderedDict
from copy import deepcopy

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain.permissions import has_capability
from app.domain.shared_metadata import mapping_section
from app.domain.test_commands import image_tag_version
from app.repositories import app_info_repo, apps_repo, official_repo
from app.repositories import component_compatibility_repo as repo
from app.repositories.audit_repo import log_audit
from app.services import lookup_service
from app.timeutil import beijing_timestamp

# Labels and versions are short identifiers; comments may include multiple failures.
MAX_LABEL_LENGTH = 120
MAX_COMMENT_LENGTH = 10000
# These runtime components apply to every App in the compatibility inventory.
UNIVERSAL_COMPONENTS = tuple(mapping_section("universal_components"))
# One new-report form prepares its scope once; normal lookups never use this.
PREVIEW_TTL_SECONDS = 300
MAX_PREVIEWS = 8
_previews = OrderedDict()
_preview_lock = threading.Lock()


def _edit(role):
    if not has_capability(role, "qa.edit"):
        raise AuthzError("只有 QA 或 RM 可填写组件兼容性结论")


def _label(value, name):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > MAX_LABEL_LENGTH:
        raise ValueError(f"{name}须为 1–{MAX_LABEL_LENGTH} 个字符")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{name}不能包含换行或控制字符")
    return value.strip()


def _source_revision(conn):
    apps = [{key: app[key] for key in ("id", "git_url", "git_branch", "cicd_status", "official_name")}
            for app in apps_repo.list_apps(conn)]
    value = [apps, official_repo.current_by_app(conn), app_info_repo.latest_version_ids(conn)]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _inventory(conn):
    rows = lookup_service.latest_running_releases(conn)["rows"]
    errors = [row for row in rows if row.get("note", "").startswith("交付镜像解析失败")]
    if errors:
        details = "；".join(dict.fromkeys(f'{row["app_id"]}：{row["note"]}' for row in errors))
        raise ApiError(502, f"无法生成完整组件清单，未创建记录。请刷新组件列表重试；若仍失败，请在兼容性验证页检查。具体原因：{details}")
    return rows


def components(conn, *, user="", role=""):
    revision = _source_revision(conn)
    rows = _inventory(conn)
    if _source_revision(conn) != revision:
        raise ApiError(409, "发布或 App 配置在生成组件列表期间发生变化，请刷新组件列表")
    groups = {name: {row["app_id"] for row in rows} for name in UNIVERSAL_COMPONENTS}
    for row in rows:
        if not row.get("release_id") or row.get("manifest_source") == "unpublished":
            continue
        for name in row.get("dependencies", []):
            if name.casefold() in UNIVERSAL_COMPONENTS:
                continue
            groups.setdefault(name, set()).add(row["app_id"])
    token = secrets.token_urlsafe(24)
    now = time.monotonic()
    with _preview_lock:
        for key in list(_previews):
            if now - _previews[key]["created"] >= PREVIEW_TTL_SECONDS:
                del _previews[key]
        while len(_previews) >= MAX_PREVIEWS:
            _previews.popitem(last=False)
        _previews[token] = {"created": now, "owner": (user, role), "revision": revision,
                            "rows": deepcopy([{key: value for key, value in row.items() if key not in ("command", "note")}
                                              for row in rows])}
    return {"components": [{"component": name, "app_count": len(ids), "all_apps": name in UNIVERSAL_COMPONENTS}
                           for name, ids in sorted(groups.items())], "preview_id": token}


def _prepared_scope(conn, token, *, user, role):
    if token is None:
        revision = _source_revision(conn)
        return revision, _inventory(conn)
    if not isinstance(token, str) or len(token) > 100:
        raise ValueError("组件清单凭据无效，请刷新组件列表")
    with _preview_lock:
        preview = _previews.get(token)
        if (not preview or preview["owner"] != (user, role)
                or time.monotonic() - preview["created"] >= PREVIEW_TTL_SECONDS):
            raise ApiError(409, "本次组件清单已失效，请刷新组件列表后重新创建")
        return preview["revision"], preview["rows"]


def _snapshots(rows, component):
    apps = {}
    for row in rows:
        if component not in UNIVERSAL_COMPONENTS and (
            not row.get("release_id") or row.get("manifest_source") == "unpublished"
            or component not in row.get("dependencies", [])
        ):
            continue
        pattern = row.get("image_pattern", "").partition(":")[2]
        tag = row.get("delivery_reference", "").partition("@")[0].rsplit("/", 1)[-1].partition(":")[2]
        delivered_version = image_tag_version(pattern, tag) if pattern and tag else None
        if delivered_version and re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?", delivered_version):
            row = {**row, "hpc_version": delivered_version}
        app = apps.setdefault(row["app_id"], {
            "app_id": row["app_id"], "app_name": row["app_name"], "git_branch": row["git_branch"],
            "release_id": row["release_id"], "app_versions": [], "hpc_versions": [], "tests": [], "baselines": [],
            "scope_note": "尚无成功发布记录" if row.get("manifest_source") == "unpublished" else "",
        })
        for source, target in (("app_version", "app_versions"), ("hpc_version", "hpc_versions")):
            if row.get(source) and row[source] not in app[target]:
                app[target].append(row[source])
        baseline = {key: row.get(key, "") for key in (
            "official_version_id", "app_info_commit_id", "publication_app_info_commit_id", "delivery_reference",
            "delivery_build_key", "os", "arch", "python_label", "pytorch_label", "image_target")}
        if baseline not in app["baselines"]:
            app["baselines"].append(baseline)
        if not row.get("path"):
            continue
        test = next((item for item in app["tests"] if item["path"] == row["path"]), None)
        if test is None:
            test = {"path": row["path"], "command": "", "platforms": []}
            app["tests"].append(test)
        platform = next((item for item in test["platforms"]
                         if (item["os"], item["arch"]) == (row.get("os", ""), row.get("arch", ""))), None)
        if platform is None:
            platform = {"os": row.get("os", ""), "arch": row.get("arch", ""), "chips": []}
            test["platforms"].append(platform)
        for chip in row.get("chips") or ([row["GPU"]] if row.get("GPU") else []):
            if chip not in platform["chips"]:
                platform["chips"].append(chip)
    for app in apps.values():
        app["app_version"] = ", ".join(app.pop("app_versions"))
        app["hpc_version"] = ", ".join(app.pop("hpc_versions"))
    return sorted(apps.values(), key=lambda item: item["app_id"])


def list_reports(conn):
    return {"reports": repo.list_reports(conn)}


def get_report(conn, report_id):
    report = repo.get_report(conn, report_id)
    if not report:
        raise ApiError(404, "组件兼容性记录不存在")
    return {**report, "apps": repo.list_apps(conn, report_id)}


def create(conn, body, *, user, role):
    _edit(role)
    component = _label(body.get("component"), "组件")
    if component.casefold() in UNIVERSAL_COMPONENTS:
        component = component.casefold()
    version = _label(body.get("component_version"), "组件版本")
    if repo.find_report(conn, component, version):
        raise ApiError(409, "该组件版本的记录已存在，请选择已有记录填写")
    revision, rows = _prepared_scope(conn, body.get("preview_id"), user=user, role=role)
    snapshots = _snapshots(rows, component)
    if not snapshots:
        raise ValueError("该组件暂无可用 App，请刷新组件列表")
    timestamp = beijing_timestamp()
    with transaction(conn, immediate=True):
        if _source_revision(conn) != revision:
            raise ApiError(409, "发布或 App 配置在生成清单期间发生变化，请刷新组件列表后重新创建")
        if repo.find_report(conn, component, version):
            raise ApiError(409, "该组件版本的记录已存在，请选择已有记录填写")
        report_id = repo.create(conn, component, version, snapshots, timestamp=timestamp, user=user)
        log_audit(conn, f"新建组件兼容性记录：{component} {version}", ts=timestamp, user=user, role=role,
                  event="component_compatibility_create", detail={"report_id": report_id, "app_count": len(snapshots)})
    with _preview_lock:
        _previews.pop(body.get("preview_id"), None)
    return get_report(conn, report_id)


def save_app(conn, report_id, app_id, body, *, user, role):
    _edit(role)
    status = body.get("qa_status")
    comment = body.get("qa_comment", "")
    revision = body.get("revision")
    if status not in ("pass", "fail"):
        raise ValueError("QA 结论只允许 pass 或 fail")
    if not isinstance(comment, str) or len(comment) > MAX_COMMENT_LENGTH:
        raise ValueError(f"comment 须为不超过 {MAX_COMMENT_LENGTH} 个字符的文字")
    if type(revision) is not int or revision < 0:
        raise ValueError("请提供有效的记录 revision，刷新后重试")
    timestamp = beijing_timestamp()
    with transaction(conn, immediate=True):
        app = repo.get_app(conn, report_id, app_id)
        if not app:
            raise ApiError(404, "该记录中不存在此 App")
        if not repo.update_app(conn, report_id, app_id, status=status, comment=comment.strip(), revision=revision,
                               timestamp=timestamp, user=user):
            raise ApiError(409, "该 App 的兼容性结论已被其他人修改，请刷新后重新保存；当前草稿尚未保存")
        log_audit(conn, f"填写组件兼容性结论：{app_id} · {status}", ts=timestamp, user=user, role=role,
                  app_id=app_id, event="component_compatibility_save",
                  detail={"report_id": report_id, "status": status, "comment": comment.strip()})
    return {"app": repo.get_app(conn, report_id, app_id)}
