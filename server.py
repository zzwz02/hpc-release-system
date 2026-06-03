from __future__ import annotations

import base64
import json
import io
import mimetypes
import os
import secrets
import subprocess
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    import ldap3
    from ldap3 import Server as LdapServer, Connection as LdapConn, ALL as LDAP_ALL, SIMPLE as LDAP_SIMPLE, SUBTREE as LDAP_SUBTREE
    from ldap3.core.exceptions import LDAPException
    _LDAP3_AVAILABLE = True
except ImportError:
    _LDAP3_AVAILABLE = False

from release_system import core
from release_system import jira_client


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "release_system.db"
ADMIN_PASSWORD_FILE = ROOT / "admin_password.local"
LDAP_CONF_PATH = ROOT / "ldap.conf"

# Populated at startup by main(); read-only after that (no lock needed).
_LDAP_CONFIG: dict = {"enabled": False}

_QA_ANALYSIS_LOCK = threading.Lock()
_QA_ANALYSIS_JOBS: dict[str, dict] = {}
_QA_ANALYSIS_TTL_SECONDS = 3600


def _load_ldap_config() -> dict:
    """Parse ldap.conf into a plain dict.

    Handles multi-word values (e.g. passwords with '=') by splitting only on
    the first '=' per line.  Returns a safe default (disabled) if the file is
    missing or unreadable.
    """
    defaults: dict = {
        "enabled": False,
        "uri": "",
        "base": "",
        "binddn": "",
        "bindpw": "",
        "user_filter": "(&(objectClass=user)(sAMAccountName={uid}))",
        "uid_attr": "sAMAccountName",
        "name_attr": "displayName",
        "timeout": 10,
    }
    if not LDAP_CONF_PATH.exists():
        return defaults
    cfg = dict(defaults)
    for line in LDAP_CONF_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if key == "enabled":
            cfg["enabled"] = value.lower() in ("true", "1", "yes")
        elif key in cfg:
            cfg[key] = value
    try:
        cfg["timeout"] = int(cfg["timeout"])
    except (ValueError, TypeError):
        cfg["timeout"] = 10
    return cfg


def ldap_authenticate(username: str, password: str) -> tuple[str, str]:
    """Verify *username*/*password* against LDAP.  Returns (username, display_name).

    Raises PermissionError for wrong credentials, RuntimeError for config /
    connectivity problems.  The two-step flow:
      1. Bind with the service account to locate the user's full DN.
      2. Bind as that DN with the supplied password to verify.
    """
    cfg = _LDAP_CONFIG
    if not cfg.get("enabled"):
        raise RuntimeError("LDAP 登录未启用")
    if not _LDAP3_AVAILABLE:
        raise RuntimeError("服务器未安装 ldap3 依赖（pip install ldap3）")
    if not username or not password:
        raise PermissionError("用户名和密码不能为空")

    # Escape LDAP special chars in username to prevent filter injection
    safe_uid = (
        username
        .replace("\\", "\\5c")
        .replace("(",  "\\28")
        .replace(")",  "\\29")
        .replace("*",  "\\2a")
    )
    search_filter = cfg["user_filter"].replace("{uid}", safe_uid)

    server = LdapServer(cfg["uri"], get_info=LDAP_ALL, connect_timeout=cfg["timeout"])

    # Step 1: service-account bind to find user DN
    try:
        svc = LdapConn(
            server,
            user=cfg["binddn"],
            password=cfg["bindpw"],
            authentication=LDAP_SIMPLE,
            auto_bind=True,
            receive_timeout=cfg["timeout"],
        )
    except LDAPException as exc:
        raise RuntimeError(f"LDAP 服务账号连接失败：{exc}") from exc

    svc.search(
        cfg["base"],
        search_filter,
        search_scope=LDAP_SUBTREE,
        attributes=[cfg["uid_attr"], cfg["name_attr"]],
    )
    entries = svc.entries
    svc.unbind()

    if not entries:
        raise PermissionError(f"域账号不存在：{username}")

    entry = entries[0]
    user_dn = entry.entry_dn
    try:
        display_name = str(entry[cfg["name_attr"]].value or username)
    except Exception:
        display_name = username

    # Step 2: user-password bind to verify credentials
    try:
        uconn = LdapConn(
            server,
            user=user_dn,
            password=password,
            authentication=LDAP_SIMPLE,
            auto_bind=True,
            receive_timeout=cfg["timeout"],
        )
        uconn.unbind()
    except LDAPException:
        raise PermissionError("域账号密码不正确")

    return username, display_name


