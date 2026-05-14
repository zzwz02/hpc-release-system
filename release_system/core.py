from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Callable


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


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
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


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
            doc_target TEXT NOT NULL DEFAULT 'manual',
            owners_json TEXT NOT NULL DEFAULT '[]',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_by TEXT NOT NULL DEFAULT 'import'
        );

        CREATE TABLE IF NOT EXISTS releases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            maca_version TEXT NOT NULL DEFAULT '',
            deadline TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'owner_filling',
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            cloned_from TEXT NOT NULL DEFAULT '',
            locked_at TEXT NOT NULL DEFAULT ''
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

        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            user TEXT NOT NULL,
            role TEXT NOT NULL,
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
    ensure_default_user(conn)
    conn.commit()


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    salt, expected = encoded.split("$", 1)
    return secrets.compare_digest(hash_password(password, salt).split("$", 1)[1], expected)


def ensure_default_user(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM users WHERE username = ?", ("rm",)).fetchone():
        return
    conn.execute("INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?)", ("rm", hash_password("rm"), "RM"))


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str = "Owner") -> None:
    conn.execute(
        "INSERT INTO users(username, password_hash, role) VALUES (?, ?, ?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role",
        (username, hash_password(password), role),
    )
    conn.commit()


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


def audit(conn: sqlite3.Connection, message: str, user: str = "system", role: str = "system") -> None:
    conn.execute(
        "INSERT INTO audit(ts, user, role, message) VALUES (?, ?, ?, ?)",
        (now(), user, role, message),
    )
    conn.commit()


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]


def parse_csv_text(text: str) -> list[dict[str, str]]:
    import io

    return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(io.StringIO(text.lstrip("\ufeff")))]


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
        "doc_target": "manual",
        "owners": [],
        "aliases": [],
        "created_by": "import",
    }
    base["aliases"] = sorted(set(base.get("aliases", []) + [name]))
    return base


def combined_release_row(rows: list[dict[str, str]]) -> dict[str, str]:
    """Combine per-arch CSV rows for the same app/version/branch variant."""
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
    return data


def save_app(conn: sqlite3.Connection, app: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO apps(id, name, official_name, category, type, description, git_url, git_branch,
                         doc_target, owners_json, aliases_json, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          official_name=excluded.official_name,
          category=excluded.category,
          type=excluded.type,
          description=excluded.description,
          git_url=excluded.git_url,
          git_branch=excluded.git_branch,
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
            app.get("doc_target", "manual"),
            dumps_json(sorted(set(app.get("owners", [])))),
            dumps_json(sorted(set(app.get("aliases", [])))),
            app.get("created_by", "import"),
        ),
    )


