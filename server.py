from __future__ import annotations

import base64
import json
import io
import mimetypes
import os
import secrets
import subprocess
import tarfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from release_system import core


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "release_system.db"
ADMIN_PASSWORD_FILE = ROOT / "admin_password.local"


class AuthzError(Exception):
    """Authenticated user lacks permission for an action -> HTTP 403.

    Kept distinct from PermissionError ("not logged in" -> HTTP 401) so the
    frontend only drops the session on genuine authentication loss.
    """


class Handler(BaseHTTPRequestHandler):
    server_version = "HPCReleaseSystem/0.2"

    def conn(self):
        """Lazily open a per-request connection that is closed in finally."""
        if not hasattr(self, "_conn") or self._conn is None:
            self._conn = core.connect(DB_PATH)
        return self._conn

    def _close_conn(self) -> None:
        c = getattr(self, "_conn", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
            self._conn = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            try:
                if parsed.path == "/api/me":
                    user = self.current_user(required=False)
                    self.send_json({"user": user})
                    return
                if parsed.path == "/api/state":
                    self.current_user()
                    self.send_json(self.state_payload())
                    return
                if parsed.path == "/api/app-audit":
                    self.current_user()
                    app_id = self.query().get("app_id", [""])[0]
                    if not app_id:
                        raise ValueError("app_id is required")
                    release_id = self.query().get("release_id", [""])[0]
                    self.send_json({"entries": core.app_audit_log(self.conn(), app_id, release_id)})
                    return
                if parsed.path == "/api/test-scope.csv":
                    self.require_rm()
                    release_id = self.query().get("release_id", [""])[0]
                    if not release_id:
                        raise ValueError("release_id is required")
                    csv_text = core.export_test_scope_csv(self.conn(), release_id)
                    release = core.get_release(self.conn(), release_id)
                    filename = f"test_scope_{release['name']}.csv"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8-sig")
                    self.send_header("Content-Disposition", f"attachment; filename={filename}")
                    self.end_headers()
                    self.wfile.write("﻿".encode("utf-8") + csv_text.encode("utf-8"))
                    return
                if parsed.path == "/api/qa-log/download":
                    self.current_user()
                    release_id = self.query().get("release_id", [""])[0]
                    if not release_id:
                        raise ValueError("release_id is required")
                    meta = core.get_qa_log(self.conn(), release_id)
                    if not meta:
                        self.send_error(404, "no qa log")
                        return
                    path = Path(meta["storage_path"])
                    if not path.exists():
                        self.send_error(404, "qa log file missing")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f"attachment; filename={meta['filename']}")
                    self.end_headers()
                    self.wfile.write(path.read_bytes())
                    return
                if parsed.path == "/api/qa-reports":
                    self.current_user()
                    release_id = self.query().get("release_id", [""])[0]
                    if not release_id:
                        raise ValueError("release_id is required")
                    self.send_json(core.build_qa_reports(self.conn(), release_id))
                    return
                if parsed.path.startswith("/api/artifacts/"):
                    self.require_rm()
                    kind = parsed.path.rsplit("/", 1)[-1]
                    release_id = self.query().get("release_id", [""])[0]
                    row = self.conn().execute("SELECT name, content FROM artifacts WHERE release_id = ? AND kind = ?", (release_id, kind)).fetchone()
                    if not row:
                        self.send_error(404, "artifact not found")
                        return
                    self.send_response(200)
                    content_type = "text/csv; charset=utf-8-sig" if row["name"].lower().endswith(".csv") else "text/plain; charset=utf-8"
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Disposition", f"attachment; filename={row['name']}")
                    self.end_headers()
                    if row["name"].lower().endswith(".csv"):
                        self.wfile.write("﻿".encode("utf-8"))
                    self.wfile.write(row["content"].encode("utf-8"))
                    return
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, status=401)
                return
            except AuthzError as exc:
                self.send_json({"error": str(exc)}, status=403)
                return
            except Exception as exc:
                self.log_error("GET %s: %s", parsed.path, exc)
                self.send_json({"error": str(exc)}, status=500)
                return
            self.serve_static(parsed.path)
        finally:
            self._close_conn()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            try:
                if parsed.path == "/api/login":
                    body = self.json_body()
                    token = core.authenticate(self.conn(), body.get("username", ""), body.get("password", ""))
                    if not token:
                        raise PermissionError("Invalid username or password")
                    self.send_json({"ok": True}, cookies=[f"hpc_session={token}; HttpOnly; SameSite=Strict; Path=/"])
                    return
                if parsed.path == "/api/logout":
                    core.logout_session(self.conn(), self.session_token())
                    self.send_json({"ok": True}, cookies=["hpc_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"])
                    return
                if parsed.path == "/api/admin/clear-db":
                    self.require_admin()
                    body = self.json_body()
                    if body.get("confirm") not in {"清空数据库", "CLEAR_DATABASE"}:
                        raise RuntimeError("确认文本必须是：清空数据库 或 CLEAR_DATABASE")
                    username = self.user()
                    role = self.role()
                    self._close_conn()  # release before backup copy
                    backup = backup_database()
                    core.clear_business_data(self.conn(), user=username, role=role)
                    self.send_json({"ok": True, "backup": backup.name})
                    return
                if parsed.path == "/api/import-initial":
                    self.require_rm()
                    body = self.json_body()
                    release_id = core.import_initial_rows(
                        self.conn(),
                        core.parse_csv_text(body.get("csv", "")),
                        release_name=body.get("release_name") or None,
                        maca_version=body.get("maca_version") or None,
                        app_freeze_deadline=body.get("app_freeze_deadline", ""),
                        doc_deadline=body.get("doc_deadline", ""),
                    )
                    self.send_json({"release_id": release_id})
                    return
                if parsed.path == "/api/releases/create":
                    self.require_rm()
                    body = self.json_body()
                    release_id = core.create_release_from_previous(
                        self.conn(),
                        body["name"],
                        maca_version=body.get("maca_version", ""),
                        app_freeze_deadline=body.get("app_freeze_deadline", ""),
                        doc_deadline=body.get("doc_deadline", ""),
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"release_id": release_id})
                    return
                if parsed.path == "/api/releases/deadlines":
                    self.require_rm()
                    body = self.json_body()
                    release = core.update_release_deadlines(
                        self.conn(),
                        body["release_id"],
                        name=body.get("name"),
                        app_freeze_deadline=body.get("app_freeze_deadline"),
                        doc_deadline=body.get("doc_deadline"),
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"release": _serialize_release(release)})
                    return
                if parsed.path == "/api/releases/final-lock":
                    self.require_rm()
                    body = self.json_body()
                    artifacts = core.final_lock_release(self.conn(), body["release_id"], user=self.user(), role=self.role())
                    self.send_json({"artifacts": list(artifacts)})
                    return
                if parsed.path == "/api/releases/final-unlock":
                    self.require_rm()
                    body = self.json_body()
                    core.final_unlock_release(self.conn(), body["release_id"], user=self.user(), role=self.role())
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/artifacts/generate":
                    self.require_rm()
                    body = self.json_body()
                    if body.get("final"):
                        raise RuntimeError("Final artifacts 只能通过最终 lock 生成")
                    artifacts = core.generate_artifacts(self.conn(), body["release_id"], final=False)
                    self.send_json({"artifacts": list(artifacts)})
                    return
                if parsed.path == "/api/artifacts/manager-review":
                    self.require_rm()
                    body = self.json_body()
                    content = core.generate_manager_review_csv(
                        self.conn(),
                        body["release_id"],
                        body.get("fields") or None,
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"artifact": "manager_review", "bytes": len(content.encode("utf-8"))})
                    return
                if parsed.path == "/api/gerrit/plan":
                    self.require_rm()
                    body = self.json_body()
                    self.send_json(core.gerrit_push_plan(self.conn(), body["release_id"]))
                    return
                if parsed.path == "/api/admin/apps/delete":
                    self.require_admin()
                    body = self.json_body()
                    if body.get("confirm") != body.get("app_id"):
                        raise RuntimeError("删除确认必须等于 app_id")
                    self._close_conn()
                    backup = backup_database()
                    deleted = core.delete_app(self.conn(), body["app_id"], user=self.user(), role=self.role())
                    self.send_json({"ok": True, "deleted": deleted, "backup": backup.name})
                    return
                if parsed.path == "/api/apps/new":
                    if self.role() != "Owner":
                        raise AuthzError("Only Owner can submit new app requests")
                    body = self.json_body()
                    app_id = core.add_new_app_request(
                        self.conn(),
                        body["release_id"],
                        official_name=body["official_name"],
                        git_url=body["git_url"],
                        git_branch=body["git_branch"],
                        release_decision=body["release_decision"],
                        owner=self.user(),
                        doc_target=body.get("doc_target", "manual"),
                    )
                    self.send_json({"app_id": app_id})
                    return
                if parsed.path == "/api/apps/update":
                    body = self.json_body()
                    conn = self.conn()
                    aid = body["app_id"]
                    rid = body["release_id"]
                    app = core.get_app(conn, aid)
                    release = core.get_release(conn, rid)
                    if release.get("released_locked"):
                        raise RuntimeError("Release 已最终锁定")
                    snap_now = release["snapshots"].get(aid, {})
                    self.require_owner_or_rm(snap_now.get("owners"))
                    role = self.role()
                    actor = self.user()
                    snap_update = body.get("snapshot", {})
                    past_doc_deadline = not core.is_before(release.get("doc_deadline", ""))
                    if past_doc_deadline:
                        if "app" in body or any(key != "release_decision" for key in snap_update):
                            raise RuntimeError("已过 doc deadline，只能下调 release 决策，不能再修改文档/表单/app_info")
                    current_decision = snap_now.get("release_decision", "release")
                    new_decision = snap_update.get("release_decision")
                    if new_decision is not None:
                        new_decision = core.normalize_release_decision(new_decision)
                        if new_decision != current_decision:
                            if new_decision == "release" and not core.is_before(release.get("app_freeze_deadline", "")):
                                raise RuntimeError("已过 app 冻结 deadline，不可再切换为 release")
                    if "app" in body and role == "RM":
                        app_update = body["app"]
                        repo_before = {"git_url": app.get("git_url", ""), "git_branch": app.get("git_branch", "")}
                        repo_changed = False
                        for key in ("git_url", "git_branch"):
                            if key in app_update and app.get(key) != app_update[key]:
                                app[key] = app_update[key]
                                repo_changed = True
                        if repo_changed:
                            core.save_app(conn, app)
                            core.audit(conn, "修改 Gerrit 信息", user=actor, role=role,
                                       app_id=aid, release_id=rid, event="update_app_repo",
                                       detail=core.field_diff(repo_before, app, {"git_url": "Gerrit URL", "git_branch": "Branch"}),
                                       commit=False)

                    owner_meta = {"type", "official_url", "description"}
                    doc_labels = {"intro": "基本介绍", "image_usage": "镜像使用方法", "binary_usage": "二进制包使用方法", "env_setup": "环境搭建", "limitations": "已知限制"}

                    def mutate(snapshot: dict) -> None:
                        name_for_msg = snapshot.get("official_name") or aid
                        if "release_decision" in snap_update:
                            decision = core.normalize_release_decision(snap_update["release_decision"])
                            if decision not in core.RELEASE_DECISIONS:
                                raise ValueError(f"Invalid release_decision: {snap_update['release_decision']}")
                            if decision != snapshot.get("release_decision"):
                                core.audit(conn, f"修改 release 决策：{name_for_msg} {snapshot.get('release_decision')} -> {decision}",
                                           user=actor, role=role, app_id=aid, release_id=rid, event="update_release_decision",
                                           detail=core.field_diff({"release_decision": snapshot.get("release_decision")},
                                                                  {"release_decision": decision},
                                                                  {"release_decision": "release 决策"}),
                                           commit=False)
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
                                value = sorted({str(o).strip() for o in (value or []) if str(o).strip()})
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
                            core.audit(conn, f"修改 app 基本信息：{name_for_msg}", user=actor, role=role,
                                       app_id=aid, release_id=rid, event="update_app_meta",
                                       detail=core.field_diff(meta_before, meta_after, core.APP_META_LABELS),
                                       commit=False)
                        if "owner_confirmed" in snap_update:
                            if role != "Owner":
                                raise AuthzError("Owner confirmation must be submitted by an Owner")
                            if snap_update["owner_confirmed"] and not snapshot.get("owner_confirmed"):
                                core.audit(conn, f"提交 Owner 确认：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="owner_confirm",
                                           detail=[{"field": "owner_confirmed", "label": "Owner 确认", "old": "未确认", "new": "已确认"}],
                                           commit=False)
                            snapshot["owner_confirmed"] = snap_update["owner_confirmed"]
                        if "doc" in snap_update:
                            doc_update = snap_update["doc"]
                            current_doc = snapshot.get("doc", {})
                            doc_changes = core.field_diff(current_doc, doc_update, {k: doc_labels.get(k, k) for k in doc_update})
                            if doc_changes:
                                core.audit(conn, f"修改文档字段：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_doc_fields", detail=doc_changes,
                                           commit=False)
                            snapshot.setdefault("doc", {}).update(doc_update)
                        if "community" in snap_update:
                            comm_update = snap_update["community"]
                            comm_labels = {"release_status": "社区发布情况", "python_version": "社区包 Python 版本", "framework_version": "社区包框架及版本"}
                            comm_before = {k: (snapshot.get("community") or {}).get(k, "") for k in comm_labels}
                            comm_changes = core.field_diff(comm_before, comm_update, comm_labels)
                            if comm_changes:
                                core.audit(conn, f"修改社区发布信息：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_community", detail=comm_changes,
                                           commit=False)
                            snapshot.setdefault("community", {}).update(comm_update)
                        if "sanity" in snap_update:
                            sanity_update = snap_update["sanity"]
                            sanity_labels = {"arm_kylin": "ARM / Kylin Sanity", "ubuntu": "Ubuntu / 兼容性 Sanity"}
                            sanity_before = {k: bool((snapshot.get("sanity") or {}).get(k)) for k in sanity_labels}
                            sanity_changes = core.field_diff(sanity_before, sanity_update, sanity_labels)
                            if sanity_changes:
                                if role != "RM":
                                    raise AuthzError("仅 RM 可修改 Sanity 信息")
                                core.audit(conn, f"修改 Sanity 信息：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_sanity", detail=sanity_changes,
                                           commit=False)
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
                                core.audit(conn, f"修改测试说明：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_test_docs", detail=td_changes,
                                           commit=False)

                    # Every audit above runs commit=False: the repo change, the
                    # field audits and the snapshot save all land in one
                    # transaction here, so a mid-request failure leaves nothing.
                    updated = core.update_snapshot(conn, rid, aid, mutate, skip_doc_deadline=past_doc_deadline)
                    updated["missing_items"] = core.missing_items_for(core.get_app(conn, aid), updated)
                    core.save_snapshot(conn, rid, aid, updated)
                    conn.commit()
                    response = {"snapshot": updated, "missing_items": updated.get("missing_items", []), "qa_status": updated.get("qa_status")}
                    if body.get("sync_decision") and snap_now.get("release_decision") != updated.get("release_decision"):
                        response["decision_sync"] = core.sync_decision_to_later_releases(
                            conn, rid, aid, updated.get("release_decision"), user=actor, role=role
                        )
                    self.send_json(response)
                    return
                if parsed.path == "/api/qa/status-batch":
                    if self.role() not in {"QA", "RM"}:
                        raise AuthzError("只有 QA 或 RM 可标注 QA 状态")
                    body = self.json_body()
                    updated = core.qa_set_status_batch(
                        self.conn(),
                        body["release_id"],
                        body.get("items") or [],
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"ok": True, "updated": len(updated)})
                    return
                if parsed.path == "/api/qa/upload-log":
                    if self.role() not in {"QA", "RM"}:
                        raise AuthzError("只有 QA 或 RM 可上传 QA log")
                    body = self.json_body()
                    content_b64 = body.get("content_base64", "")
                    if not content_b64:
                        raise ValueError("content_base64 required")
                    content = base64.b64decode(content_b64)
                    meta = core.qa_upload_log(
                        self.conn(),
                        DB_PATH,
                        body["release_id"],
                        content,
                        body.get("filename", "qa_log"),
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"ok": True, **meta})
                    return
                if parsed.path == "/api/app-info":
                    body = self.json_body()
                    conn = self.conn()
                    release = core.get_release(conn, body["release_id"])
                    snap = release["snapshots"].get(body["app_id"], {})
                    self.require_owner_or_rm(snap.get("owners"))
                    snapshot = core.apply_app_info(
                        conn,
                        body["release_id"],
                        body["app_id"],
                        body["app_info"],
                        source=body.get("source", "owner upload"),
                        source_type="owner_upload",
                        uploaded_by=self.user(),
                    )
                    self.send_json({"snapshot": snapshot})
                    return
                if parsed.path == "/api/app-info/fetch":
                    body = self.json_body()
                    conn = self.conn()
                    app = core.get_app(conn, body["app_id"])
                    release = core.get_release(conn, body["release_id"])
                    snap = release["snapshots"].get(body["app_id"], {})
                    self.require_owner_or_rm(snap.get("owners"))
                    raw, commit_id = fetch_app_info_from_gerrit(app["git_url"], app["git_branch"])
                    snapshot = core.apply_app_info(
                        conn,
                        body["release_id"],
                        body["app_id"],
                        raw,
                        source=f"{app['git_url']} {app['git_branch']}:app_info.json",
                        source_type="gerrit_fetch",
                        commit_id=commit_id,
                        uploaded_by=self.user(),
                    )
                    self.send_json({"snapshot": snapshot, "commit_id": commit_id, "source": snapshot.get("app_info", {}).get("source", "")})
                    return
                if parsed.path == "/api/app-info/fetch-all":
                    self.require_rm()
                    body = self.json_body()
                    results = fetch_all_app_infos_from_gerrit(self.conn(), body["release_id"], uploaded_by=self.user())
                    self.send_json(results)
                    return
            except PermissionError as exc:
                self.send_json({"error": str(exc)}, status=401)
                return
            except AuthzError as exc:
                self.send_json({"error": str(exc)}, status=403)
                return
            except Exception as exc:
                self.log_error("POST %s: %s", parsed.path, exc)
                self.send_json({"error": str(exc)}, status=400)
                return
            self.send_error(404, "unknown endpoint")
        finally:
            self._close_conn()

    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    def json_body(self) -> dict:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size).decode("utf-8") if size else "{}"
        return json.loads(raw or "{}")

    def state_payload(self) -> dict:
        conn = self.conn()
        user = self.current_user()
        releases = core.list_releases(conn)
        release_id = self.query().get("release_id", [releases[-1]["id"] if releases else ""])[0]
        apps = core.list_apps(conn)
        payload = {
            "apps": apps,
            "releases": [_serialize_release(r) for r in releases],
            "release": None,
            "artifacts": [],
            "user": user,
            "qa_log": None,
        }
        if release_id:
            core.refresh_missing_items(conn, release_id)
            release = core.get_release(conn, release_id)
            payload["release"] = _serialize_release(release)
            payload["artifacts"] = [dict(row) for row in conn.execute("SELECT kind, name, final, generated_at FROM artifacts WHERE release_id = ?", (release_id,))]
            payload["qa_log"] = core.get_qa_log(conn, release_id)
        return payload

    def send_json(self, payload: dict, status: int = 200, cookies: list[str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for cookie in cookies or []:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path: str) -> None:
        rel = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (ROOT / rel).resolve()
        try:
            target.relative_to(ROOT)
        except ValueError:
            self.send_error(403, "forbidden")
            return
        if not target.exists() or target.is_dir():
            self.send_error(404, "not found")
            return
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def session_token(self) -> str:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if part.strip().startswith("hpc_session="):
                return part.strip().split("=", 1)[1]
        return ""

    def current_user(self, *, required: bool = True) -> dict[str, str] | None:
        user = core.session_user(self.conn(), self.session_token())
        if required and not user:
            raise PermissionError("Login required")
        return user

    def user(self) -> str:
        return self.current_user()["username"]

    def role(self) -> str:
        return self.current_user()["role"]

    def require_rm(self) -> None:
        if self.role() != "RM":
            raise AuthzError("RM role required")

    def require_admin(self) -> None:
        if self.role() != "Admin":
            raise AuthzError("Admin role required")

    def require_owner_or_rm(self, owners: list[str] | None) -> None:
        if self.role() == "RM":
            return
        if self.role() == "Owner" and self.user() in (owners or []):
            return
        raise AuthzError("Owner permission required")


def _serialize_release(release: dict) -> dict:
    out = dict(release)
    out["released_locked"] = bool(out.get("released_locked"))
    out["phase"] = core.current_phase(out)
    return out


def read_admin_password_file() -> str:
    if not ADMIN_PASSWORD_FILE.exists():
        return ""
    for line in ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    return ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()


def ensure_admin_user(conn) -> str:
    if conn.execute("SELECT 1 FROM users WHERE username = ?", ("admin",)).fetchone():
        return ""
    password = os.environ.get("HPC_ADMIN_PASSWORD", "").strip()
    source = "HPC_ADMIN_PASSWORD"
    if not password:
        password = read_admin_password_file()
        source = str(ADMIN_PASSWORD_FILE)
    if not password:
        password = secrets.token_urlsafe(24)
        ADMIN_PASSWORD_FILE.write_text(f"username=admin\npassword={password}\n", encoding="utf-8")
        source = str(ADMIN_PASSWORD_FILE)
    core.create_user(conn, "admin", password, "Admin")
    return source


def backup_database() -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = ROOT / f"release_system_admin_backup_{stamp}.sqlite"
    if DB_PATH.exists():
        core.backup_sqlite(DB_PATH, backup)
    return backup


def run_git(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=True)


HPC_GERRIT_PREFIX = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/"
HPC_GERRIT_ROOT = "ssh://sw-gerrit-devops.metax-internal.com:29418/"


def gerrit_remote_url(git_url: str) -> str:
    git_url = (git_url or "").strip()
    if git_url.startswith(("ssh://", "http://", "https://", "git@")):
        return git_url
    project = git_url.lstrip("/")
    if project.startswith("PDE/HPC/"):
        return f"{HPC_GERRIT_ROOT}{project}"
    return f"{HPC_GERRIT_PREFIX}{project}"


def fetch_app_info_from_gerrit(git_url: str, branch: str) -> tuple[str, str]:
    if not git_url or not branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    remote_url = gerrit_remote_url(git_url)
    try:
        ref = run_git(["git", "ls-remote", remote_url, branch])
        line = ref.stdout.decode("utf-8", errors="replace").splitlines()[0]
        commit_id = line.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError) as exc:
        raise RuntimeError(f"无法获取 Gerrit commit id: {exc}") from exc
    try:
        archive = run_git(["git", "archive", f"--remote={remote_url}", commit_id, "app_info.json"], timeout=120)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
            member = tar.getmember("app_info.json")
            extracted = tar.extractfile(member)
            if not extracted:
                raise RuntimeError("archive 中 app_info.json 为空")
            return extracted.read().decode("utf-8"), commit_id
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, tarfile.TarError, KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"无法从 Gerrit 拉取 app_info.json: {exc}") from exc


def fetch_all_app_infos_from_gerrit(conn, release_id: str, *, uploaded_by: str) -> dict:
    release = core.get_release(conn, release_id)
    results = []
    for app_id in sorted(release.get("snapshots", {})):
        try:
            app = core.get_app(conn, app_id)
            raw, commit_id = fetch_app_info_from_gerrit(app["git_url"], app["git_branch"])
            snapshot = core.apply_app_info(
                conn,
                release_id,
                app_id,
                raw,
                source=f"{app['git_url']} {app['git_branch']}:app_info.json",
                source_type="gerrit_fetch",
                commit_id=commit_id,
                uploaded_by=uploaded_by,
            )
            results.append({
                "app_id": app_id,
                "ok": True,
                "commit_id": commit_id,
                "source": snapshot.get("app_info", {}).get("source", ""),
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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    conn = core.connect(DB_PATH)
    admin_source = ensure_admin_user(conn)
    conn.close()
    if admin_source:
        print(f"Admin user created. Password source: {admin_source}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