def _qa_analysis_now() -> float:
    return time.time()


def _cleanup_qa_analysis_jobs() -> None:
    cutoff = _qa_analysis_now() - _QA_ANALYSIS_TTL_SECONDS
    with _QA_ANALYSIS_LOCK:
        stale = [
            job_id
            for job_id, job in _QA_ANALYSIS_JOBS.items()
            if job.get("status") != "running" and float(job.get("updated_at") or 0) < cutoff
        ]
        for job_id in stale:
            _QA_ANALYSIS_JOBS.pop(job_id, None)


def _qa_job_snapshot(job: dict) -> dict:
    payload = {
        "job_id": job["job_id"],
        "release_id": job["release_id"],
        "status": job["status"],
        "stage": job.get("stage", ""),
        "message": job.get("message", ""),
        "started_at": job.get("started_at", 0),
        "updated_at": job.get("updated_at", 0),
        "finished_at": job.get("finished_at", 0),
    }
    if job.get("error"):
        payload["error"] = job["error"]
    if job.get("token_count") is not None:
        payload["token_count"] = job.get("token_count", 0)
    if job.get("result") is not None:
        payload["result"] = job["result"]
    return payload


def _update_qa_analysis_job(job_id: str, **updates) -> None:
    updates["updated_at"] = _qa_analysis_now()
    with _QA_ANALYSIS_LOCK:
        job = _QA_ANALYSIS_JOBS.get(job_id)
        if job:
            job.update(updates)