def get_app(conn: sqlite3.Connection, app_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM apps WHERE id = ?", (app_id,)).fetchone()
    if not row:
        raise KeyError(f"Unknown app: {app_id}")
    return row_to_app(row)


def list_apps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [row_to_app(row) for row in conn.execute("SELECT * FROM apps ORDER BY name")]


def base_snapshot(app: dict[str, Any], release_row: dict[str, str] | None = None, owner_row: dict[str, str] | None = None) -> dict[str, Any]:
    release_row = release_row or {}
    owner_row = owner_row or {}
    return {
        "app_id": app["id"],
        "release_decision": "release",
        "lifecycle": "active",
        "qa_status": "not_checked",
        "owner_confirmed": False,
        "rm_admitted": False,
        "locked": False,
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
        "cicd": {"enabled": False, "build": "", "test": "", "infra_note": ""},
        "app_info": None,
        "app_info_diffs": [],
        "test_docs": [],
        "blockers": [],
        "change_requests": [],
    }


def save_release(conn: sqlite3.Connection, release: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO releases(id, name, maca_version, deadline, state, created_at, source, cloned_from, locked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          maca_version=excluded.maca_version,
          deadline=excluded.deadline,
          state=excluded.state,
          created_at=excluded.created_at,
          source=excluded.source,
          cloned_from=excluded.cloned_from,
          locked_at=excluded.locked_at
        """,
        (
            release["id"],
            release["name"],
            release.get("maca_version", ""),
            release.get("deadline", ""),
            release.get("state", "owner_filling"),
            release.get("created_at", now()),
            release.get("source", "manual"),
            release.get("cloned_from", ""),
            release.get("locked_at", ""),
        ),
    )


def release_is_locked(conn: sqlite3.Connection, release_id: str) -> bool:
    row = conn.execute("SELECT state FROM releases WHERE id = ?", (release_id,)).fetchone()
    return bool(row and row["state"] == "release_locked")


def save_snapshot(conn: sqlite3.Connection, release_id: str, app_id: str, snapshot: dict[str, Any], *, allow_locked: bool = False) -> None:
    if not allow_locked:
        existing = conn.execute("SELECT data_json FROM snapshots WHERE release_id = ? AND app_id = ?", (release_id, app_id)).fetchone()
        if release_is_locked(conn, release_id) or (existing and loads_json(existing["data_json"], {}).get("locked")):
            raise RuntimeError("Release snapshot is locked and immutable")
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
    release["snapshots"] = {
        snap["app_id"]: loads_json(snap["data_json"], {})
        for snap in conn.execute("SELECT app_id, data_json FROM snapshots WHERE release_id = ?", (release_id,))
    }
    return release


def list_releases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute("SELECT * FROM releases ORDER BY created_at")]


def previous_release(conn: sqlite3.Connection, release_id: str) -> dict[str, Any] | None:
    releases = list_releases(conn)
    for i, release in enumerate(releases):
        if release["id"] == release_id and i > 0:
            return get_release(conn, releases[i - 1]["id"])
    return None


def import_initial(
    conn: sqlite3.Connection,
    release_csv: str | Path,
    owner_csv: str | Path,
    *,
    alias_text: str = "",
    release_name: str | None = None,
    maca_version: str | None = None,
    deadline: str = "",
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
        deadline=deadline,
    )


def import_initial_rows(
    conn: sqlite3.Connection,
    release_rows: list[dict[str, str]],
    owner_rows: list[dict[str, str]],
    *,
    alias_text: str = "",
    release_name: str | None = None,
    maca_version: str | None = None,
    deadline: str = "",
) -> str:
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

    release_id = new_id("rel")
    first_release = {
        "id": release_id,
        "name": release_name or maca_version or release_rows[0].get("maca_version") or "initial-release",
        "maca_version": maca_version or release_rows[0].get("maca_version", ""),
        "deadline": deadline,
        "state": "owner_filling",
        "created_at": now(),
        "source": "initial_csv",
        "cloned_from": "",
        "locked_at": "",
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
    audit(conn, f"首次初始化导入完成：release rows={len(release_rows)}, owner rows={len(owner_rows)}")
    return release_id


def create_release_from_previous(conn: sqlite3.Connection, name: str, *, maca_version: str = "", deadline: str = "") -> str:
    releases = list_releases(conn)
    previous = get_release(conn, releases[-1]["id"]) if releases else None
    release_id = new_id("rel")
    release = {
        "id": release_id,
        "name": name,
        "maca_version": maca_version,
        "deadline": deadline,
        "state": "owner_filling",
        "created_at": now(),
        "source": "cloned_from_previous" if previous else "empty",
        "cloned_from": previous["id"] if previous else "",
        "locked_at": "",
    }
    save_release(conn, release)
    for app in list_apps(conn):
        old = previous["snapshots"].get(app["id"]) if previous else None
        snapshot = json.loads(json.dumps(old)) if old else base_snapshot(app)
        snapshot.pop("app_meta", None)
        snapshot.update({"owner_confirmed": False, "rm_admitted": False, "qa_status": "not_checked", "locked": False, "blockers": [], "change_requests": []})
        save_snapshot(conn, release_id, app["id"], snapshot)
    conn.commit()
    audit(conn, f"创建 release {name}，沿用上一版本信息")
    return release_id


def add_new_app_request(
    conn: sqlite3.Connection,
    release_id: str,
    *,
    official_name: str,
    git_url: str,
    git_branch: str,
    owner: str,
    doc_target: str = "manual",
) -> str:
    """Create a new app from the three owner-provided fields plus submitter owner."""
    if not official_name or not git_url or not git_branch or not owner:
        raise ValueError("New app requires official_name, git_url, git_branch, and submitter owner")
    release = get_release(conn, release_id)
    if release.get("state") in {"qa_open", "release_locked"}:
        raise RuntimeError("New apps cannot be added after QA starts")
    app_id = normalize_name(official_name)
    app = {
        "id": app_id,
        "name": official_name,
        "official_name": official_name,
        "category": "",
        "type": "",
        "description": "",
        "git_url": git_url,
        "git_branch": git_branch,
        "doc_target": doc_target,
        "owners": [owner],
        "aliases": [official_name],
        "created_by": owner,
    }
    save_app(conn, app)
    snapshot = base_snapshot(app)
    snapshot["change_requests"].append({"id": new_id("chg"), "type": "new_app", "status": "submitted", "created_at": now(), "owner": owner})
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    audit(conn, f"新增 app 申请：{official_name}，owner={owner}")
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
            target.update(str(chip) for chip in chips)
        build_targets.append({"path": env, "arch": arch, "chips": chips, "enabled": enabled, "build_target": cfg.get("build_target", "")})

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if "test_cmd" not in node:
            return
        supported = node.get("supported_chip") or {}
        if isinstance(supported, dict):
            chips = list(supported.keys())
            arch_list = sorted({str(v) for values in supported.values() for v in (values if isinstance(values, list) else [values])})
            for chip, arch_values in supported.items():
                values = arch_values if isinstance(arch_values, list) else [arch_values]
                has_arm = any(re.search(r"arm|aarch64", str(arch), re.I) for arch in values)
                has_x86 = any(re.search(r"x86|amd64", str(arch), re.I) for arch in values)
                if has_arm:
                    arm_chips.add(str(chip))
                if has_x86 or not has_arm:
                    x86_chips.add(str(chip))
        elif isinstance(supported, list):
            chips = [str(v) for v in supported]
            arch_list = []
            x86_chips.update(chips)
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
    changed_paths = {diff["field"] for diff in diffs if diff["type"].startswith("test_cmd")}
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
                    "stale": False,
                    "obsolete": False,
                }
            )
        else:
            doc["command"] = test["command"]
            doc["obsolete"] = False
            if test["path"] in changed_paths:
                doc["stale"] = True
    for doc in snapshot["test_docs"]:
        if not doc.get("owner_added") and doc["path"] not in current_paths:
            doc["obsolete"] = True


def assert_unlocked(snapshot: dict[str, Any]) -> None:
    if snapshot.get("locked"):
        raise RuntimeError("Release snapshot is locked and immutable")


def update_snapshot(
    conn: sqlite3.Connection,
    release_id: str,
    app_id: str,
    mutator: Callable[[dict[str, Any]], None],
    *,
    allow_qa_change: bool = False,
) -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("state") == "qa_open" and not allow_qa_change:
        raise RuntimeError("QA is open; key field changes require RM-approved change request workflow")
    snapshot = release["snapshots"][app_id]
    assert_unlocked(snapshot)
    mutator(snapshot)
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    return snapshot


def mark_qa_passed(conn: sqlite3.Connection, release_id: str, app_id: str) -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("state") != "qa_open":
        raise RuntimeError("QA pass can only be recorded while QA is open")
    snapshot = release["snapshots"][app_id]
    assert_unlocked(snapshot)
    if snapshot.get("release_decision") != "release" or not snapshot.get("rm_admitted"):
        raise RuntimeError("Only RM-admitted release apps can be marked QA passed")
    blockers = blockers_for(get_app(conn, app_id), snapshot)
    if blockers:
        raise RuntimeError(f"Cannot mark QA passed; blockers remain: {blockers}")
    snapshot["qa_status"] = "qa_passed"
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    audit(conn, f"{app_id} QA passed")
    return snapshot


def apply_app_info(conn: sqlite3.Connection, release_id: str, app_id: str, raw: str | dict[str, Any], *, source: str = "upload") -> dict[str, Any]:
    release = get_release(conn, release_id)
    if release.get("state") == "qa_open":
        raise RuntimeError("QA is open; app_info changes require RM-approved reduction workflow")
    snapshot = release["snapshots"][app_id]
    assert_unlocked(snapshot)
    parsed = parse_app_info(raw)
    previous = previous_release(conn, release_id)
    old_parsed = None
    if previous and app_id in previous["snapshots"]:
        old_parsed = (previous["snapshots"][app_id].get("app_info") or {}).get("parsed")
    if old_parsed is None:
        old_parsed = (snapshot.get("app_info") or {}).get("parsed")
    diffs = diff_app_info(old_parsed, parsed) if old_parsed is not None else []
    snapshot["app_info"] = {"source": source, "synced_at": now(), "raw": parsed["raw"], "parsed": parsed}
    snapshot["app_info_diffs"] = diffs
    snapshot["version"] = parsed.get("app_version") or snapshot.get("version", "")
    snapshot["x86_chips"] = join_list(parsed.get("x86_chips", [])) or snapshot.get("x86_chips", "")
    snapshot["arm_chips"] = join_list(parsed.get("arm_chips", [])) or snapshot.get("arm_chips", "")
    ensure_test_docs(snapshot, parsed, diffs)
    save_snapshot(conn, release_id, app_id, snapshot)
    conn.commit()
    audit(conn, f"{app_id} 更新 app_info.json，差异 {len(diffs)} 项")
    return snapshot


def blockers_for(app: dict[str, Any], snapshot: dict[str, Any]) -> list[str]:
    decision = snapshot.get("release_decision")
    if decision not in {"release", "cicd_only"} and not (decision == "no_release" and snapshot.get("cicd", {}).get("enabled")):
        return []
    blockers: list[str] = []
    if not app.get("owners"):
        blockers.append("缺少 owner")
    if not app.get("git_url"):
        blockers.append("缺少 Gerrit URL")
    if not app.get("git_branch"):
        blockers.append("缺少 branch")
    if not snapshot.get("app_info"):
        blockers.append("缺少可追溯 AppInfoSnapshot")
    if decision != "release":
        cicd = snapshot.get("cicd", {})
        if not cicd.get("enabled"):
            blockers.append("CICD app 未启用 CICD 配置")
        if not cicd.get("build"):
            blockers.append("缺少 CICD build 配置")
        if not cicd.get("test"):
            blockers.append("缺少 CICD test 配置")
        if not cicd.get("infra_note"):
            blockers.append("缺少 Infra 备注")
        return blockers
    if any(not diff.get("confirmed") for diff in snapshot.get("app_info_diffs", [])):
        blockers.append("app_info 差异未确认")
    if not snapshot.get("version"):
        blockers.append("缺少 对应官方版本")
    if not snapshot.get("x86_chips"):
        blockers.append("缺少 X86支持芯片系列")
    if app.get("doc_target") in {"manual", "ai4sci", "both"}:
        doc = snapshot.get("doc", {})
        required = {
            "intro": "基本介绍",
            "image_usage": "镜像使用方法",
            "binary_usage": "二进制包使用方法",
            "env_setup": "环境搭建",
            "test_method": "测试方法",
            "test_result": "测试结果查看",
        }
        for key, label in required.items():
            if not doc.get(key):
                blockers.append(f"缺少{label}")
    for doc in snapshot.get("test_docs", []):
        if doc.get("obsolete"):
            continue
        if doc.get("owner_added") and not doc.get("command"):
            blockers.append(f"{doc['path']} 缺少 owner-added 测试命令")
        for key, label in {"dataset": "测试数据集", "content": "测试内容", "result_view": "结果查看方式", "pass_criteria": "通过标准"}.items():
            if not doc.get(key):
                blockers.append(f"{doc['path']} 缺少{label}")
        if doc.get("stale"):
            blockers.append(f"{doc['path']} 测试说明 stale")
    if not snapshot.get("owner_confirmed"):
        blockers.append("owner 未确认")
    return blockers


def run_admission_check(conn: sqlite3.Connection, release_id: str) -> dict[str, list[str]]:
    release = get_release(conn, release_id)
    if release.get("state") == "release_locked":
        raise RuntimeError("Release is locked and immutable")
    results: dict[str, list[str]] = {}
    for app in list_apps(conn):
        snapshot = release["snapshots"].get(app["id"])
        if not snapshot:
            continue
        blockers = blockers_for(app, snapshot)
        snapshot["blockers"] = blockers
        if snapshot.get("release_decision") != "release":
            snapshot["qa_status"] = "not_in_release"
        elif blockers:
            snapshot["qa_status"] = "blocked"
        elif snapshot.get("rm_admitted") and snapshot.get("qa_status") in {"in_qa", "qa_passed", "passed"}:
            snapshot["qa_status"] = snapshot.get("qa_status")
        else:
            snapshot["qa_status"] = "eligible"
        save_snapshot(conn, release_id, app["id"], snapshot)
        results[app["id"]] = blockers
    conn.execute("UPDATE releases SET state = ? WHERE id = ?", ("qa_admission_check", release_id))
    conn.commit()
    audit(conn, f"完成 QA 准入检查：{release['name']}")
    return results


def admission_blockers(conn: sqlite3.Connection, release_id: str) -> dict[str, list[str]]:
    release = get_release(conn, release_id)
    results: dict[str, list[str]] = {}
    for app in list_apps(conn):
        snapshot = release["snapshots"].get(app["id"])
        if snapshot:
            results[app["id"]] = blockers_for(app, snapshot)
    return results


def open_qa(conn: sqlite3.Connection, release_id: str) -> None:
    release = get_release(conn, release_id)
    if release.get("state") == "release_locked":
        raise RuntimeError("Release is locked and immutable")
    for app_id, snapshot in release["snapshots"].items():
        if snapshot.get("qa_status") == "eligible":
            snapshot["qa_status"] = "in_qa"
            snapshot["rm_admitted"] = True
            save_snapshot(conn, release_id, app_id, snapshot)
    conn.execute("UPDATE releases SET state = ? WHERE id = ?", ("qa_open", release_id))
    conn.commit()
    audit(conn, f"打开 QA 周期：{release['name']}")


def lock_release(conn: sqlite3.Connection, release_id: str) -> dict[str, str]:
    release = get_release(conn, release_id)
    if release.get("state") == "release_locked":
        raise RuntimeError("Release is already locked")
    blockers = admission_blockers(conn, release_id)
    blocking = {app_id: items for app_id, items in blockers.items() if items and release["snapshots"][app_id].get("release_decision") == "release"}
    if blocking:
        raise RuntimeError(f"Cannot lock release; blockers remain: {blocking}")
    not_admitted = [
        app_id
        for app_id, snapshot in release["snapshots"].items()
        if snapshot.get("release_decision") == "release" and not snapshot.get("rm_admitted")
    ]
    if not_admitted:
        raise RuntimeError(f"Cannot lock release; apps not admitted to QA: {not_admitted}")
    not_passed = [
        app_id
        for app_id, snapshot in release["snapshots"].items()
        if snapshot.get("release_decision") == "release" and snapshot.get("qa_status") not in {"qa_passed", "passed"}
    ]
    if not_passed:
        raise RuntimeError(f"Cannot lock release; apps have not passed QA: {not_passed}")
    apps_by_id = {app["id"]: app for app in list_apps(conn)}
    for app_id, snapshot in release["snapshots"].items():
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
                "doc_target": app["doc_target"],
                "owners": app["owners"],
            }
        snapshot["locked"] = True
        save_snapshot(conn, release_id, app_id, snapshot, allow_locked=True)
    conn.execute("UPDATE releases SET state = ?, locked_at = ? WHERE id = ?", ("release_locked", now(), release_id))
    conn.commit()
    artifacts = generate_artifacts(conn, release_id, final=True, from_lock=True)
    audit(conn, f"Release locked：{release['name']}")
    return artifacts


def rst_title(title: str, marker: str = "=") -> str:
    return f"{title}\n{marker * len(title)}\n\n"


def code_block(content: str) -> str:
    if not content:
        return "\n"
    body = "\n".join(f"   {line}" for line in content.splitlines())
    return f".. code-block:: shell\n\n{body}\n\n"


def release_rows(conn: sqlite3.Connection, release: dict[str, Any], *, admitted_only: bool = False) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    apps = {app["id"]: app for app in list_apps(conn)}
    rows = []
    for app_id, snapshot in release["snapshots"].items():
        app = snapshot.get("app_meta") or apps.get(app_id)
        if not app or snapshot.get("release_decision") != "release":
            continue
        if admitted_only and not snapshot.get("rm_admitted"):
            continue
        if admitted_only and snapshot.get("qa_status") not in {"qa_passed", "passed"}:
            continue
        rows.append((app, snapshot))
    return sorted(rows, key=lambda item: item[0]["name"].lower())


def render_release_note(release: dict[str, Any], rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    out = rst_title(f"MACA HPC 发布列表 - {release['name']}")
    out += ".. list-table::\n   :header-rows: 1\n   :widths: 15 15 30 12 14 14\n\n"
    out += "   * - 名称\n     - 类型\n     - 描述\n     - 对应官方版本\n     - X86支持芯片系列\n     - ARM支持芯片类型\n"
    for app, snapshot in rows:
        out += f"   * - {app['name']}\n     - {app.get('type') or app.get('category') or ''}\n     - {app.get('description') or ''}\n     - {snapshot.get('version') or ''}\n     - {snapshot.get('x86_chips') or ''}\n     - {snapshot.get('arm_chips') or ''}\n"
    return out


def render_guide(title: str, rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    out = rst_title(title)
    for app, snapshot in rows:
        doc = snapshot.get("doc", {})
        out += rst_title(app["name"], "-")
        out += f"{doc.get('intro') or app.get('description') or ''}\n\n"
        out += f"版本：{snapshot.get('version') or ''}\n\n"
        out += "**镜像使用方法：**\n\n" + code_block(doc.get("image_usage", ""))
        out += "**二进制包使用方法：**\n\n" + code_block(doc.get("binary_usage", ""))
        out += "**环境搭建：**\n\n" + code_block(doc.get("env_setup", ""))
        out += f"**测试方法：**\n\n{doc.get('test_method') or ''}\n\n"
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
        out += f"**测试结果查看：**\n\n{doc.get('test_result') or ''}\n\n"
        if doc.get("limitations"):
            out += f"**已知限制：**\n\n{doc['limitations']}\n\n"
    return out


def generate_artifacts(conn: sqlite3.Connection, release_id: str, *, final: bool = False, from_lock: bool = False) -> dict[str, str]:
    release = get_release(conn, release_id)
    if final and not from_lock:
        raise RuntimeError("Final artifacts can only be generated by release lock")
    if from_lock and not final:
        raise RuntimeError("Lock generation must create final artifacts")
    if from_lock:
        if release.get("state") != "release_locked":
            raise RuntimeError("Final artifacts require a locked release")
        unlocked = [
            app_id
            for app_id, snapshot in release["snapshots"].items()
            if snapshot.get("release_decision") == "release" and not snapshot.get("locked")
        ]
        if unlocked:
            raise RuntimeError(f"Final artifacts require locked snapshots: {unlocked}")
    if release.get("state") == "release_locked" and not from_lock:
        raise RuntimeError("Release is locked; artifacts are immutable")
    if final:
        existing = conn.execute("SELECT 1 FROM artifacts WHERE release_id = ? AND final = 1 LIMIT 1", (release_id,)).fetchone()
        if existing:
            raise RuntimeError("Final artifacts already exist and are immutable")
    rows = release_rows(conn, release, admitted_only=final)
    artifacts = {
        "release_note": render_release_note(release, rows),
        "manual": render_guide("HPC Manual App 章节", [(a, s) for a, s in rows if a.get("doc_target") in {"manual", "both"}]),
        "ai4sci": render_guide("AI4Sci User Guide App 章节", [(a, s) for a, s in rows if a.get("doc_target") in {"ai4sci", "both"}]),
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
    audit(conn, "生成最终 artifacts" if final else "生成预览 artifacts")
    return artifacts


def gerrit_push_plan(conn: sqlite3.Connection, release_id: str) -> dict[str, Any]:
    """Return Gerrit push readiness and commands.

    Real push is intentionally gated by explicit configuration so the system
    cannot silently claim docs were pushed without credentials/remotes.
    """
    release = get_release(conn, release_id)
    if release.get("state") != "release_locked":
        raise RuntimeError("Gerrit push requires release_locked")
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
