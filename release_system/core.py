from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Callable


_INIT_LOCK = threading.Lock()
_INITIALIZED_DBS: set[str] = set()


BEIJING_TZ = dt.timezone(dt.timedelta(hours=8))


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def beijing_now() -> dt.datetime:
    """Current Beijing time as a naive datetime (no tzinfo)."""
    return dt.datetime.now(BEIJING_TZ).replace(tzinfo=None, microsecond=0)


def normalize_deadline(value: str | None) -> str:
    """Normalize a deadline string to ``YYYY-MM-DD HH:MM`` (Beijing time).

    Accepts ``''`` (returns ``''``), ``YYYY-MM-DD``, ``YYYY-MM-DDTHH:MM[:SS]``,
    or ``YYYY-MM-DD HH:MM[:SS]``. Empty deadline means "no deadline set".
    """
    text = (value or "").strip()
    if not text:
        return ""
    text = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = dt.datetime.strptime(text, fmt)
            if fmt == "%Y-%m-%d":
                parsed = parsed.replace(hour=23, minute=59)
            return parsed.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    raise ValueError(f"Invalid deadline: {value!r}; expected YYYY-MM-DD or YYYY-MM-DD HH:MM")


def parse_deadline(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.strptime(normalize_deadline(value), "%Y-%m-%d %H:%M")


def validate_deadline_order(app_freeze_deadline: str | None, doc_deadline: str | None) -> None:
    """Reject deadlines where app freeze lands after the doc deadline.

    Empty deadlines mean "not set" and are always accepted. current_phase
    assumes app freeze precedes the doc deadline; a reversed pair produces
    incoherent phases, so it is blocked at every entry point that sets them.
    """
    freeze = parse_deadline(app_freeze_deadline)
    doc = parse_deadline(doc_deadline)
    if freeze is not None and doc is not None and freeze > doc:
        raise ValueError(
            f"App 冻结 deadline（{normalize_deadline(app_freeze_deadline)}）"
            f"不能晚于 Doc deadline（{normalize_deadline(doc_deadline)}）"
        )


def is_before(deadline: str | None, *, ref: dt.datetime | None = None) -> bool:
    """True if the reference moment is strictly before the deadline.

    Empty/None deadline means "no deadline set" → treated as infinite future,
    so this returns True (i.e. the action is still allowed).
    """
    dl = parse_deadline(deadline)
    if dl is None:
        return True
    return (ref or beijing_now()) < dl


def current_phase(release: dict[str, Any]) -> str:
    """Derive the lifecycle phase of a release from its deadlines and lock flag."""
    if release.get("released_locked"):
        return "released_locked"
    if not is_before(release.get("doc_deadline", "")):
        return "after_doc_deadline"
    if not is_before(release.get("app_freeze_deadline", "")):
        return "after_app_freeze"
    return "before_app_freeze"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def normalize_name(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text.replace("_", "-").replace(".", "-")


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,，、;；/]+", value)
    return sorted({part.strip() for part in parts if part.strip()})


def join_list(values: list[str] | set[str] | tuple[str, ...]) -> str:
    return ",".join(sorted({str(v).strip() for v in values if str(v).strip()}))


def loads_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def connect(path: str | Path = "release_system.db") -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    key = str(Path(path).resolve()) if not str(path).startswith(":") else str(path)
    with _INIT_LOCK:
        if key not in _INITIALIZED_DBS:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.OperationalError:
                pass  # in-memory dbs don't support WAL
            init_db(conn)
            _INITIALIZED_DBS.add(key)
    return conn


def reset_init_state() -> None:
    """Reset the initialized-db tracker; used by tests when cycling DBs."""
    with _INIT_LOCK:
        _INITIALIZED_DBS.clear()


def backup_sqlite(src_path: str | Path, dest_path: str | Path) -> None:
    """Write a consistent backup of the SQLite db at *src_path* to *dest_path*.

    Uses the SQLite online-backup API rather than a file copy: in WAL mode a
    plain file copy can miss committed transactions still in the -wal file and
    skips the -wal/-shm sidecars, producing a silently stale backup.
    """
    source = sqlite3.connect(src_path)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS apps (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            official_name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '',
            type TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            git_url TEXT NOT NULL DEFAULT '',
            git_branch TEXT NOT NULL DEFAULT '',
            official_url TEXT NOT NULL DEFAULT '',
            doc_target TEXT NOT NULL DEFAULT 'manual',
            owners_json TEXT NOT NULL DEFAULT '[]',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_by TEXT NOT NULL DEFAULT 'import',
            created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS releases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            maca_version TEXT NOT NULL DEFAULT '',
            app_freeze_deadline TEXT NOT NULL DEFAULT '',
            doc_deadline TEXT NOT NULL DEFAULT '',
            released_locked INTEGER NOT NULL DEFAULT 0,
            released_locked_at TEXT NOT NULL DEFAULT '',
            released_locked_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cloned_from TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
            data_json TEXT NOT NULL,
            PRIMARY KEY (release_id, app_id)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            release_id TEXT NOT NULL REFERENCES releases(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            final INTEGER NOT NULL DEFAULT 0,
            generated_at TEXT NOT NULL,
            PRIMARY KEY (release_id, kind)
        );

        CREATE TABLE IF NOT EXISTS qa_logs (
            release_id TEXT PRIMARY KEY REFERENCES releases(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            storage_path TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            uploaded_by TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user TEXT NOT NULL,
            role TEXT NOT NULL,
            app_id TEXT NOT NULL DEFAULT '',
            release_id TEXT NOT NULL DEFAULT '',
            event TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
            created_at TEXT NOT NULL
        );
        """
    )
    if "official_url" not in {row["name"] for row in conn.execute("PRAGMA table_info(apps)")}:
        conn.execute("ALTER TABLE apps ADD COLUMN official_url TEXT NOT NULL DEFAULT ''")
    conn.execute("UPDATE apps SET doc_target = 'manual' WHERE doc_target NOT IN ('manual', 'ai4sci')")
    ensure_default_user(conn)
    conn.commit()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = encoded.split("$", 1)
    return secrets.compare_digest(hash_password(password, salt).split("$", 1)[1], expected)


DEFAULT_USERS: tuple[tuple[str, str, str], ...] = (
    ("rm", "rm", "RM"),
    ("owner_test", "owner_test", "Owner"),
    ("qa", "qa", "QA"),
)

ROLES = {"RM", "Owner", "QA", "Admin"}
RELEASE_DECISIONS = {"release", "cicd_only", "stopped"}
NON_RELEASE_DECISIONS = {"cicd_only", "stopped"}
DOC_TARGETS = {"manual", "ai4sci"}
QA_STATUSES = {"not_checked", "qa_passed", "has_issues", "cannot_release"}
MAX_APP_DESCRIPTION_CHARS = 30
MANAGER_REVIEW_FIELDS = [
    ("app_name", "App"),
    ("official_name", "官方名称"),
    ("doc_target", "文档类型"),
    ("app_type", "App类型"),
    ("version", "版本号"),
    ("owners", "Owner"),
    ("chip_support", "支持芯片类型"),
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
    "version",
    "owners",
    "chip_support",
    "releasable",
    "not_releasable_reason",
    "known_limitations",
]


def normalize_release_decision(value: str | None) -> str:
    decision = (value or "release").strip()
    return "stopped" if decision == "no_release" else decision


def normalize_doc_target(value: str | None) -> str:
    target = (value or "manual").strip()
    aliases = {
        "HPC": "manual",
        "hpc": "manual",
        "manual": "manual",
        "AI4Sci": "ai4sci",
        "ai4sci": "ai4sci",
        "AI4SCI": "ai4sci",
    }
    return aliases.get(target, "manual")


def normalize_app_description(value: str | None) -> str:
    description = (value or "").strip()
    if len(description) > MAX_APP_DESCRIPTION_CHARS:
        raise ValueError(f"描述不能超过{MAX_APP_DESCRIPTION_CHARS}字")
    return description


def ensure_default_user(conn: sqlite3.Connection) -> None:
    for username, password, role in DEFAULT_USERS:
        conn.execute(
            "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?) ON CONFLICT(username) DO NOTHING",
            (username, hash_password(password), role),
        )


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = "Owner") -> None:
    conn.execute(
        "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role",
        (username, hash_password(password), role),
    )
    conn.commit()


def clear_business_data(conn: sqlite3.Connection, *, user: str = "admin", role: str = "Admin") -> None:
    """Clear release data while preserving user accounts."""
    conn.execute("DELETE FROM artifacts")
    conn.execute("DELETE FROM qa_logs")
    conn.execute("DELETE FROM snapshots")
    conn.execute("DELETE FROM releases")
    conn.execute("DELETE FROM apps")
    conn.execute("DELETE FROM audit")
    ensure_default_user(conn)
    conn.commit()
    audit(conn, "数据库已清空，默认账号已保留", user=user, role=role)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    row = conn.execute("SELECT username, password_hash FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token, username, created_at) VALUES (?, ?, ?)", (token, username, now()))
    conn.commit()
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    row = conn.execute(
        "SELECT users.username, users.role FROM sessions JOIN users ON sessions.username = users.username WHERE sessions.token = ?",
        (token,),
    ).fetchone()
    return dict(row) if row else None


def logout_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def audit(
    conn: sqlite3.Connection,
    message: str,
    *,
    user: str = "system",
    role: str = "system",
    app_id: str = "",
    release_id: str = "",
    event: str = "",
    commit: bool = True,
) -> None:
    conn.execute(
        "INSERT INTO audit(ts, user, role, app_id, release_id, event, message) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now(), user, role, app_id, release_id, event, message),
    )
    if commit:
        conn.commit()


def app_audit_log(conn: sqlite3.Connection, app_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT ts, user, role, release_id, event, message FROM audit WHERE app_id = ? ORDER BY id DESC",
            (app_id,),
        )
    ]


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def parse_csv_text(text: str) -> list[dict[str, str]]:
    return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text.lstrip("﻿")))]


def parse_alias_lines(raw: str = "") -> dict[str, str]:
    aliases: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        left, right = [part.strip() for part in line.split("=", 1)]
        if left and right:
            aliases[normalize_name(left)] = normalize_name(right)
    return aliases


def infer_doc_target(category: str = "", app_type: str = "") -> str:
    text = f"{category} {app_type}".lower()
    if "ai" in text or "模型" in text or "框架" in text:
        return "ai4sci"
    return "manual"


def canonical_id(name: str, aliases: dict[str, str] | None = None) -> str:
    normalized = normalize_name(name)
    return (aliases or {}).get(normalized, normalized)


def app_from_row(existing: dict[str, Any] | None, *, app_id: str, name: str) -> dict[str, Any]:
    base = existing or {
        "id": app_id,
        "name": name,
        "official_name": name,
        "category": "",
        "type": "",
        "description": "",
        "git_url": "",
        "git_branch": "",
        "official_url": "",
        "doc_target": "",
        "owners": [],
        "aliases": [],
        "created_by": "import",
        "created_at": now(),
    }
    base["aliases"] = sorted(set(base.get("aliases", []) + [name]))
    return base


def combined_release_row(rows: list[dict[str, str]]) -> dict[str, str]:
    combined = dict(rows[0]) if rows else {}
    x86_chips: list[str] = []
    arm_chips: list[str] = []
    hpcc_chips: list[str] = []
    archs: list[str] = []
    for row in rows:
        arch = (row.get("arch") or "").lower()
        chips = split_list(row.get("maca_chip"))
        if "arm" in arch or "aarch" in arch:
            arm_chips.extend(chips)
        else:
            x86_chips.extend(chips)
        hpcc_chips.extend(split_list(row.get("hpcc_chip")))
        if row.get("arch"):
            archs.append(row["arch"])
    combined["_x86_chips"] = join_list(x86_chips)
    combined["_arm_chips"] = join_list(arm_chips)
    combined["maca_chip"] = combined["_x86_chips"] or combined.get("maca_chip", "")
    combined["hpcc_chip"] = join_list(hpcc_chips) or combined.get("hpcc_chip", "")
    combined["arch"] = join_list(archs) or combined.get("arch", "")
    return combined


def variant_app_id(base_id: str, version: str, branch: str, used_ids: set[str]) -> str:
    suffix = normalize_name(version) or normalize_name(branch) or "variant"
    candidate = f"{base_id}_{suffix}" if suffix else base_id
    branch_suffix = normalize_name(branch)
    if candidate in used_ids and branch_suffix and branch_suffix not in candidate:
        candidate = f"{candidate}_{branch_suffix}"
    index = 2
    original = candidate
    while candidate in used_ids:
        candidate = f"{original}_{index}"
        index += 1
    return candidate


def row_to_app(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["owners"] = loads_json(data.pop("owners_json"), [])
    data["aliases"] = loads_json(data.pop("aliases_json"), [])
    data["doc_target"] = normalize_doc_target(data.get("doc_target"))
    return data


def save_app(conn: sqlite3.Connection, app: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO apps(id, name, official_name, category, type, description, git_url, git_branch,
                         official_url, doc_target, owners_json, aliases_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          official_name=excluded.official_name,
          category=excluded.category,
          type=excluded.type,
          description=excluded.description,
          git_url=excluded.git_url,
          git_branch=excluded.git_branch,
          official_url=excluded.official_url,
          doc_target=excluded.doc_target,
          owners_json=excluded.owners_json,
          aliases_json=excluded.aliases_json,
          created_by=excluded.created_by
        """,
        (
            app["id"],
            app["name"],
            app.get("official_name") or app["name"],
            app.get("category", ""),
            app.get("type", ""),
            app.get("description", ""),
            app.get("git_url", ""),
            app.get("git_branch", ""),
            app.get("official_url", ""),
            normalize_doc_target(app.get("doc_target")),
            dumps_json(sorted(set(app.get("owners", [])))),
            dumps_json(sorted(set(app.get("aliases", [])))),
            app.get("created_by", "import"),
            app.get("created_at") or now(),
        ),
    )


def get_app(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise KeyError(f"Unknown app: {app_id}")
    return row_to_app(row)


def list_apps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [row_to_app(row) for row in conn.execute("SELECT * FROM apps ORDER BY name")]


def locked_releases_for_app(conn: sqlite3.Connection, app_id: str) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            """
            SELECT releases.name
            FROM releases
            JOIN snapshots ON snapshots.release_id = releases.id
            WHERE snapshots.app_id = ? AND releases.released_locked = 1
            ORDER BY releases.created_at
            """,
            (app_id,),
        )
    ]


def delete_app(conn: sqlite3.Connection, app_id: str, *, user: str = "admin", role: str = "Admin") -> dict[str, Any]:
    app = get_app(conn, app_id)
    locked_releases = locked_releases_for_app(conn, app_id)
    if locked_releases:
        raise RuntimeError(f"App is used by locked releases and cannot be deleted: {', '.join(locked_releases)}")
    affected_releases = [
        row["release_id"]
        for row in conn.execute("SELECT release_id FROM snapshots WHERE app_id = ?", (app_id,))
    ]
    for affected in affected_releases:
        conn.execute("DELETE FROM artifacts WHERE release_id = ? AND final = 0", (affected,))
    conn.execute("DELETE FROM apps WHERE id = ?", (app_id,))
    conn.commit()
    audit(conn, f"删除 app：{app['name']} ({app_id})", user=user, role=role, app_id=app_id, event="delete_app")
    return app


def base_snapshot(app: dict[str, Any], release_row: dict[str, str] | None = None, owner_row: dict[str, str] | None = None) -> dict[str, Any]:
    release_row = release_row or {}
    owner_row = owner_row or {}
    return {
        "app_id": app["id"],
        "release_decision": "release",
        "qa_status": "not_checked",
        "qa_issue_note": "",
        "owner_confirmed": False,
        "version": release_row.get("app_version") or owner_row.get("对应官方版本") or "",
        "x86_chips": release_row.get("maca_chip") or owner_row.get("X86支持芯片系列") or "",
        "arm_chips": owner_row.get("ARM支持芯片类型") or (release_row.get("maca_chip", "") if release_row.get("arch") == "arm" else ""),
        "hpcc_chip": release_row.get("hpcc_chip") or "",
        "arch": release_row.get("arch") or "",
        "maca_version": release_row.get("maca_version") or "",
        "community": owner_row.get("开发者社区发布情况") or "",
        "python_version": owner_row.get("开发者社区发布包支持python版本") or "",
        "framework_version": owner_row.get("开发者社区发布包支持的底层框架及版本") or "",
        "doc": {
            "intro": owner_row.get("描述") or app.get("description", ""),
            "image_usage": "",
            "binary_usage": "",
            "env_setup": "",
            "test_method": "",
            "test_result": "",
            "limitations": "",
        },
        "app_info": None,
        "app_info_diffs": [],
        "test_docs": [],
        "missing_items": [],
    }


def save_release(conn: sqlite3.Connection, release: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO releases(id, name, maca_version, app_freeze_deadline, doc_deadline,
                             released_locked, released_locked_at, released_locked_by,
                             created_at, source, cloned_from)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          maca_version=excluded.maca_version,
          app_freeze_deadline=excluded.app_freeze_deadline,
          doc_deadline=excluded.doc_deadline,
          released_locked=excluded.released_locked,
          released_locked_at=excluded.released_locked_at,
          released_locked_by=excluded.released_locked_by,
          created_at=excluded.created_at,
          source=excluded.source,
          cloned_from=excluded.cloned_from
        """,
        (
            release["id"],
            release["name"],
            release.get("maca_version", ""),
            normalize_deadline(release.get("app_freeze_deadline", "")),
            normalize_deadline(release.get("doc_deadline", "")),
            int(release.get("released_locked", 0)),
            release.get("released_locked_at", ""),
            release.get("released_locked_by", ""),
            release.get("created_at", now()),
            release.get("source", "manual"),
            release.get("cloned_from", ""),
        ),
    )


def update_release_deadlines(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    name: str | None = None,
    app_freeze_deadline: str | None = None,
    doc_deadline: str | None = None,
    user: str = "system",
    role: str = "system",
) -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改 release 设置")
    new_name = (name or "").strip() if name is not None else release.get("name", "")
    if not new_name:
        raise ValueError("Release 名称不能为空")
    new_freeze = normalize_deadline(app_freeze_deadline) if app_freeze_deadline is not None else release.get("app_freeze_deadline", "")
    new_doc = normalize_deadline(doc_deadline) if doc_deadline is not None else release.get("doc_deadline", "")
    validate_deadline_order(new_freeze, new_doc)
    conn.execute(
        "UPDATE releases SET name = ?, app_freeze_deadline = ?, doc_deadline = ? WHERE id = ?",
        (new_name, new_freeze, new_doc, release_id),
    )
    conn.commit()
    audit(
        conn,
        f"更新 release 设置：{release['name']} -> {new_name}，app_freeze={new_freeze or '空'}, doc={new_doc or '空'}",
        user=user,
        role=role,
        release_id=release_id,
        event="update_release_settings",
    )
    return get_release(conn, release_id)


def release_is_locked(conn: sqlite3.Connection, release_id: str) -> bool:
    row = conn.execute("SELECT released_locked FROM releases WHERE id = ?", (release_id,)).fetchone()
    return bool(row and row["released_locked"])


def save_snapshot(conn: sqlite3.Connection, release_id: str, app_id: str, snapshot: dict[str, Any]) -> None:
    if release_is_locked(conn, release_id):
        raise RuntimeError("Release 已最终锁定，所有快照不可修改")
    conn.execute(
        """
        INSERT INTO snapshots(release_id, app_id, data_json)
        VALUES (?, ?, ?)
        ON CONFLICT(release_id, app_id) DO UPDATE SET data_json=excluded.data_json
        """,
        (release_id, app_id, dumps_json(snapshot)),
    )


def get_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,)).fetchone()
    if not row:
        raise KeyError(f"Unknown release: {release_id}")
    release = dict(row)
    release["released_locked"] = bool(release.get("released_locked"))
    release["snapshots"] = {
        snap["app_id"]: loads_json(snap["data_json"], {})
        for snap in conn.execute("SELECT app_id, data_json FROM snapshots WHERE release_id = ?", (release_id,))
    }
    release["phase"] = current_phase(release)
    return release


def list_releases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    result = []
    for row in conn.execute("SELECT * FROM releases ORDER BY created_at, rowid"):
        rel = dict(row)
        rel["released_locked"] = bool(rel.get("released_locked"))
        rel["phase"] = current_phase(rel)
        result.append(rel)
    return result


def previous_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    releases = list_releases(conn)
    for i, release in enumerate(releases):
        if release["id"] == release_id and i > 0:
            return get_release(conn, releases[i - 1]["id"])
    return None


def last_published_snapshot(conn: sqlite3.Connection, app_id: str, before_release_id: str) -> dict[str, Any] | None:
    """Find the most recent locked snapshot for *app_id* before *before_release_id*."""
    releases = list_releases(conn)
    target_idx = next((i for i, r in enumerate(releases) if r["id"] == before_release_id), None)
    if target_idx is None:
        return None
    for i in range(target_idx - 1, -1, -1):
        r = releases[i]
        if not r.get("released_locked"):
            continue
        row = conn.execute(
            "SELECT data_json FROM snapshots WHERE release_id = ? AND app_id = ?",
            (r["id"], app_id),
        ).fetchone()
        if row:
            snap = loads_json(row["data_json"], {})
            if snap.get("locked_in_release"):
                return snap
    return None


def import_initial(
    conn: sqlite3.Connection,
    release_csv: str | Path,
    owner_csv: str | Path,
    *,
    alias_text: str = "",
    release_name: str | None = None,
    maca_version: str | None = None,
    app_freeze_deadline: str = "",
    doc_deadline: str = "",
) -> str:
    release_rows = read_csv(release_csv)
    owner_rows = read_csv(owner_csv)
    return import_initial_rows(
        conn,
        release_rows,
        owner_rows,
        alias_text=alias_text,
        release_name=release_name,
        maca_version=maca_version,
        app_freeze_deadline=app_freeze_deadline,
        doc_deadline=doc_deadline,
    )


def import_initial_rows(
    conn: sqlite3.Connection,
    release_rows: list[dict[str, str]],
    owner_rows: list[dict[str, str]],
    *,
    alias_text: str = "",
    release_name: str | None = None,
    maca_version: str | None = None,
    app_freeze_deadline: str = "",
    doc_deadline: str = "",
) -> str:
    validate_deadline_order(app_freeze_deadline, doc_deadline)
    aliases = parse_alias_lines(alias_text)
    apps: dict[str, dict[str, Any]] = {app["id"]: app for app in list_apps(conn)}
    owner_by_base: dict[str, list[dict[str, str]]] = {}
    owner_by_base_version: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in owner_rows:
        base_id = canonical_id(row.get("名称", ""), aliases)
        owner_by_base.setdefault(base_id, []).append(row)
        owner_by_base_version.setdefault((base_id, row.get("对应官方版本", "")), []).append(row)

    release_groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in release_rows:
        base_id = canonical_id(row.get("app_name", ""), aliases)
        key = (base_id, row.get("app_version", ""), row.get("git_branch", ""))
        release_groups.setdefault(key, []).append(row)
    variants_by_base: dict[str, int] = {}
    for base_id, _, _ in release_groups:
        variants_by_base[base_id] = variants_by_base.get(base_id, 0) + 1

    release_row_by_app: dict[str, dict[str, str]] = {}
    owner_row_by_app: dict[str, dict[str, str]] = {}
    release_base_ids: set[str] = set()
    used_ids: set[str] = set()
    for (base_id, version, branch), rows in release_groups.items():
        release_base_ids.add(base_id)
        app_id = base_id if variants_by_base[base_id] == 1 else variant_app_id(base_id, version, branch, used_ids)
        used_ids.add(app_id)
        rel_row = combined_release_row(rows)
        selected_owner_rows = owner_by_base_version.get((base_id, version)) or owner_by_base.get(base_id, [])
        owner_row = selected_owner_rows[0] if selected_owner_rows else {}
        display_name = rows[0].get("app_name", "")
        if variants_by_base[base_id] > 1:
            display_name = f"{display_name} {version or branch}".strip()
        app = app_from_row(apps.get(app_id), app_id=app_id, name=display_name)
        app["official_name"] = rows[0].get("app_name", "") or app.get("official_name") or display_name
        app["git_url"] = app.get("git_url") or rel_row.get("git_url", "")
        app["git_branch"] = app.get("git_branch") or rel_row.get("git_branch", "")
        if owner_row:
            app["category"] = app.get("category") or owner_row.get("类别", "")
            app["type"] = app.get("type") or owner_row.get("类型", "")
            app["description"] = app.get("description") or owner_row.get("描述", "")
            app["doc_target"] = app.get("doc_target") or infer_doc_target(owner_row.get("类别", ""), owner_row.get("类型", ""))
        owners: list[str] = []
        for selected in selected_owner_rows:
            owners.extend(split_list(selected.get("Owner")))
        app["owners"] = sorted(set(app.get("owners", []) + owners))
        apps[app_id] = app
        release_row_by_app[app_id] = rel_row
        owner_row_by_app[app_id] = owner_row

    for base_id, rows in owner_by_base.items():
        if base_id in release_base_ids:
            continue
        owner_row = rows[0]
        app = app_from_row(apps.get(base_id), app_id=base_id, name=owner_row.get("名称", ""))
        app["category"] = app.get("category") or owner_row.get("类别", "")
        app["type"] = app.get("type") or owner_row.get("类型", "")
        app["description"] = app.get("description") or owner_row.get("描述", "")
        app["doc_target"] = app.get("doc_target") or infer_doc_target(owner_row.get("类别", ""), owner_row.get("类型", ""))
        owners: list[str] = []
        for selected in rows:
            owners.extend(split_list(selected.get("Owner")))
        app["owners"] = sorted(set(app.get("owners", []) + owners))
        apps[base_id] = app
        owner_row_by_app[base_id] = owner_row

    for app in apps.values():
        save_app(conn, app)
        audit(
            conn,
            f"导入 app：{app['name']}",
            user="import",
            role="system",
            app_id=app["id"],
            event="create_app",
        )

    release_id = new_id("rel")
    first_release = {
        "id": release_id,
        "name": release_name or maca_version or release_rows[0].get("maca_version") or "initial-release",
        "maca_version": maca_version or release_rows[0].get("maca_version", ""),
        "app_freeze_deadline": app_freeze_deadline,
        "doc_deadline": doc_deadline,
        "released_locked": 0,
        "released_locked_at": "",
        "released_locked_by": "",
        "created_at": now(),
        "source": "initial_csv",
        "cloned_from": "",
    }
    save_release(conn, first_release)

    for app in apps.values():
        rel_row = release_row_by_app.get(app["id"], {})
        snapshot = base_snapshot(app, rel_row, owner_row_by_app.get(app["id"], {}))
        if rel_row.get("_x86_chips"):
            snapshot["x86_chips"] = rel_row["_x86_chips"]
        if rel_row.get("_arm_chips"):
            snapshot["arm_chips"] = rel_row["_arm_chips"]
        save_snapshot(conn, release_id, app["id"], snapshot)

    conn.commit()
    audit(
        conn,
        f"首次初始化导入完成：release rows={len(release_rows)}, owner rows={len(owner_rows)}",
        release_id=release_id,
        event="import_initial",
    )
    return release_id


def create_release_from_previous(
    conn: sqlite3.Connection,
    name: str,
    *,
    maca_version: str = "",
    app_freeze_deadline: str = "",
    doc_deadline: str = "",
) -> str:
    if not (name or "").strip():
        raise ValueError("新 Release 名称不能为空")
    validate_deadline_order(app_freeze_deadline, doc_deadline)
    releases = list_releases(conn)
    previous = get_release(conn, releases[-1]["id"]) if releases else None
    release_id = new_id("rel")
    release = {
        "id": release_id,
        "name": name,
        "maca_version": maca_version,
        "app_freeze_deadline": app_freeze_deadline,
        "doc_deadline": doc_deadline,
        "released_locked": 0,
        "released_locked_at": "",
        "released_locked_by": "",
        "created_at": now(),
        "source": "cloned_from_previous" if previous else "empty",
        "cloned_from": previous["id"] if previous else "",
    }
    save_release(conn, release)
    for app in list_apps(conn):
        old = previous["snapshots"].get(app["id"]) if previous else None
        snapshot = json.loads(json.dumps(old)) if old else base_snapshot(app)
        snapshot.pop("app_meta", None)
        snapshot.pop("locked_in_release", None)
        snapshot.update(
            {
                "owner_confirmed": False,
                "qa_status": "not_checked",
                "qa_issue_note": "",
                "missing_items": [],
            }
        )
        for td in snapshot.get("test_docs", []):
            td["stale"] = True
        save_snapshot(conn, release_id, app["id"], snapshot)
    conn.commit()
    audit(conn, f"创建 release {name}，沿用上一版本信息", release_id=release_id, event="create_release")
    return release_id


def _future_unlocked_release_ids(conn: sqlite3.Connection, release_id: str) -> list[str]:
    releases = list_releases(conn)
    start = next((idx for idx, release in enumerate(releases) if release["id"] == release_id), None)
    if start is None:
        raise KeyError(f"Unknown release: {release_id}")
    return [release["id"] for release in releases[start:] if not release.get("released_locked")]


def _initial_snapshot_for_future_release(snapshot: dict[str, Any], target_release: dict[str, Any] | None = None) -> dict[str, Any]:
    future = json.loads(json.dumps(snapshot))
    future.pop("app_meta", None)
    future.pop("locked_in_release", None)
    future.update(
        {
            "owner_confirmed": False,
            "qa_status": "not_checked",
            "qa_issue_note": "",
            "missing_items": [],
        }
    )
    if (
        target_release
        and future.get("release_decision") == "release"
        and not is_before(target_release.get("app_freeze_deadline", ""))
    ):
        future["release_decision"] = "cicd_only"
    for test_doc in future.get("test_docs", []):
        test_doc["stale"] = True
    return future


def add_new_app_request(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    official_name: str,
    git_url: str,
    git_branch: str,
    release_decision: str,
    owner: str,
    doc_target: str = "manual",
) -> str:
    raw_decision = (release_decision or "").strip()
    git_url = (git_url or "").strip()
    git_branch = (git_branch or "").strip()
    if not official_name or not git_url or not git_branch or not raw_decision or not owner:
        raise ValueError("New app requires official_name, git_url, git_branch, release_decision, and submitter owner")
    release_decision = normalize_release_decision(raw_decision)
    if release_decision not in RELEASE_DECISIONS:
        raise ValueError(f"Invalid release_decision: {release_decision}")
    doc_target = normalize_doc_target(doc_target)
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可新增 app")
    if release_decision == "release" and not is_before(release.get("app_freeze_deadline", "")):
        raise RuntimeError("已过 app 冻结 deadline，不可再新增以 release 状态进入本期的 app")
    duplicate = conn.execute(
        "SELECT id, name FROM apps WHERE git_url = ? AND git_branch = ?",
        (git_url, git_branch),
    ).fetchone()
    if duplicate:
        raise RuntimeError(
            f"该 Gerrit URL + branch 已登记为 app「{duplicate['name']}」(id={duplicate['id']})："
            f"无需重复新增；如需让它参与本 release，请联系现有 owner 或 RM"
        )
    base_id = normalize_name(official_name)
    if not base_id:
        raise ValueError(f"无法由名称生成有效的 app id：{official_name!r}")
    used_ids = {row["id"] for row in conn.execute("SELECT id FROM apps")}
    app_id = base_id if base_id not in used_ids else variant_app_id(base_id, "", git_branch, used_ids)
    app = {
        "id": app_id,
        "name": official_name,
        "official_name": official_name,
        "category": "",
        "type": "",
        "description": "",
        "git_url": git_url,
        "git_branch": git_branch,
        "official_url": "",
        "doc_target": doc_target,
        "owners": [owner],
        "aliases": [official_name],
        "created_by": owner,
        "created_at": now(),
    }
    save_app(conn, app)
    snapshot = base_snapshot(app)
    snapshot["release_decision"] = release_decision
    synced_release_ids = []
    for target_release_id in _future_unlocked_release_ids(conn, release_id):
        target_release = get_release(conn, target_release_id)
        if app_id in target_release["snapshots"]:
            continue
        target_snapshot = snapshot if target_release_id == release_id else _initial_snapshot_for_future_release(snapshot, target_release)
        save_snapshot(conn, target_release_id, app_id, target_snapshot)
        synced_release_ids.append(target_release_id)
    conn.commit()
    audit(
        conn,
        f"新增 app：{official_name}，owner={owner}，初始决策={release_decision}，同步 release 数={len(synced_release_ids)}",
        user=owner,
        role="Owner",
        app_id=app_id,
        release_id=release_id,
        event="create_app",
    )
    return app_id


def walk_objects(value: Any, visitor: Callable[[dict[str, Any], list[str]], None], path: list[str] | None = None) -> None:
    path = path or []
    if isinstance(value, dict):
        visitor(value, path)
        for key, child in value.items():
            walk_objects(child, visitor, path + [str(key)])
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            walk_objects(child, visitor, path + [str(idx)])


def parse_app_info(raw: str | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    x86_chips: set[str] = set()
    arm_chips: set[str] = set()
    build_targets: list[dict[str, Any]] = []
    test_targets: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []

    for env, cfg in (data.get("app_build") or {}).items():
        if not isinstance(cfg, dict):
            continue
        arch = str(cfg.get("arch") or env)
        chips = cfg.get("supported_chip") if isinstance(cfg.get("supported_chip"), list) else []
        enabled = cfg.get("enabled") is not False
        if enabled:
            target = arm_chips if re.search(r"arm|aarch64", arch, re.I) else x86_chips
            target.update(str(chip).upper() for chip in chips)
        build_targets.append({"path": env, "arch": arch, "chips": chips, "enabled": enabled, "build_target": cfg.get("build_target", "")})

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if "test_cmd" not in node:
            return
        if node.get("enabled") is False:
            return
        if str(node.get("test_period", "")).strip().lower() == "weekly":
            return
        supported = node.get("supported_chip") or {}
        if isinstance(supported, dict):
            chips = list(supported.keys())
            arch_list = sorted({str(v) for values in supported.values() for v in (values if isinstance(values, list) else [values])})
        elif isinstance(supported, list):
            chips = [str(v) for v in supported]
            arch_list = []
        else:
            chips = []
            arch_list = []
        test = {
            "id": ".".join(path),
            "name": path[-1] if path else "test",
            "path": ".".join(path),
            "command": str(node.get("test_cmd") or "").strip(),
            "supported_chips": chips,
            "arch_list": arch_list,
            "enabled": node.get("enabled") is not False,
            "container_args": node.get("container_args", ""),
            "image_target": node.get("img_target", ""),
        }
        tests.append(test)
        test_targets.append(
            {
                "path": test["path"],
                "enabled": test["enabled"],
                "command": test["command"],
                "supported_chips": chips,
                "arch_list": arch_list,
                "container_args": test["container_args"],
                "image_target": test["image_target"],
            }
        )

    walk_objects(data.get("app_test") or {}, visitor)
    return {
        "app_name": data.get("app_name", ""),
        "app_version": data.get("app_version", ""),
        "x86_chips": sorted(x86_chips),
        "arm_chips": sorted(arm_chips),
        "build_targets": build_targets,
        "test_targets": test_targets,
        "tests": tests,
        "raw": data,
    }


def diff_app_info(old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []

    def add(diff_type: str, field: str, old_value: Any, new_value: Any, qa_impact: bool = True) -> None:
        if old_value != new_value:
            diffs.append({"id": new_id("diff"), "type": diff_type, "field": field, "old_value": old_value, "new_value": new_value, "qa_impact": qa_impact, "confirmed": False})

    old = old or {}
    add("版本变化", "app_version", old.get("app_version", ""), new.get("app_version", ""))
    add("X86芯片变化", "x86_chips", old.get("x86_chips", []), new.get("x86_chips", []))
    add("ARM芯片变化", "arm_chips", old.get("arm_chips", []), new.get("arm_chips", []))
    add(
        "Build target变化",
        "build_targets",
        [f"{x.get('path')}:{x.get('enabled')}:{x.get('build_target')}" for x in old.get("build_targets", [])],
        [f"{x.get('path')}:{x.get('enabled')}:{x.get('build_target')}" for x in new.get("build_targets", [])],
    )
    add("Test target变化", "test_targets", old.get("test_targets", []), new.get("test_targets", []))
    old_tests = {t["path"]: t["command"] for t in old.get("tests", [])}
    new_tests = {t["path"]: t["command"] for t in new.get("tests", [])}
    for path, cmd in new_tests.items():
        if path not in old_tests:
            add("test_cmd新增", path, "", cmd)
        elif old_tests[path] != cmd:
            add("test_cmd修改", path, old_tests[path], cmd)
    for path, cmd in old_tests.items():
        if path not in new_tests:
            add("test_cmd删除", path, cmd, "")
    return diffs


def ensure_test_docs(snapshot: dict[str, Any], parsed: dict[str, Any], diffs: list[dict[str, Any]]) -> None:
    snapshot.setdefault("test_docs", [])
    docs_by_path = {doc["path"]: doc for doc in snapshot["test_docs"]}
    current_paths = set()
    for test in parsed.get("tests", []):
        current_paths.add(test["path"])
        doc = docs_by_path.get(test["path"])
        if not doc:
            snapshot["test_docs"].append(
                {
                    "id": new_id("testdoc"),
                    "path": test["path"],
                    "name": test["name"],
                    "command": test["command"],
                    "dataset": "",
                    "content": "",
                    "preconditions": "",
                    "result_view": "",
                    "pass_criteria": "",
                    "coverage": join_list(test.get("supported_chips", [])),
                    "owner_added": False,
                    "stale": True,
                    "obsolete": False,
                }
            )
        else:
            doc["command"] = test["command"]
            doc["obsolete"] = False
            doc["stale"] = True
    for doc in snapshot["test_docs"]:
        if not doc.get("owner_added") and doc["path"] not in current_paths:
            doc["obsolete"] = True


def update_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    mutator: Callable[[dict[str, Any]], None],
    *,
    skip_doc_deadline: bool = False,
) -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改")
    if not skip_doc_deadline and not is_before(release.get("doc_deadline", "")):
        raise RuntimeError("已过 doc deadline，不可再修改文档/表单信息")
    snapshot = release["snapshots"][app_id]
    mutator(snapshot)
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    return snapshot


def apply_app_info(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    raw: str | dict[str, Any],
    *,
    source: str = "upload",
    source_type: str = "owner_upload",
    commit_id: str = "",
    uploaded_by: str = "",
) -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可上传 app_info")
    if not is_before(release.get("doc_deadline", "")):
        raise RuntimeError("已过 doc deadline，不可再上传 app_info")
    snapshot = release["snapshots"][app_id]
    parsed = parse_app_info(raw)
    previous = previous_release(conn, release_id)
    old_parsed = None
    if previous and app_id in previous["snapshots"]:
        old_parsed = (previous["snapshots"][app_id].get("app_info") or {}).get("parsed")
    if old_parsed is None:
        old_parsed = (snapshot.get("app_info") or {}).get("parsed")
    diffs = diff_app_info(old_parsed, parsed) if old_parsed is not None else []
    snapshot["app_info"] = {
        "source": source,
        "source_type": source_type,
        "synced_at": now(),
        "commit_id": commit_id,
        "uploaded_by": uploaded_by,
        "raw": parsed["raw"],
        "parsed": parsed,
    }
    snapshot["app_info_diffs"] = diffs
    snapshot["version"] = parsed.get("app_version") or snapshot.get("version", "")
    snapshot["x86_chips"] = join_list(parsed.get("x86_chips", []))
    snapshot["arm_chips"] = join_list(parsed.get("arm_chips", []))
    ensure_test_docs(snapshot, parsed, diffs)
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    audit(
        conn,
        f"{app_id} 更新 app_info.json，差异 {len(diffs)} 项",
        user=uploaded_by or "system",
        role="Owner",
        app_id=app_id,
        release_id=release_id,
        event="upload_app_info",
    )
    return snapshot


def missing_items_for(app: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    """Readiness and final-release gate items shown to RM/owners."""
    decision = normalize_release_decision(snapshot.get("release_decision"))
    if decision != "release":
        return []
    missing: list[str] = []
    if not app.get("owners"):
        missing.append("缺少 owner")
    if not app.get("git_url"):
        missing.append("缺少 Gerrit URL")
    if not app.get("git_branch"):
        missing.append("缺少 branch")
    if not (app.get("type") or "").strip():
        missing.append("缺少 App类型")
    description = (app.get("description") or "").strip()
    if not description:
        missing.append("缺少描述（30字内）")
    elif len(description) > MAX_APP_DESCRIPTION_CHARS:
        missing.append("描述超过30字")
    if not snapshot.get("app_info"):
        missing.append("缺少可追溯 AppInfoSnapshot")
    if any(not diff.get("confirmed") for diff in snapshot.get("app_info_diffs", [])):
        missing.append("app_info 差异未确认")
    if not snapshot.get("version"):
        missing.append("缺少 对应官方版本")
    if not snapshot.get("x86_chips"):
        missing.append("缺少 X86支持芯片系列")
    if normalize_doc_target(app.get("doc_target")) in DOC_TARGETS:
        doc = snapshot.get("doc", {})
        required = {
            "intro": "基本介绍",
            "image_usage": "镜像使用方法",
            "binary_usage": "二进制包使用方法",
            "env_setup": "环境搭建",
        }
        for key, label in required.items():
            if not doc.get(key):
                missing.append(f"缺少{label}")
    for doc in snapshot.get("test_docs", []):
        if doc.get("obsolete"):
            continue
        if doc.get("owner_added") and not doc.get("command"):
            missing.append(f"{doc['path']} 缺少 owner-added 测试命令")
        for key, label in {"dataset": "测试数据集", "content": "测试内容", "result_view": "结果查看方式", "pass_criteria": "通过标准"}.items():
            if not doc.get(key):
                missing.append(f"{doc['path']} 缺少{label}")
        if doc.get("stale"):
            missing.append(f"{doc['path']} 测试说明 stale")
    if not snapshot.get("owner_confirmed"):
        missing.append("Owner 未确认 doc")
    qa_status = snapshot.get("qa_status", "not_checked")
    if qa_status == "not_checked":
        missing.append("QA 未测试")
    elif qa_status == "cannot_release":
        missing.append("QA 标注为不可发布")
    return missing


def refresh_missing_items(conn: sqlite3.Connection, release_id: str) -> dict[str, list[str]]:
    """Recompute missing_items for every snapshot in the release; return map.

    Only writes back when the recomputed value (or normalized decision) differs
    from what is stored, so /api/state polling doesn't thrash the DB.
    """
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        return {app_id: snap.get("missing_items", []) for app_id, snap in release["snapshots"].items()}
    apps = {app["id"]: app for app in list_apps(conn)}
    results: dict[str, list[str]] = {}
    dirty = False
    for app_id, snapshot in release["snapshots"].items():
        app = apps.get(app_id)
        if not app:
            continue
        before = dumps_json(snapshot)
        snapshot["release_decision"] = normalize_release_decision(snapshot.get("release_decision"))
        snapshot.pop("cicd", None)
        items = missing_items_for(app, snapshot)
        snapshot["missing_items"] = items
        results[app_id] = items
        after = dumps_json(snapshot)
        if before != after:
            save_snapshot(conn, release_id, app_id, snapshot)
            dirty = True
    if dirty:
        conn.commit()
    return results


def qa_set_status(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    status: str,
    *,
    issue_note: str = "",
    user: str = "qa",
    role: str = "QA",
) -> dict[str, Any]:
    if status not in QA_STATUSES:
        raise ValueError(f"Invalid QA status: {status}")
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改 QA 状态")
    snapshot = release["snapshots"].get(app_id)
    if not snapshot:
        raise KeyError(f"App {app_id} not in release")
    if snapshot.get("release_decision") != "release":
        raise RuntimeError("仅 release 决策的 app 可由 QA 标注状态")
    if status == "has_issues" and not (issue_note or "").strip():
        raise ValueError("标注「存在问题」时必须填写问题说明")
    snapshot["qa_status"] = status
    snapshot["qa_issue_note"] = (issue_note or "").strip() if status == "has_issues" else ""
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    audit(
        conn,
        f"QA 标注 {app_id} 为 {status}" + (f"：{issue_note}" if issue_note else ""),
        user=user,
        role=role,
        app_id=app_id,
        release_id=release_id,
        event="qa_set_status",
    )
    return snapshot


def qa_set_status_batch(
    conn: sqlite3.Connection,
    release_id: str,
    items: list[dict[str, Any]],
    *,
    user: str = "qa",
    role: str = "QA",
) -> dict[str, dict[str, Any]]:
    """Apply several QA-status updates atomically.

    Every item is validated first; if any item is invalid the whole batch is
    rejected and nothing is written. Only on full success is a single commit
    issued, so a mid-batch failure can never leave a partial save.
    """
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可修改 QA 状态")
    prepared: list[tuple[str, dict[str, Any], str, str]] = []
    errors: list[str] = []
    for item in items:
        app_id = item.get("app_id", "")
        status = item.get("status", "")
        issue_note = (item.get("issue_note") or "").strip()
        if status not in QA_STATUSES:
            errors.append(f"{app_id}：无效的 QA 状态 {status!r}")
            continue
        snapshot = release["snapshots"].get(app_id)
        if not snapshot:
            errors.append(f"{app_id}：不在本 release 中")
            continue
        if snapshot.get("release_decision") != "release":
            errors.append(f"{app_id}：仅 release 决策的 app 可标注 QA 状态")
            continue
        if status == "has_issues" and not issue_note:
            errors.append(f"{app_id}：标注「存在问题」时必须填写问题说明")
            continue
        prepared.append((app_id, snapshot, status, issue_note))
    if errors:
        raise ValueError("；".join(errors))
    for app_id, snapshot, status, issue_note in prepared:
        snapshot["qa_status"] = status
        snapshot["qa_issue_note"] = issue_note if status == "has_issues" else ""
        save_snapshot(conn, release_id, app_id, snapshot)
        audit(
            conn,
            f"QA 标注 {app_id} 为 {status}" + (f"：{issue_note}" if issue_note else ""),
            user=user,
            role=role,
            app_id=app_id,
            release_id=release_id,
            event="qa_set_status",
            commit=False,
        )
    conn.commit()
    return {app_id: snapshot for app_id, snapshot, _, _ in prepared}


def qa_log_dir(db_path: str | Path) -> Path:
    base = Path(db_path).resolve().parent / "qa_logs"
    base.mkdir(exist_ok=True)
    return base


def qa_upload_log(
    conn: sqlite3.Connection,
    db_path: str | Path,
    release_id: str,
    content: bytes,
    filename: str,
    *,
    user: str = "qa",
    role: str = "QA",
) -> dict[str, str]:
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，不可上传 QA log")
    if not filename:
        raise ValueError("filename required")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "qa_log"
    target_dir = qa_log_dir(db_path)
    storage_path = target_dir / f"{release_id}__{safe_name}"
    storage_path.write_bytes(content)
    conn.execute(
        """
        INSERT INTO qa_logs(release_id, filename, storage_path, uploaded_at, uploaded_by)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(release_id) DO UPDATE SET
          filename=excluded.filename,
          storage_path=excluded.storage_path,
          uploaded_at=excluded.uploaded_at,
          uploaded_by=excluded.uploaded_by
        """,
        (release_id, safe_name, str(storage_path), now(), user),
    )
    conn.commit()
    audit(
        conn,
        f"QA 上传 log：{safe_name}",
        user=user,
        role=role,
        release_id=release_id,
        event="qa_upload_log",
    )
    return {"filename": safe_name, "storage_path": str(storage_path), "uploaded_at": now(), "uploaded_by": user}


def get_qa_log(conn: sqlite3.Connection, release_id: str) -> dict[str, str] | None:
    row = conn.execute(
        "SELECT release_id, filename, storage_path, uploaded_at, uploaded_by FROM qa_logs WHERE release_id = ?",
        (release_id,),
    ).fetchone()
    return dict(row) if row else None


def export_test_scope_csv(conn: sqlite3.Connection, release_id: str) -> str:
    """Return CSV text of release-decision=release apps in the release."""
    release = get_release(conn, release_id)
    apps = {app["id"]: app for app in list_apps(conn)}
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
        rows.append(
            (
                app.get("name", ""),
                snapshot.get("version", ""),
                app.get("git_url", ""),
                app.get("git_branch", ""),
                ",".join(app.get("owners", [])),
            )
        )
    rows.sort(key=lambda r: r[0].lower())
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def final_lock_release(conn: sqlite3.Connection, release_id: str, *, user: str = "rm", role: str = "RM") -> dict[str, str]:
    """Final lock: snapshot app_meta, freeze all writes, generate final artifacts."""
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定")
    refresh_missing_items(conn, release_id)
    release = get_release(conn, release_id)
    apps_by_id = {app["id"]: app for app in list_apps(conn)}
    for app_id, snapshot in release["snapshots"].items():
        if _qualifies_for_final(snapshot):
            app = apps_by_id.get(app_id)
            if app:
                snapshot["app_meta"] = {
                    "id": app["id"],
                    "name": app["name"],
                    "official_name": app["official_name"],
                    "category": app["category"],
                    "type": app["type"],
                    "description": app["description"],
                    "git_url": app["git_url"],
                    "git_branch": app["git_branch"],
                    "official_url": app.get("official_url", ""),
                    "doc_target": app["doc_target"],
                    "owners": app["owners"],
                }
            snapshot["locked_in_release"] = True
        save_snapshot_raw(conn, release_id, app_id, snapshot)
    conn.execute(
        "UPDATE releases SET released_locked = 1, released_locked_at = ?, released_locked_by = ? WHERE id = ?",
        (now(), user, release_id),
    )
    conn.commit()
    artifacts = generate_artifacts(conn, release_id, final=True, from_lock=True)
    audit(conn, f"Release 最终锁定：{release['name']}", user=user, role=role, release_id=release_id, event="final_lock")
    return artifacts


def final_unlock_release(conn: sqlite3.Connection, release_id: str, *, user: str = "rm", role: str = "RM") -> None:
    """Reverse a final lock: clear app_meta + locked flag, delete final artifacts."""
    release = get_release(conn, release_id)
    if not release.get("released_locked"):
        raise RuntimeError("Release 未锁定，无需解锁")
    conn.execute(
        "UPDATE releases SET released_locked = 0, released_locked_at = '', released_locked_by = '' WHERE id = ?",
        (release_id,),
    )
    for app_id, snapshot in release["snapshots"].items():
        snapshot.pop("app_meta", None)
        snapshot.pop("locked_in_release", None)
        save_snapshot_raw(conn, release_id, app_id, snapshot)
    conn.execute("DELETE FROM artifacts WHERE release_id = ? AND final = 1", (release_id,))
    conn.commit()
    audit(conn, f"Release 解锁：{release['name']}", user=user, role=role, release_id=release_id, event="final_unlock")


def save_snapshot_raw(conn: sqlite3.Connection, release_id: str, app_id: str, snapshot: dict[str, Any]) -> None:
    """Save snapshot WITHOUT lock checks; used by lock/unlock themselves."""
    conn.execute(
        """
        INSERT INTO snapshots(release_id, app_id, data_json)
        VALUES (?, ?, ?)
        ON CONFLICT(release_id, app_id) DO UPDATE SET data_json=excluded.data_json
        """,
        (release_id, app_id, dumps_json(snapshot)),
    )


def _qualifies_for_final(snapshot: dict[str, Any]) -> bool:
    """True if this snapshot should be included in the final release_note/manuals."""
    if snapshot.get("release_decision") != "release":
        return False
    if not snapshot.get("owner_confirmed"):
        return False
    if _docs_gate_items(snapshot):
        return False
    if snapshot.get("qa_status") in {"qa_passed", "has_issues"}:
        return True
    return False


def _docs_gate_items(snapshot: dict[str, Any]) -> list[str]:
    return [item for item in snapshot.get("missing_items", []) if not str(item).startswith("QA ")]


def rst_title(title: str, marker: str = "=") -> str:
    return f"{title}\n{marker * len(title)}\n\n"


def code_block(content: str) -> str:
    if not content:
        return "\n"
    body = "\n".join(f"   {line}" for line in content.splitlines())
    return f".. code-block:: shell\n\n{body}\n\n"


def release_rows(conn: sqlite3.Connection, release: dict[str, Any], *, final: bool = False) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Rows for the release note.

    Preview and final RST both include only apps that currently qualify for
    release. Unfinished apps stay visible in missing_items, not generated RST.
    """
    apps = {app["id"]: app for app in list_apps(conn)}
    rows = []
    for app_id, snapshot in release["snapshots"].items():
        app = snapshot.get("app_meta") or apps.get(app_id)
        if not app or snapshot.get("release_decision") != "release":
            continue
        if not _qualifies_for_final(snapshot):
            continue
        rows.append((app, snapshot))
    return sorted(rows, key=lambda item: item[0]["name"].lower())


def guide_rows(
    conn: sqlite3.Connection,
    release: dict[str, Any],
    doc_target: str,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Return ``(active_rows, stopped_rows)`` for a manual/ai4sci guide."""
    apps = {app["id"]: app for app in list_apps(conn)}
    active: list[tuple[dict[str, Any], dict[str, Any]]] = []
    stopped: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for app_id, snapshot in release["snapshots"].items():
        app = snapshot.get("app_meta") or apps.get(app_id)
        if not app:
            continue
        if normalize_doc_target(app.get("doc_target")) != doc_target:
            continue

        decision = snapshot.get("release_decision", "release")
        qualifies = _qualifies_for_final(snapshot)

        if decision == "stopped":
            prev = last_published_snapshot(conn, app_id, release["id"])
            if prev:
                prev_app = prev.get("app_meta") or app
                stopped.append((prev_app, prev))
            continue

        if decision != "release":
            continue

        prev = last_published_snapshot(conn, app_id, release["id"])

        if qualifies:
            active.append((app, snapshot))
        elif prev:
            prev_app = prev.get("app_meta") or app
            active.append((prev_app, prev))
        # else: new app that didn't qualify — omit

    active.sort(key=lambda item: item[0]["name"].lower())
    stopped.sort(key=lambda item: item[0]["name"].lower())
    return active, stopped


def render_release_note(release: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    out = rst_title(f"MACA HPC 发布列表 - {release['name']}")
    out += ".. list-table::\n   :header-rows: 1\n   :widths: 15 15 30 12 14 14\n\n"
    out += "   * - 名称\n     - 类型\n     - 描述\n     - 对应官方版本\n     - X86支持芯片系列\n     - ARM支持芯片类型\n"
    for app, snapshot in rows:
        out += f"   * - {app['name']}\n     - {app.get('type') or app.get('category') or ''}\n     - {app.get('description') or ''}\n     - {snapshot.get('version') or ''}\n     - {snapshot.get('x86_chips') or ''}\n     - {snapshot.get('arm_chips') or ''}\n"
    return out


def _merged_limitations(snapshot: dict[str, Any]) -> str:
    """Merge owner-written limitations with QA's issue note if any."""
    text = (snapshot.get("doc", {}) or {}).get("limitations", "") or ""
    if snapshot.get("qa_status") == "has_issues" and snapshot.get("qa_issue_note"):
        prefix = f"QA 备注：{snapshot['qa_issue_note']}"
        text = f"{text}\n\n{prefix}".strip() if text else prefix
    return text


def _chip_support_text(snapshot: dict[str, Any]) -> str:
    parts = []
    if snapshot.get("x86_chips"):
        parts.append(f"X86: {snapshot['x86_chips']}")
    if snapshot.get("arm_chips"):
        parts.append(f"ARM: {snapshot['arm_chips']}")
    if snapshot.get("hpcc_chip"):
        parts.append(f"HPCC: {snapshot['hpcc_chip']}")
    return "; ".join(parts)


def _not_releasable_reason(snapshot: dict[str, Any]) -> str:
    decision = normalize_release_decision(snapshot.get("release_decision"))
    if _qualifies_for_final(snapshot):
        return ""
    if decision != "release":
        return f"Release决策为 {decision}"
    missing = snapshot.get("missing_items", [])
    return "；".join(missing) if missing else "未满足发布条件"


def render_manager_review_csv(
    conn: sqlite3.Connection,
    release: dict[str, Any],
    fields: list[str] | None = None,
) -> str:
    """Return manager-review CSV for all apps in the release snapshots."""
    field_labels = dict(MANAGER_REVIEW_FIELDS)
    selected = fields or DEFAULT_MANAGER_REVIEW_FIELDS
    if not selected:
        raise ValueError("至少选择一个输出字段")
    invalid = [field for field in selected if field not in field_labels]
    if invalid:
        raise ValueError(f"未知 Manager Review 字段: {', '.join(invalid)}")

    apps = {app["id"]: app for app in list_apps(conn)}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([field_labels[field] for field in selected])
    rows = []
    for app_id, snapshot in release["snapshots"].items():
        app = snapshot.get("app_meta") or apps.get(app_id)
        if not app:
            continue
        rows.append((app, snapshot))
    rows.sort(key=lambda item: item[0].get("name", "").lower())
    for app, snapshot in rows:
        values = {
            "app_name": app.get("name", ""),
            "official_name": app.get("official_name", ""),
            "doc_target": "AI4Sci" if normalize_doc_target(app.get("doc_target")) == "ai4sci" else "HPC",
            "app_type": app.get("type", ""),
            "version": snapshot.get("version", ""),
            "owners": ",".join(app.get("owners", [])),
            "chip_support": _chip_support_text(snapshot),
            "x86_chips": snapshot.get("x86_chips", ""),
            "arm_chips": snapshot.get("arm_chips", ""),
            "release_decision": normalize_release_decision(snapshot.get("release_decision")),
            "qa_status": snapshot.get("qa_status", "not_checked"),
            "owner_confirmed": "是" if snapshot.get("owner_confirmed") else "否",
            "releasable": "是" if _qualifies_for_final(snapshot) else "否",
            "not_releasable_reason": _not_releasable_reason(snapshot),
            "known_limitations": _merged_limitations(snapshot),
            "gerrit_url": app.get("git_url", ""),
            "git_branch": app.get("git_branch", ""),
        }
        writer.writerow([values[field] for field in selected])
    return out.getvalue()


def _render_guide_entries(rows: list[tuple[dict[str, Any], dict[str, Any]]], *, stopped: bool = False) -> str:
    out = ""
    for app, snapshot in rows:
        doc = snapshot.get("doc", {})
        heading = f"{app['name']}（已停止支持）" if stopped else app["name"]
        out += rst_title(heading, "-")
        out += f"{doc.get('intro') or app.get('description') or ''}\n\n"
        out += f"版本：{snapshot.get('version') or ''}\n\n"
        if app.get("official_url"):
            out += f"官方网址：{app['official_url']}\n\n"
        out += "**镜像使用方法：**\n\n" + code_block(doc.get("image_usage", ""))
        out += "**二进制包使用方法：**\n\n" + code_block(doc.get("binary_usage", ""))
        out += "**环境搭建：**\n\n" + code_block(doc.get("env_setup", ""))
        out += "**测试方法：**\n\n"
        for test_doc in snapshot.get("test_docs", []):
            if test_doc.get("obsolete"):
                continue
            out += f"- {test_doc['path']}\n\n"
            out += f"  - 测试数据集：{test_doc.get('dataset', '')}\n"
            out += f"  - 测试内容：{test_doc.get('content', '')}\n"
            out += f"  - 结果查看：{test_doc.get('result_view', '')}\n"
            out += f"  - 通过标准：{test_doc.get('pass_criteria', '')}\n\n"
            if test_doc.get("command"):
                out += code_block(test_doc["command"])
        limits = _merged_limitations(snapshot)
        if limits:
            out += f"**已知限制：**\n\n{limits}\n\n"
    return out


def render_guide(
    title: str,
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    stopped_rows: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> str:
    out = rst_title(title)
    out += _render_guide_entries(rows)
    if stopped_rows:
        out += _render_guide_entries(stopped_rows, stopped=True)
    return out


def generate_artifacts(conn: sqlite3.Connection, release_id: str, *, final: bool = False, from_lock: bool = False) -> dict[str, str]:
    release = get_release(conn, release_id)
    if final and not from_lock:
        raise RuntimeError("Final artifacts 只能通过 final_lock_release 生成")
    if from_lock and not final:
        raise RuntimeError("Lock generation must create final artifacts")
    if from_lock and not release.get("released_locked"):
        raise RuntimeError("Final artifacts require a locked release")
    if release.get("released_locked") and not from_lock:
        raise RuntimeError("Release 已最终锁定，artifacts 不可重新生成")
    if final:
        existing = conn.execute("SELECT 1 FROM artifacts WHERE release_id = ? AND final = 1 LIMIT 1", (release_id,)).fetchone()
        if existing:
            raise RuntimeError("Final artifacts already exist and are immutable")
    if not final:
        refresh_missing_items(conn, release_id)
        release = get_release(conn, release_id)
    rows = release_rows(conn, release, final=final)
    if final:
        manual_active, manual_stopped = guide_rows(conn, release, "manual")
        ai4sci_active, ai4sci_stopped = guide_rows(conn, release, "ai4sci")
    else:
        manual_active = [(a, s) for a, s in rows if normalize_doc_target(a.get("doc_target")) == "manual"]
        manual_stopped = []
        ai4sci_active = [(a, s) for a, s in rows if normalize_doc_target(a.get("doc_target")) == "ai4sci"]
        ai4sci_stopped = []
    artifacts = {
        "release_note": render_release_note(release, rows),
        "manual": render_guide("HPC Manual App 章节", manual_active, manual_stopped),
        "ai4sci": render_guide("AI4Sci User Guide App 章节", ai4sci_active, ai4sci_stopped),
        "data": dumps_json({"release": release, "apps": list_apps(conn), "generated_at": now(), "final": final}),
    }
    names = {
        "release_note": "release_note.rst",
        "manual": "hpc_manual_apps.rst",
        "ai4sci": "ai4sci_user_guide_apps.rst",
        "data": "release_data.json",
    }
    for kind, content in artifacts.items():
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
            (release_id, kind, names[kind], content, int(final), now()),
        )
    conn.commit()
    audit(
        conn,
        "生成最终 artifacts" if final else "生成预览 artifacts",
        release_id=release_id,
        event="generate_artifacts",
    )
    return artifacts


def generate_manager_review_csv(
    conn: sqlite3.Connection,
    release_id: str,
    fields: list[str] | None = None,
    *,
    user: str = "rm",
    role: str = "RM",
) -> str:
    release = get_release(conn, release_id)
    if release.get("released_locked"):
        raise RuntimeError("Release 已最终锁定，Manager Review CSV 不可重新生成")
    refresh_missing_items(conn, release_id)
    release = get_release(conn, release_id)
    content = render_manager_review_csv(conn, release, fields)
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
        (release_id, "manager_review", "manager_review.csv", content, 0, now()),
    )
    conn.commit()
    audit(
        conn,
        "生成 Manager Review CSV",
        user=user,
        role=role,
        release_id=release_id,
        event="generate_manager_review",
    )
    return content


def gerrit_push_plan(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    release = get_release(conn, release_id)
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
            "copy generated RST artifacts into docs-worktree",
            f"git -C docs-worktree push origin HEAD:refs/for/{branch}",
            f"git clone {data_remote} release-data-worktree",
            f"git -C release-data-worktree checkout -b {branch}",
            "copy release_data.json into release-data-worktree",
            f"git -C release-data-worktree push origin HEAD:refs/for/{branch}",
        ],
    }
