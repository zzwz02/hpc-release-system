#!/usr/bin/env python3
"""
ldap_group_test.py - query LDAP/AD groups for one domain account.

Usage:
  python3 ldap_group_test.py
  python3 ldap_group_test.py m123456
  python3 ldap_group_test.py m123456 --role-only
  python3 ldap_group_test.py m123456 --with-dn
  python3 ldap_group_test.py m123456 --names-only
  python3 ldap_group_test.py m123456 --json
  python3 ldap_group_test.py m123456 --config ldap.conf

The script reads the production LDAP config from ldap.conf by default, binds
with the read-only service account, searches the user, and prints groups from
the memberOf attribute. It never prints bindpw.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == "tests" else SCRIPT_DIR
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from release_system import core

try:
    from ldap3 import ALL, SIMPLE, SUBTREE, Connection, Server
    from ldap3.core.exceptions import LDAPException
    from ldap3.utils.conv import escape_filter_chars
    from ldap3.utils.dn import parse_dn
except ImportError:
    sys.exit(
        "[ERROR] 缺少依赖 ldap3\n"
        "请先安装：pip install ldap3"
    )


DEFAULT_CONFIG = {
    "enabled": False,
    "uri": "",
    "base": "",
    "binddn": "",
    "bindpw": "",
    "user_filter": "(&(objectClass=user)(sAMAccountName={uid}))",
    "uid_attr": "sAMAccountName",
    "name_attr": "displayName",
    "timeout": "10",
}


def load_config(path: str) -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.is_absolute():
        cfg_path = PROJECT_ROOT / cfg_path
    if not cfg_path.exists():
        sys.exit(f"[ERROR] 配置文件不存在：{cfg_path}")

    cfg = dict(DEFAULT_CONFIG)
    for lineno, raw in enumerate(cfg_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            print(f"[WARN] 跳过第 {lineno} 行：缺少 '='", file=sys.stderr)
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        if key == "enabled":
            cfg["enabled"] = value.strip().lower() in ("true", "1", "yes")
        else:
            cfg[key] = value.strip()

    try:
        cfg["timeout"] = int(cfg.get("timeout") or 10)
    except (TypeError, ValueError):
        cfg["timeout"] = 10

    missing = [key for key in ("uri", "base", "binddn", "bindpw") if not cfg.get(key)]
    if missing:
        sys.exit(f"[ERROR] ldap.conf 缺少必填项：{', '.join(missing)}")
    return cfg


def entry_values(entry: Any, attr: str) -> list[str]:
    try:
        value = entry[attr].value
    except Exception:
        return []
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return [str(value)]


def entry_value(entry: Any, attr: str) -> str:
    values = entry_values(entry, attr)
    return values[0] if values else ""


def dn_attr(dn: str, attr_name: str) -> str:
    try:
        for attr, value, _sep in parse_dn(dn):
            if attr.lower() == attr_name.lower():
                return str(value)
    except Exception:
        pass
    return dn


def query_groups(cfg: dict[str, Any], username: str) -> dict[str, Any]:
    safe_uid = escape_filter_chars(username)
    search_filter = str(cfg["user_filter"]).replace("{uid}", safe_uid)
    attrs = sorted({cfg["uid_attr"], cfg["name_attr"], "memberOf", "distinguishedName"})

    server = Server(cfg["uri"], get_info=ALL, connect_timeout=cfg["timeout"])
    try:
        conn = Connection(
            server,
            user=cfg["binddn"],
            password=cfg["bindpw"],
            authentication=SIMPLE,
            auto_bind=True,
            receive_timeout=cfg["timeout"],
        )
    except LDAPException as exc:
        sys.exit(f"[ERROR] LDAP 服务账号连接失败：{exc}")

    try:
        ok = conn.search(
            cfg["base"],
            search_filter,
            search_scope=SUBTREE,
            attributes=attrs,
        )
        if not ok or not conn.entries:
            sys.exit(f"[ERROR] 未找到域账号：{username}")

        entry = conn.entries[0]
        group_dns = entry_values(entry, "memberOf")
        groups = [dn_attr(dn, "CN") for dn in group_dns]
        return {
            "username": username,
            "display_name": entry_value(entry, cfg["name_attr"]),
            "entry_dn": entry.entry_dn or entry_value(entry, "distinguishedName"),
            "predicted_role": core.ldap_role_from_groups(group_dns),
            "groups": groups,
            "group_dns": group_dns,
        }
    finally:
        conn.unbind()


def print_groups(result: dict[str, Any], with_dn: bool) -> None:
    display = result.get("display_name") or ""
    groups = result.get("groups") or []
    group_dns = result.get("group_dns") or []

    print(f"账号: {result['username']}")
    if display:
        print(f"姓名: {display}")
    print(f"首次登录将分配角色: {result.get('predicted_role') or 'Guest'}")
    print(f"组数量: {len(groups)}")
    print("组:")

    if not groups:
        print("  (empty)")
        return

    for idx, name in enumerate(groups, 1):
        print(f"  {idx}. {name}")
        if with_dn:
            print(f"     DN: {group_dns[idx - 1]}")


def print_names_only(result: dict[str, Any]) -> None:
    for name in result.get("groups") or []:
        print(name)


def print_role_only(result: dict[str, Any]) -> None:
    print(result.get("predicted_role") or "Guest")


def main() -> None:
    parser = argparse.ArgumentParser(description="输入域账号，输出 LDAP/AD memberOf 组。")
    parser.add_argument("username", nargs="?", help="域账号，例如 m123456；不传则交互输入")
    parser.add_argument(
        "--config",
        default="ldap.conf",
        help="LDAP 配置文件路径；相对路径按脚本所在目录解析，默认 ldap.conf",
    )
    parser.add_argument("--with-dn", action="store_true", help="同时输出每个组的完整 DN")
    parser.add_argument("--names-only", action="store_true", help="只输出组名，一行一个")
    parser.add_argument("--role-only", action="store_true", help="只输出首次登录将分配的角色")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    username = (args.username or input("域账号: ")).strip()
    if not username:
        sys.exit("[ERROR] 域账号不能为空")

    cfg = load_config(args.config)
    if not cfg.get("enabled"):
        print("[WARN] ldap.conf 中 enabled=false，仍继续执行查询。", file=sys.stderr)

    result = query_groups(cfg, username)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.role_only:
        print_role_only(result)
    elif args.names_only:
        print_names_only(result)
    else:
        print_groups(result, args.with_dn)


if __name__ == "__main__":
    main()
