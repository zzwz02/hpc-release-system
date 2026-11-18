"""Account API tokens: no changes to browser login, MCP or external agents."""
import hashlib
import secrets

from app.api.errors import ApiError, AuthzError
from app.db.connection import transaction
from app.domain.permissions import roles_for_capability
from app.repositories import api_tokens_repo as repo, audit_repo, users_repo
from app.timeutil import beijing_timestamp

TOKEN_PREFIX = "rsapi_"
MAX_ACTIVE_TOKENS = 5
CI_ROLES = frozenset(roles_for_capability("ci.automation"))
CI_OPERATIONS = frozenset({"owner_lookup", "release_check", "failure_report", "test_results", "release_gate_failure"})


def create(conn, user: dict, name: str) -> dict:
    name = name.strip()
    if not name or len(name) > 60:
        raise ValueError("Token 名称需为 1 至 60 个字符")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = beijing_timestamp()
    with transaction(conn, immediate=True):
        current = users_repo.get_user(conn, user["username"])
        if not current:
            raise PermissionError("账号已不存在，请重新登录")
        if repo.active_count(conn, user["username"]) >= MAX_ACTIVE_TOKENS:
            raise ApiError(409, "最多保留 5 个有效 Token，请先撤销不再使用的 Token")
        token_id = repo.insert(conn, user["username"], name, hashlib.sha256(token.encode()).hexdigest(),
                               TOKEN_PREFIX + "..." + token[-6:], now)
        audit_repo.log_audit(conn, "创建平台 API Token", ts=now, user=user["username"], role=current["role"],
                             event="api_token.create", detail={"token_id": token_id})
    return {**repo.get_token(conn, token_id, user["username"]), "token": token}


def revoke(conn, user: dict, token_id: int) -> dict:
    with transaction(conn, immediate=True):
        token = repo.get_token(conn, token_id, user["username"])
        if not token:
            raise ApiError(404, "Token 不存在")
        if not token["revoked_at"]:
            now = beijing_timestamp()
            repo.revoke(conn, token_id, user["username"], now)
            audit_repo.log_audit(conn, "撤销平台 API Token", ts=now, user=user["username"], role=user["role"],
                                 event="api_token.revoke", detail={"token_id": token_id})
    return repo.get_token(conn, token_id, user["username"])


def delete(conn, user: dict, token_id: int) -> None:
    with transaction(conn, immediate=True):
        token = repo.get_token(conn, token_id, user["username"])
        if not token:
            raise ApiError(404, "Token 不存在")
        if not token["revoked_at"]:
            raise ApiError(409, "请先撤销 Token，再删除")
        repo.delete_revoked(conn, token_id, user["username"])
        audit_repo.log_audit(conn, "删除已撤销的平台 API Token", ts=beijing_timestamp(),
                             user=user["username"], role=user["role"], event="api_token.delete",
                             detail={"token_id": token_id, "name": token["name"], "token_hint": token["token_hint"],
                                     "created_at": token["created_at"], "revoked_at": token["revoked_at"],
                                     "last_used_at": token["last_used_at"], "last_operation": token["last_operation"]})


def authenticate(conn, authorization: str, operation: str) -> dict:
    if operation not in CI_OPERATIONS:
        raise AuthzError("平台 Token 不支持此操作")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith(TOKEN_PREFIX) or len(token) > 128:
        raise PermissionError("需要平台 API Token")
    # Re-read the role and revocation state on every request; no permission cache.
    with transaction(conn, immediate=True):
        identity = repo.authenticate(conn, hashlib.sha256(token.encode()).hexdigest())
        if not identity:
            raise PermissionError("平台 API Token 无效或已撤销")
        if identity["role"] not in CI_ROLES:
            raise AuthzError("当前账号角色不能调用 CI 自动化接口")
        repo.touch(conn, identity["token_id"], beijing_timestamp(), operation)
    return identity
