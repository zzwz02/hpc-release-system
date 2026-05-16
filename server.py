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
                    self.send_json({"entries": core.app_audit_log(self.conn(), app_id)})
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
                        core.parse_csv_text(body.get("release_csv", "")),
                        core.parse_csv_text(body.get("owner_csv", "")),
                        alias_text=body.get("alias_text", ""),
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
                    app = core.get_app(conn, body["app_id"])
                    self.require_owner_or_rm(app)
                    release = core.get_release(conn, body["release_id"])
                    if release.get("released_locked"):
                        raise RuntimeError("Release 已最终锁定")
                    past_doc_deadline = not core.is_before(release.get("doc_deadline", ""))
                    if past_doc_deadline:
                        snap_update = body.get("snapshot", {})
                        if "app" in body or any(key != "release_decision" for key in snap_update):
                            raise RuntimeError("已过 doc deadline，只能下调 release 决策，不能再修改文档/表单/app_info")
                    snap_now = release["snapshots"].get(body["app_id"], {})
                    current_decision = snap_now.get("release_decision", "release")
                    new_decision = body.get("snapshot", {}).get("release_decision")
                    if new_decision is not None:
                        new_decision = core.normalize_release_decision(new_decision)
                        if new_decision != current_decision:
                            if new_decision == "release" and not core.is_before(release.get("app_freeze_deadline", "")):
                                raise RuntimeError("已过 app 冻结 deadline，不可再切换为 release")
                    if "app" in body and self.role() in {"RM", "Owner"}:
                        app_update = body["app"]
                        editable_keys = ["official_name", "type", "description", "git_url", "git_branch", "doc_target", "owners"] if self.role() == "RM" else ["type", "description"]
                        before_app_meta = {
                            key: app.get(key)
                            for key in ["official_name", "type", "description", "git_url", "git_branch", "doc_target", "owners"]
                        }
                        for key in editable_keys:
                            if key in app_update:
                                if key == "doc_target":
                                    app[key] = core.normalize_doc_target(app_update[key])
                                elif key == "description":
                                    app[key] = core.normalize_app_description(app_update[key])
                                elif key == "type":
                                    app[key] = (app_update[key] or "").strip()
                                else:
                                    app[key] = app_update[key]
                        if self.role() == "RM" and "owners" in app_update:
                            app["owners"] = app_update["owners"]
                        after_app_meta = {
                            key: app.get(key)
                            for key in ["official_name", "type", "description", "git_url", "git_branch", "doc_target", "owners"]
                        }
                        if before_app_meta != after_app_meta:
                            core.save_app(conn, app)
                            core.audit(
                                conn,
                                f"修改 app 元数据：{app['name']}",
                                user=self.user(),
                                role=self.role(),
                                app_id=app["id"],
                                release_id=body["release_id"],
                                event="update_app_meta",
                            )

                    def mutate(snapshot: dict) -> None:
                        snap_update = body.get("snapshot", {})
                        if "release_decision" in snap_update:
                            decision = core.normalize_release_decision(snap_update["release_decision"])
                            if decision not in core.RELEASE_DECISIONS:
                                raise ValueError(f"Invalid release_decision: {snap_update['release_decision']}")
                            if decision != snapshot.get("release_decision"):
                                core.audit(
                                    conn,
                                    f"修改 release 决策：{app['name']} {snapshot.get('release_decision')} -> {decision}",
                                    user=self.user(),
                                    role=self.role(),
                                    app_id=app["id"],
                                    release_id=body["release_id"],
                                    event="update_release_decision",
                                )
                            snapshot["release_decision"] = decision
                        if "owner_confirmed" in snap_update:
                            if self.role() != "Owner":
                                raise AuthzError("Owner confirmation must be submitted by an Owner")
                            if snap_update["owner_confirmed"] and not snapshot.get("owner_confirmed"):
                                core.audit(
                                    conn,
                                    f"提交 Owner 确认：{app['name']}",
                                    user=self.user(),
                                    role=self.role(),
                                    app_id=app["id"],
                                    release_id=body["release_id"],
                                    event="owner_confirm",
                                )
                            snapshot["owner_confirmed"] = snap_update["owner_confirmed"]
                        if "doc" in snap_update:
                            doc_update = snap_update["doc"]
                            current_doc = snapshot.get("doc", {})
                            if any((current_doc.get(key) or "") != (value or "") for key, value in doc_update.items()):
                                core.audit(
                                    conn,
                                    f"修改文档字段：{app['name']}",
                                    user=self.user(),
                                    role=self.role(),
                                    app_id=app["id"],
                                    release_id=body["release_id"],
                                    event="update_doc_fields",
                                )
                            snapshot.setdefault("doc", {}).update(doc_update)
                        if "diff_confirm_all" in snap_update and snap_update["diff_confirm_all"]:
                            if self.role() != "Owner":
                                raise AuthzError("app_info diff confirmation must be submitted by an Owner")
                            had_unconfirmed_diff = any(not diff.get("confirmed") for diff in snapshot.get("app_info_diffs", []))
                            for diff in snapshot.get("app_info_diffs", []):
                                diff["confirmed"] = True
                            if had_unconfirmed_diff:
                                core.audit(
                                    conn,
                                    f"确认 app_info diff：{app['name']}",
                                    user=self.user(),
                                    role=self.role(),
                                    app_id=app["id"],
                                    release_id=body["release_id"],
                                    event="confirm_app_info_diff",
                                )
                        if "test_docs" in snap_update:
                            before_test_docs = json.dumps(snapshot.get("test_docs", []), sort_keys=True, ensure_ascii=False)
                            by_id = {doc["id"]: doc for doc in snapshot.get("test_docs", [])}
                            for item in snap_update["test_docs"]:
                                if item.get("id") in by_id:
                                    by_id[item["id"]].update(item)
                                elif item.get("owner_added"):
                                    item.setdefault("id", core.new_id("testdoc"))
                                    item.setdefault("path", f"owner_added.{len(by_id) + 1}")
                                    snapshot.setdefault("test_docs", []).append(item)
                            after_test_docs = json.dumps(snapshot.get("test_docs", []), sort_keys=True, ensure_ascii=False)
                            if before_test_docs != after_test_docs:
                                core.audit(
                                    conn,
                                    f"修改测试说明：{app['name']}",
                                    user=self.user(),
                                    role=self.role(),
                                    app_id=app["id"],
                                    release_id=body["release_id"],
                                    event="update_test_docs",
                                )

                    updated = core.update_snapshot(conn, body["release_id"], body["app_id"], mutate, skip_doc_deadline=past_doc_deadline)
                    updated["missing_items"] = core.missing_items_for(core.get_app(conn, body["app_id"]), updated)
                    core.save_snapshot(conn, body["release_id"], body["app_id"], updated)
                    conn.commit()
                    self.send_json({"snapshot": updated, "missing_items": updated.get("missing_items", []), "qa_status": updated.get("qa_status")})
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
                    app = core.get_app(conn, body["app_id"])
                    self.require_owner_or_rm(app)
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
                    self.require_owner_or_rm(app)
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
        if user["role"] == "Owner":
            apps = [app for app in apps if user["username"] in app.get("owners", [])]
        payload = {
            "apps": apps,
            "releases": [_serialize_release(r) for r in releases],
            "release": None,
            "artifacts": [],
            "user": user,
            "qa_log": None,
            "server_now_beijing": core.beijing_now().strftime("%Y-%m-%d %H:%M"),
        }
        if release_id:
            core.refresh_missing_items(conn, release_id)
            release = core.get_release(conn, release_id)
            if user["role"] == "Owner":
                visible = {app["id"] for app in apps}
                release["snapshots"] = {app_id: snap for app_id, snap in release["snapshots"].items() if app_id in visible}
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

    def require_owner_or_rm(self, app: dict) -> None:
        if self.role() == "RM":
            return
        if self.role() == "Owner" and self.user() in app.get("owners", []):
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


def fetch_app_info_from_gerrit(git_url: str, branch: str) -> tuple[str, str]:
    if not git_url or not branch:
        raise RuntimeError("Gerrit URL 和 branch 不能为空")
    try:
        ref = run_git(["git", "ls-remote", git_url, branch])
        line = ref.stdout.decode("utf-8", errors="replace").splitlines()[0]
        commit_id = line.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError) as exc:
        raise RuntimeError(f"无法获取 Gerrit commit id: {exc}") from exc
    try:
        archive = run_git(["git", "archive", f"--remote={git_url}", commit_id, "app_info.json"], timeout=120)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tar:
            member = tar.getmember("app_info.json")
            extracted = tar.extractfile(member)
            if not extracted:
                raise RuntimeError("archive 中 app_info.json 为空")
            return extracted.read().decode("utf-8"), commit_id
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, tarfile.TarError, KeyError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"无法从 Gerrit 拉取 app_info.json: {exc}") from exc


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
