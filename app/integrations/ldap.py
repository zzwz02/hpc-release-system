"""LDAP integration — authentication and group membership.

Wraps server.py LDAP logic (server.py:104-179) for use as an injectable
integration.  Same two-step bind flow: service-account bind to locate the
user DN, then user-password bind to verify credentials.
"""
from __future__ import annotations

try:
    from ldap3 import ALL as LDAP_ALL  # type: ignore[import-untyped]
    from ldap3 import SIMPLE as LDAP_SIMPLE
    from ldap3 import SUBTREE as LDAP_SUBTREE
    from ldap3 import Connection as LdapConn
    from ldap3 import Server as LdapServer
    from ldap3.core.exceptions import LDAPException

    _LDAP3_AVAILABLE = True
except ImportError:
    _LDAP3_AVAILABLE = False


def _ldap_entry_values(entry, attr: str) -> list[str]:
    """Extract a multi-valued LDAP attribute as a list of strings."""
    try:
        value = entry[attr].value
    except Exception:
        return []
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return [str(value)]


def authenticate(
    username: str,
    password: str,
    *,
    ldap_config: dict,
) -> tuple[str, str, list[str]]:
    """Verify username/password against LDAP.

    Returns (username, display_name, groups).
    Raises PermissionError for wrong credentials, RuntimeError for config /
    connectivity problems.

    Two-step bind flow (mirrors server.py:ldap_authenticate):
      1. Bind as service account to locate the user's full DN.
      2. Bind as that DN with the supplied password to verify.
    """
    cfg = ldap_config
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
        attributes=[cfg["uid_attr"], cfg["name_attr"], "memberOf"],
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
    groups = _ldap_entry_values(entry, "memberOf")

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
        raise PermissionError("域账号密码不正确") from None

    return username, display_name, groups


def groups_for_user(username: str, *, ldap_config: dict) -> list[str]:
    """Return group memberships for a user.

    Not used in Phase 2 core flows but provided for completeness.
    Raises RuntimeError if LDAP is not enabled or ldap3 is unavailable.
    """
    cfg = ldap_config
    if not cfg.get("enabled"):
        raise RuntimeError("LDAP 登录未启用")
    if not _LDAP3_AVAILABLE:
        raise RuntimeError("服务器未安装 ldap3 依赖（pip install ldap3）")

    safe_uid = (
        username
        .replace("\\", "\\5c")
        .replace("(",  "\\28")
        .replace(")",  "\\29")
        .replace("*",  "\\2a")
    )
    search_filter = cfg["user_filter"].replace("{uid}", safe_uid)

    server = LdapServer(cfg["uri"], get_info=LDAP_ALL, connect_timeout=cfg["timeout"])
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
        attributes=["memberOf"],
    )
    entries = svc.entries
    svc.unbind()

    if not entries:
        return []
    return _ldap_entry_values(entries[0], "memberOf")


def load_ldap_config(conf_path) -> dict:
    """Parse ldap.conf into a plain dict.

    Handles multi-word values (e.g. passwords with '=') by splitting only on
    the first '=' per line.  Returns a safe default (disabled) if the file is
    missing or unreadable.

    Ported from server.py:53-90.
    """
    from pathlib import Path

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
    path = Path(conf_path)
    if not path.exists():
        return defaults
    cfg = dict(defaults)
    for line in path.read_text(encoding="utf-8").splitlines():
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