def _run_qa_analysis_job(job_id: str, release_id: str) -> None:
    conn = None
    try:
        conn = core.connect(DB_PATH)

        def progress(stage: str, message: str, **extra) -> None:
            _update_qa_analysis_job(job_id, stage=stage, message=message, **extra)

        result = core.qa_analyze_log(conn, DB_PATH, release_id, progress=progress)
        _update_qa_analysis_job(
            job_id,
            status="completed",
            stage="completed",
            message="AI 分析完成",
            result=result,
            finished_at=_qa_analysis_now(),
        )
    except Exception as exc:
        _update_qa_analysis_job(
            job_id,
            status="failed",
            stage="failed",
            message=f"AI 分析失败：{exc}",
            error=str(exc),
            finished_at=_qa_analysis_now(),
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def start_qa_analysis_job(release_id: str, user: str, role: str) -> dict:
    _cleanup_qa_analysis_jobs()
    job_id = secrets.token_urlsafe(12)
    now = _qa_analysis_now()
    job = {
        "job_id": job_id,
        "release_id": release_id,
        "user": user,
        "role": role,
        "status": "running",
        "stage": "queued",
        "message": "AI 分析任务已提交",
        "started_at": now,
        "updated_at": now,
        "finished_at": 0,
        "token_count": 0,
        "result": None,
        "error": "",
    }
    with _QA_ANALYSIS_LOCK:
        _QA_ANALYSIS_JOBS[job_id] = job
    thread = threading.Thread(target=_run_qa_analysis_job, args=(job_id, release_id), daemon=True)
    thread.start()
    return _qa_job_snapshot(job)


def get_qa_analysis_job(job_id: str, user: str, role: str) -> dict | None:
    _cleanup_qa_analysis_jobs()
    with _QA_ANALYSIS_LOCK:
        job = _QA_ANALYSIS_JOBS.get(job_id)
        if not job:
            return None
        if role != "RM" and job.get("user") != user:
            raise AuthzError("无权查看该 AI 分析任务")
        return _qa_job_snapshot(job)


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
                if parsed.path == "/api/ldap/status":
                    # Public endpoint — no auth required (front-end reads this before login)
                    cfg = _LDAP_CONFIG
                    self.send_json({"enabled": bool(cfg.get("enabled")), "uri": cfg.get("uri", "")})
                    return
                if parsed.path == "/api/admin/users":
                    self.require_admin()
                    self.send_json({"users": core.list_users(self.conn())})
                    return
                if parsed.path == "/api/state":
                    self.current_user()
                    self.send_json(self.state_payload())
                    return
                if parsed.path == "/api/qa/analyze-log/status":
                    user = self.current_user()
                    if user["role"] not in {"QA", "RM"}:
                        raise AuthzError("只有 QA 或 RM 可查看 AI 分析进度")
                    job_id = self.query().get("job_id", [""])[0]
                    if not job_id:
                        raise ValueError("job_id is required")
                    job = get_qa_analysis_job(job_id, user["username"], user["role"])
                    if not job:
                        self.send_json({"error": "AI 分析任务不存在或已过期"}, status=404)
                        return
                    self.send_json(job)
                    return
                if parsed.path == "/api/app-audit":
                    self.current_user()
                    app_id = self.query().get("app_id", [""])[0]
                    if not app_id:
                        raise ValueError("app_id is required")
                    release_id = self.query().get("release_id", [""])[0]
                    self.require_app_audit_access(app_id, release_id)
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
                    q = self.query()
                    release_id = q.get("release_id", [""])[0]
                    compare_id = q.get("compare_release_id", [""])[0]
                    if not release_id:
                        raise ValueError("release_id is required")
                    self.send_json(core.build_qa_reports(self.conn(), release_id, compare_id or None))
                    return
                # --- CICD workbench ---
                if parsed.path == "/api/cicd/tasks":
                    self.current_user()
                    status_filter = self.query().get("status", [None])[0]
                    tasks = core.list_cicd_tasks(self.conn(), status_filter=status_filter)
                    self.send_json({"tasks": tasks})
                    return
                if parsed.path.startswith("/api/cicd/tasks/") and parsed.path.endswith("/history"):
                    self.current_user()
                    task_id = parsed.path[len("/api/cicd/tasks/"):-len("/history")]
                    history = core.get_cicd_task_history(self.conn(), task_id)
                    self.send_json({"history": history})
                    return
                if parsed.path == "/api/cicd/requests":
                    user = self.current_user()
                    q = self.query()
                    role = user["role"]
                    username = user["username"]
                    only_mine = q.get("only_mine", [""])[0] == "1"
                    task_id = q.get("task_id", [None])[0]
                    status_filter = q.get("status", [None])[0]
                    since_days_str = q.get("since_days", [None])[0]
                    since_days = int(since_days_str) if since_days_str else None
                    requests = core.list_cicd_requests(
                        self.conn(),
                        username=username if only_mine else None,
                        role=role,
                        task_id=task_id,
                        status_filter=status_filter,
                        since_days=since_days,
                        exclude_cancelled=True,
                    )
                    self.send_json({"requests": requests})
                    return
                if parsed.path == "/api/cicd/notifications":
                    user = self.current_user()
                    counts = core.get_cicd_notifications(self.conn(), user["username"], user["role"])
                    self.send_json(counts)
                    return
                if parsed.path == "/api/cicd/deliveries":
                    user = self.current_user()
                    role = user["role"]
                    if role not in {"SPD", "RM", "Admin", "Owner"}:
                        raise AuthzError("无权访问交付列表")
                    q = self.query()
                    status_filter = q.get("status", [None])[0]
                    submitter_filter = user["username"] if role == "Owner" else None
                    deliveries = core.list_cicd_deliveries(
                        self.conn(),
                        status_filter=status_filter,
                        role=role,
                        submitter=submitter_filter,
                    )
                    self.send_json({"deliveries": deliveries})
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
                if parsed.path == "/api/login/ldap":
                    body = self.json_body()
                    uname, display = ldap_authenticate(body.get("username", ""), body.get("password", ""))
                    token = core.ldap_login_or_create(self.conn(), uname, display)
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
                    # Defence-in-depth: a session cookie alone is not enough to
                    # wipe the business data — require the admin to re-enter
                    # their password in the confirmation form. Blocks a hijacked
                    # tab / forgotten session from triggering an irreversible
                    # action with one click.
                    password = str(body.get("password") or "")
                    if not password:
                        raise AuthzError("清空数据库需要重新输入 admin 密码")
                    row = self.conn().execute(
                        "SELECT password_hash FROM users WHERE username = ?", (username,)
                    ).fetchone()
                    if not row or not core.verify_password(password, row["password_hash"]):
                        raise AuthzError("admin 密码不正确")
                    self._close_conn()  # release before backup copy
                    backup = backup_database()
                    core.clear_business_data(self.conn(), user=username, role=role)
                    self.send_json({"ok": True, "backup": backup.name})
                    return
                if parsed.path == "/api/admin/users/set-role":
                    self.require_admin()
                    body = self.json_body()
                    if not body.get("username") or not body.get("role"):
                        raise ValueError("username 和 role 均为必填")
                    core.set_user_role(
                        self.conn(),
                        body["username"],
                        body["role"],
                        actor=self.user(),
                        actor_role=self.role(),
                    )
                    self.send_json({"ok": True})
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
                    if self.role() not in {"Owner", "RM"}:
                        raise AuthzError("Only Owner or RM can submit new app requests")
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
                    if role == "Owner":
                        owner_content_keys = set(snap_update) - {"release_decision", "owner_confirmed"}
                        if ("app" in body or owner_content_keys) and snap_update.get("owner_confirmed") is not True:
                            raise AuthzError("Owner edits must be saved with Owner confirmation")
                        if "owner_confirmed" in snap_update and snap_update["owner_confirmed"] is not True:
                            raise AuthzError("Owner confirmation can only be submitted, not cleared")
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
                    owner_meta = {"type", "official_url", "description"}
                    doc_labels = {"intro": "基本介绍", "image_usage": "镜像使用方法", "binary_usage": "二进制包使用方法", "env_setup": "环境搭建", "limitations": "已知限制"}

                    def update_repo_if_needed() -> None:
                        if "app" not in body or role != "RM":
                            return
                        app_update = body["app"]
                        repo_before = {"git_url": app.get("git_url", ""), "git_branch": app.get("git_branch", "")}
                        repo_changed = False
                        for key in ("git_url", "git_branch"):
                            if key in app_update and app.get(key) != app_update[key]:
                                app[key] = app_update[key]
                                repo_changed = True
                        if not repo_changed:
                            return
                        # Reject collisions with another app's (git_url, git_branch).
                        # Without this, RM could rename app A's repo info onto app B's
                        # and break the invariant add_new_app_request relies on for
                        # duplicate detection — leaving two apps that look identical
                        # to every Gerrit-fetch and CSV-export code path.
                        collision = conn.execute(
                            "SELECT id FROM apps WHERE git_url = ? AND git_branch = ? AND id != ?",
                            (app.get("git_url", ""), app.get("git_branch", ""), aid),
                        ).fetchone()
                        if collision:
                            raise RuntimeError(
                                f"该 Gerrit URL + branch 已被 app {collision['id']} 占用，不能改成相同值"
                            )
                        core.save_app(conn, app)
                        core.audit(conn, "修改 Gerrit 信息", user=actor, role=role,
                                   app_id=aid, release_id=rid, event="update_app_repo",
                                   detail=core.field_diff(repo_before, app, {"git_url": "Gerrit URL", "git_branch": "Branch"}))

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
                                                                  {"release_decision": "release 决策"}))
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
                                       detail=core.field_diff(meta_before, meta_after, core.APP_META_LABELS))
                        if "owner_confirmed" in snap_update:
                            if role != "Owner":
                                raise AuthzError("Owner confirmation must be submitted by an Owner")
                            if snap_update["owner_confirmed"] and not snapshot.get("owner_confirmed"):
                                core.audit(conn, f"提交 Owner 确认：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="owner_confirm",
                                           detail=[{"field": "owner_confirmed", "label": "Owner 确认", "old": "未确认", "new": "已确认"}])
                            snapshot["owner_confirmed"] = snap_update["owner_confirmed"]
                        if "doc" in snap_update:
                            doc_update = snap_update["doc"]
                            current_doc = snapshot.get("doc", {})
                            doc_changes = core.field_diff(current_doc, doc_update, {k: doc_labels.get(k, k) for k in doc_update})
                            if doc_changes:
                                core.audit(conn, f"修改文档字段：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_doc_fields", detail=doc_changes)
                            snapshot.setdefault("doc", {}).update(doc_update)
                        if "community" in snap_update:
                            comm_update = snap_update["community"]
                            comm_labels = {"release_status": "社区发布情况", "python_version": "社区包 Python 版本", "framework_version": "社区包框架及版本"}
                            comm_before = {k: (snapshot.get("community") or {}).get(k, "") for k in comm_labels}
                            comm_changes = core.field_diff(comm_before, comm_update, comm_labels)
                            if comm_changes:
                                core.audit(conn, f"修改社区发布信息：{name_for_msg}", user=actor, role=role,
                                           app_id=aid, release_id=rid, event="update_community", detail=comm_changes)
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
                                           app_id=aid, release_id=rid, event="update_sanity", detail=sanity_changes)
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
                                           app_id=aid, release_id=rid, event="update_test_docs", detail=td_changes)
                    # The transaction context suppresses helper-level commits,
                    # including legacy helpers added later in this path.
                    with core.transaction(conn):
                        update_repo_if_needed()
                        updated = core.update_snapshot(conn, rid, aid, mutate, skip_doc_deadline=past_doc_deadline)
                        updated["missing_items"] = core.missing_items_for(core.get_app(conn, aid), updated)
                        core.save_snapshot(conn, rid, aid, updated)
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
                if parsed.path == "/api/qa/analyze-log":
                    if self.role() not in {"QA", "RM"}:
                        raise AuthzError("只有 QA 或 RM 可使用 AI 分析 log")
                    body = self.json_body()
                    result = core.qa_analyze_log(
                        self.conn(),
                        DB_PATH,
                        body["release_id"],
                    )
                    self.send_json(result)
                    return
                if parsed.path == "/api/qa/analyze-log/start":
                    user = self.current_user()
                    if user["role"] not in {"QA", "RM"}:
                        raise AuthzError("只有 QA 或 RM 可使用 AI 分析 log")
                    body = self.json_body()
                    release_id = body.get("release_id", "")
                    if not release_id:
                        raise ValueError("release_id is required")
                    job = start_qa_analysis_job(release_id, user["username"], user["role"])
                    self.send_json(job)
                    return
                if parsed.path == "/api/release-schedule/upsert":
                    self.require_rm()
                    body = self.json_body()
                    entry = core.upsert_release_schedule(
                        self.conn(),
                        entry_id=body.get("id") or None,
                        version=body.get("version", ""),
                        branch_cut_at=body.get("branch_cut_at", ""),
                        release_at=body.get("release_at", ""),
                        note=body.get("note", ""),
                        user=self.user(),
                        role=self.role(),
                    )
                    self.send_json({"entry": entry})
                    return
                if parsed.path == "/api/release-schedule/delete":
                    self.require_rm()
                    body = self.json_body()
                    if not body.get("id"):
                        raise ValueError("id is required")
                    ok = core.delete_release_schedule(self.conn(), body["id"], user=self.user(), role=self.role())
                    if not ok:
                        raise RuntimeError("entry not found")
                    self.send_json({"ok": True})
                    return
                # --- CICD workbench ---
                if parsed.path == "/api/cicd/requests/submit":
                    user = self.current_user()
                    role = user["role"]
                    if role not in core.CICD_CREATE_ROLES:
                        raise AuthzError("只有 Owner、RM、Admin 可以提交 CICD 任务申请")
                    body = self.json_body()
                    req = core.submit_cicd_request(
                        self.conn(),
                        task_id=body.get("task_id") or None,
                        request_type=body.get("request_type", "create"),
                        payload=body.get("payload", {}),
                        submitter=user["username"],
                        submitter_role=role,
                        submitter_display=user.get("display_name", ""),
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/approve":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以审批")
                    body = self.json_body()
                    approval_mode    = body.get("approval_mode", "immediate")
                    jira_auto_created = int(body.get("jira_auto_created", 0))
                    jira_id          = body.get("jira_id", "")

                    # Auto-create Jira issue when dispatching to SPD
                    if jira_auto_created and approval_mode == "dispatch_spd" and not jira_id:
                        try:
                            jcfg = jira_client.load_config()
                            if jcfg:
                                conn_tmp = self.conn()
                                row = conn_tmp.execute(
                                    "SELECT request_type, task_id, payload, submitter FROM cicd_task_requests WHERE id=?",
                                    (int(body["request_id"]),),
                                ).fetchone()
                                if row:
                                    import json as _json
                                    payload_dict = _json.loads(row["payload"] or "{}")
                                    title = jira_client.compute_title(
                                        conn_tmp, row["request_type"], payload_dict, row["task_id"]
                                    )
                                    desc = jira_client.build_description(
                                        request_id=int(body["request_id"]),
                                        request_type=row["request_type"],
                                        payload=payload_dict,
                                        task_id=row["task_id"],
                                        submitter=row["submitter"],
                                        title=title,
                                        review_note=body.get("review_note", ""),
                                    )
                                    jira_id = jira_client.create_issue(jcfg, title, description=desc)
                        except Exception as _je:
                            import logging as _log
                            _log.getLogger(__name__).warning("Jira auto-create failed: %s", _je)
                            # Do not block approval on Jira failure

                    req = core.approve_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        reviewer=user["username"],
                        reviewer_role=user["role"],
                        review_note=body.get("review_note", ""),
                        approval_mode=approval_mode,
                        jira_id=jira_id,
                        jira_auto_created=jira_auto_created,
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/reject":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以拒绝")
                    body = self.json_body()
                    req = core.reject_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        reviewer=user["username"],
                        reviewer_role=user["role"],
                        review_note=body.get("review_note", ""),
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/cancel":
                    user = self.current_user()
                    body = self.json_body()
                    req = core.cancel_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        username=user["username"],
                        role=user["role"],
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/tasks/transfer-owner":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以直接修改负责人")
                    body = self.json_body()
                    task = core.transfer_cicd_owner(
                        self.conn(),
                        body["task_id"],
                        body["new_owner"],
                        actor=user["username"],
                        actor_role=user["role"],
                    )
                    self.send_json({"ok": True, "task": task})
                    return
                if parsed.path == "/api/cicd/tasks/delete":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以删除 CICD 任务")
                    body = self.json_body()
                    core.delete_cicd_task(
                        self.conn(),
                        body["task_id"],
                        actor=user["username"],
                        actor_role=user["role"],
                    )
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/cicd/notifications/mark-visited":
                    user = self.current_user()
                    core.mark_cicd_visited(self.conn(), user["username"])
                    self.send_json({"ok": True})
                    return
                if parsed.path == "/api/cicd/requests/deliver":
                    user = self.current_user()
                    if user["role"] not in {"SPD", "RM", "Admin"}:
                        raise AuthzError("只有 SPD、RM、Admin 可以标记已交付")
                    body = self.json_body()
                    req = core.deliver_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        deliverer=user["username"],
                        deliverer_role=user["role"],
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/return-delivery":
                    user = self.current_user()
                    if user["role"] != "SPD":
                        raise AuthzError("只有 SPD 可以退回交付申请")
                    body = self.json_body()
                    req = core.return_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        returner=user["username"],
                        returner_role=user["role"],
                        reason=body.get("reason", ""),
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/re-dispatch":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以重新下发")
                    body = self.json_body()
                    req = core.re_dispatch_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        actor=user["username"],
                        actor_role=user["role"],
                    )
                    self.send_json({"ok": True, "request": req})
                    return
                if parsed.path == "/api/cicd/requests/apply-returned":
                    user = self.current_user()
                    if user["role"] not in core.CICD_APPROVER_ROLES:
                        raise AuthzError("只有 RM/Admin 可以直接生效")
                    body = self.json_body()
                    req = core.apply_returned_cicd_request(
                        self.conn(),
                        int(body["request_id"]),
                        actor=user["username"],
                        actor_role=user["role"],
                    )
                    self.send_json({"ok": True, "request": req})
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
                        role=self.role(),
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
                        role=self.role(),
                    )
                    self.send_json({"snapshot": snapshot, "commit_id": commit_id, "source": snapshot.get("app_info", {}).get("source", "")})
                    return
                if parsed.path == "/api/app-info/fetch-all":
                    self.require_rm()
                    body = self.json_body()
                    results = fetch_all_app_infos_from_gerrit(self.conn(), body["release_id"], uploaded_by=self.user(), role=self.role())
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
        release_ids = {r["id"] for r in releases}
        latest = releases[-1]["id"] if releases else ""
        requested = self.query().get("release_id", [latest])[0]
        release_id = requested if requested in release_ids else latest
        apps = core.list_apps(conn)
        payload = {
            "apps": apps,
            "releases": [_serialize_release(r) for r in releases],
            "release": None,
            "artifacts": [],
            "user": user,
            "user_display_names": {
                row["username"]: row["display_name"]
                for row in conn.execute("SELECT username, display_name FROM users WHERE display_name <> ''")
            },
            "qa_log": None,
            "qa_audit_logs": {},
            "release_schedule": core.list_release_schedule(conn),
        }
        if release_id:
            core.refresh_missing_items(conn, release_id)
            release = core.get_release(conn, release_id)
            payload["release"] = _serialize_release(release)
            payload["artifacts"] = [dict(row) for row in conn.execute("SELECT kind, name, final, generated_at FROM artifacts WHERE release_id = ?", (release_id,))]
            payload["qa_log"] = core.get_qa_log(conn, release_id)
            if user["role"] in {"QA", "RM"}:
                payload["qa_audit_logs"] = core.release_qa_audit_logs(conn, release_id)
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

    def require_app_audit_access(self, app_id: str, release_id: str = "") -> None:
        role = self.role()
        if role in {"RM", "Admin", "QA"}:
            return
        if role != "Owner":
            raise AuthzError("App audit access denied")

        username = self.user()
        if release_id:
            snapshot = core.get_release(self.conn(), release_id)["snapshots"].get(app_id)
            if snapshot and username in (snapshot.get("owners") or []):
                return
            raise AuthzError("App audit access denied")

        for release in core.list_releases(self.conn()):
            snapshot = core.get_release(self.conn(), release["id"])["snapshots"].get(app_id)
            if snapshot and username in (snapshot.get("owners") or []):
                return
        raise AuthzError("App audit access denied")


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


def fetch_all_app_infos_from_gerrit(conn, release_id: str, *, uploaded_by: str, role: str = "RM") -> dict:
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
                role=role,
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
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8033)
    args = parser.parse_args()
    conn = core.connect(DB_PATH)
    admin_source = ensure_admin_user(conn)
    conn.close()
    if admin_source:
        print(f"Admin user created. Password source: {admin_source}")
    global _LDAP_CONFIG
    _LDAP_CONFIG = _load_ldap_config()
    if _LDAP_CONFIG.get("enabled"):
        print(f"LDAP authentication enabled: {_LDAP_CONFIG['uri']}")
    else:
        print("LDAP authentication disabled (set 'enabled = true' in ldap.conf to enable)")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
