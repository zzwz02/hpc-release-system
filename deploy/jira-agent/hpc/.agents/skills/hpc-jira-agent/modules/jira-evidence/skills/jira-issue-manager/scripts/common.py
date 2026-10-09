#!/usr/bin/env python3
"""Jira Python 脚本的公共方法。"""
from __future__ import annotations

import json
import hashlib
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any, Optional

import requests
from requests import Response


DEFAULT_BASE_URL = os.environ.get("JIRA_BASE_URL", "http://10.2.201.98:8080")


def read_token(argument: Optional[str] = None) -> str:
    """Existing login secret, never printed or written to issue evidence."""
    if argument:
        print("--token 已弃用：建议使用 JIRA_TOKEN_FILE，避免进程参数暴露凭据", file=sys.stderr)
        return argument
    filename = os.environ.get("JIRA_TOKEN_FILE")
    token = Path(filename).read_text().strip() if filename else os.environ.get("JIRA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("请配置 JIRA_TOKEN_FILE 或 JIRA_TOKEN；不要把令牌写入 Skill")
    return token


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    allowed = urlsplit(os.environ.get("JIRA_ALLOWED_ORIGIN", DEFAULT_BASE_URL))
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise RuntimeError("Jira URL 必须是无内嵌凭据的 HTTP(S) 地址")
    if (parsed.scheme, parsed.netloc) != (allowed.scheme, allowed.netloc):
        raise RuntimeError("拒绝向未批准的 Jira origin 发送凭据；请核对 JIRA_ALLOWED_ORIGIN")


def approve_write(method: str, url: str, payload: Any) -> None:
    """Exact write approval, or operator-scoped stage comments on one issue."""
    validate_url(url)
    policy = os.environ.get("JIRA_COMMENT_POLICY_FILE")
    if method == "POST" and re.fullmatch(r"/rest/api/2/issue/[A-Z][A-Z0-9_]*-[0-9]+/comment", urlsplit(url).path) and policy:
        approve_comment(url, payload, Path(policy))
        return
    filename = os.environ.get("JIRA_WRITE_APPROVAL_FILE")
    if not filename:
        raise RuntimeError("写操作未授权：需要管理员提供精确动作/目标/载荷的 JIRA_WRITE_APPROVAL_FILE")
    approval = json.loads(Path(filename).read_text())
    path = urlsplit(url).path
    issue = re.search(r"/issue/([A-Z][A-Z0-9_]*-[0-9]+)(?:/|$)", path)
    action = "create_issue" if path.endswith("/issue") and method == "POST" else "add_comment" if path.endswith("/comment") else "upload_attachment" if path.endswith("/attachments") else "update_issue"
    target = issue[1] if issue else str((payload.get("fields") or {}).get("project", {}).get("key", ""))
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    expiry = datetime.fromisoformat(approval.get("expires_at", "1970-01-01T00:00:00+00:00"))
    if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise RuntimeError("写操作批准已过期或缺少时区")
    expected = {"method": method, "url": url, "action": action, "target": target, "payload_sha256": digest}
    if not approval.get("approved_by") or not approval.get("approval_id") or any(approval.get(key) != value for key, value in expected.items()):
        raise RuntimeError("写操作与批准的动作/目标/内容不一致")


def approve_comment(url: str, payload: Any, filename: Path) -> None:
    """An operator supplies this file; it does not authorize any other Jira write."""
    policy = json.loads(filename.read_text())
    expiry = datetime.fromisoformat(policy.get("expires_at", "1970-01-01T00:00:00+00:00"))
    issue = policy.get("issue", "")
    run = policy.get("run_id", "")
    body = payload.get("body") if isinstance(payload, dict) else None
    limit = policy.get("max_body_chars", 12000)
    if (policy.get("schema_version") != 1 or policy.get("action") != "add_comment"
            or not policy.get("approved_by") or not policy.get("approval_id")
            or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
        raise RuntimeError("阶段评论授权无效或已过期")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", issue) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run):
        raise RuntimeError("阶段评论授权缺少有效 issue/run_id")
    expected = f"{str(policy.get('origin', '')).rstrip('/')}/rest/api/2/issue/{issue}/comment"
    if url != expected or not isinstance(body, str) or set(payload) != {"body"}:
        raise RuntimeError("评论目标或载荷不在本轮授权内")
    if type(limit) is not int or not 1 <= limit <= 32000 or not 0 < len(body) <= limit:
        raise RuntimeError("评论超过本轮授权的正文长度")
    marker = rf"\[hpc-staff:{re.escape(issue)}:{re.escape(run)}:[A-Za-z0-9_-]{{1,80}}\]"
    if not re.fullmatch(marker, body.splitlines()[0]):
        raise RuntimeError("评论缺少本轮授权的稳定事件标记")


def build_api_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}{path}"


def build_headers(token: str, content_type: Optional[str] = "application/json") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def request_json(
    method: str,
    url: str,
    token: str,
    payload: Optional[Any] = None,
    timeout: int = 30,
    extra_headers: Optional[dict[str, str]] = None,
) -> Response:
    validate_url(url)
    if method.upper() not in ("GET", "HEAD", "OPTIONS"):
        approve_write(method.upper(), url, payload)
    headers = build_headers(token)
    if extra_headers:
        headers.update(extra_headers)
    return requests.request(method=method, url=url, headers=headers, json=payload, timeout=timeout, allow_redirects=False)


def load_json_arg(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"JSON 解析失败: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def emit_json(payload: Any) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if isinstance(payload, dict) and payload.get("success") is False else 0


def response_to_json(response: Response) -> Any:
    if not response.text.strip():
        return {"success": True}
    return response.json()


def print_json_response(response: Response) -> int:
    if 200 <= response.status_code < 300:
        try:
            return emit_json(response_to_json(response))
        except ValueError:
            print(response.text)
            return 0

    print(f"请求失败: HTTP {response.status_code}", file=sys.stderr)
    if response.text:
        print(response.text, file=sys.stderr)
    return 1


def fetch_json(
    method: str,
    base_url: str,
    path: str,
    token: str,
    payload: Optional[Any] = None,
    timeout: int = 30,
    extra_headers: Optional[dict[str, str]] = None,
) -> Any:
    url = build_api_url(base_url, path)
    response = request_json(
        method=method,
        url=url,
        token=token,
        payload=payload,
        timeout=timeout,
        extra_headers=extra_headers,
    )
    if not (200 <= response.status_code < 300):
        raise RuntimeError(f"请求失败: HTTP {response.status_code}；未输出可能含敏感信息的响应体")
    try:
        return response_to_json(response)
    except ValueError as exc:
        raise RuntimeError("响应不是有效的 JSON") from exc


def extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [extract_text(item) for item in value]
        return "".join(part for part in parts if part)
    if isinstance(value, dict):
        node_type = value.get("type")
        if "text" in value and isinstance(value["text"], str):
            return value["text"]
        content = value.get("content")
        if isinstance(content, list):
            chunks = [extract_text(item) for item in content]
            separator = "\n" if node_type in {"paragraph", "heading", "blockquote", "listItem"} else ""
            text = separator.join(chunk for chunk in chunks if chunk)
            if node_type in {"paragraph", "heading", "blockquote"} and text:
                return text + "\n"
            return text
    return ""


def compact_text(value: Any) -> str:
    # Historical function name retained for callers; script indentation is evidence.
    return extract_text(value)


def read_failure(exc):
    """Return a bounded diagnostic code, never response bodies or exception URLs."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.RequestException):
        return "network_error"
    match = re.search(r"HTTP ([0-9]{3})", str(exc)) if isinstance(exc, RuntimeError) else None
    if match:
        return {"401": "authentication_required", "403": "access_denied", "404": "not_found"}.get(match[1], "http_" + match[1])
    return "invalid_or_incomplete_response"


def fetch_all_comments(base_url: str, issue_id: str, token: str, timeout: int = 30, allow_partial: bool = False) -> dict[str, Any]:
    comments = []
    seen = set()
    start = 0
    total = None
    pages = []
    try:
        for _ in range(1000):
            page = fetch_json("GET", base_url, f"/rest/api/2/issue/{issue_id}/comment?startAt={start}&maxResults=100", token, timeout=timeout)
            if not isinstance(page, dict) or not isinstance(page.get("comments"), list):
                raise RuntimeError("评论分页结构无效")
            items = page["comments"]
            reported = page.get("total")
            if type(reported) is not int or reported < start + len(items) or page.get("startAt") != start:
                raise RuntimeError("评论分页位置或总数无效")
            if total is not None and reported != total:
                raise RuntimeError("评论分页总数变化，需重新获取快照")
            total = reported
            ids = [str(item["id"]) for item in items if isinstance(item, dict)
                   and type(item.get("id")) in (str, int) and str(item["id"])]
            if len(ids) != len(items) or len(set(ids)) != len(ids) or seen.intersection(ids):
                raise RuntimeError("评论分页重复或缺少 ID，需要重新获取快照")
            seen.update(ids)
            comments.extend(items)
            pages.append({"startAt": start, "count": len(items), "total": total})
            start += len(items)
            if start == total:
                return {"comments": comments, "total": total, "startAt": 0, "maxResults": len(comments),
                        "coverage": {"status": "complete", "pages": pages, "collected": len(comments), "reported_total": total}}
            if not items:
                raise RuntimeError("评论分页提前结束，不能声称采集完整")
        raise RuntimeError("评论数量超过采集预算")
    except (RuntimeError, ValueError, TypeError, requests.exceptions.RequestException) as exc:
        if not allow_partial:
            raise RuntimeError("评论读取未完成：" + read_failure(exc)) from None
        return {"comments": comments, "total": total, "startAt": 0, "maxResults": len(comments),
                "coverage": {"status": "partial" if comments else "unavailable", "pages": pages,
                             "collected": len(comments), "reported_total": total, "error_code": read_failure(exc)}}


def build_field_maps(field_list: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    id_to_name: dict[str, str] = {}
    id_to_field: dict[str, dict[str, Any]] = {}
    for field in field_list:
        field_id = field.get("id")
        if not field_id:
            continue
        id_to_name[field_id] = field.get("name") or field_id
        id_to_field[field_id] = field
    return id_to_name, id_to_field


def build_name_to_id_map(field_list: list[dict[str, Any]]) -> dict[str, str]:
    name_to_id: dict[str, str] = {}
    for field in field_list:
        field_id = field.get("id")
        field_name = field.get("name")
        if not field_id or not field_name:
            continue
        # Prefer the first mapping to avoid unstable overrides on duplicate display names.
        name_to_id.setdefault(field_name, field_id)
    return name_to_id


def map_field_keys_to_ids(fields: dict[str, Any], name_to_id: dict[str, str]) -> dict[str, Any]:
    # 创建/更新时允许直接传可读字段名，这里统一转换成 Jira 真正接受的字段 ID。
    mapped_fields: dict[str, Any] = {}
    for field_key, value in fields.items():
        mapped_key = field_key
        if (
            isinstance(field_key, str)
            and not field_key.startswith("customfield_")
            and field_key not in name_to_id.values()
            and field_key in name_to_id
        ):
            mapped_key = name_to_id[field_key]
        mapped_fields[mapped_key] = value
    return mapped_fields


def normalize_user(user_obj: Optional[dict[str, Any]]) -> Optional[str]:
    if not user_obj:
        return None
    return (
        user_obj.get("displayName")
        or user_obj.get("name")
        or user_obj.get("emailAddress")
        or user_obj.get("accountId")
    )


def normalize_comments(comment_payload: dict[str, Any]) -> list[dict[str, Any]]:
    comments = comment_payload.get("comments", [])
    normalized = []
    for comment in comments:
        normalized.append(
            {
                "id": comment.get("id"),
                "author": normalize_user(comment.get("author")),
                "body": compact_text(comment.get("body")),
                "created": comment.get("created"),
                "updated": comment.get("updated"),
                "visibility": comment.get("visibility"),
                "body_raw": comment.get("body"),
            }
        )
    return normalized


def normalize_attachments(attachment_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 详情接口只返回附件元数据，保留下载地址但不在这里触发实际下载。
    normalized = []
    for attachment in attachment_list:
        normalized.append(
            {
                "id": attachment.get("id"),
                "filename": attachment.get("filename"),
                "url": attachment.get("content"),
                "mime_type": attachment.get("mimeType"),
                "size": attachment.get("size"),
                "created": attachment.get("created"),
                "author": normalize_user(attachment.get("author")),
            }
        )
    return normalized


NOISY_CUSTOM_FIELD_NAMES = {
    # 这些字段通常是 Jira 内部排序或开发辅助信息，默认不作为业务输出返回。
    "Rank",
    "Development",
}


def is_empty_like(value: Any) -> bool:
    if value in (None, "", [], {}):
        return True
    if isinstance(value, str) and value.strip() in {"{}", "[]", "null", "None"}:
        return True
    return False


def normalize_custom_field_value(value: Any, field_def: Optional[dict[str, Any]] = None) -> Any:
    # 这里只做“可读化”，不做过度推断，避免把 Jira 原始语义改坏。
    schema = (field_def or {}).get("schema") or {}
    schema_type = schema.get("type")

    if is_empty_like(value):
        return None

    if isinstance(value, list):
        normalized_items = [normalize_custom_field_value(item, field_def) for item in value]
        normalized_items = [item for item in normalized_items if not is_empty_like(item)]
        return normalized_items or None

    if isinstance(value, dict):
        user_value = normalize_user(value)
        if schema_type == "user" and user_value:
            return user_value
        if "value" in value and isinstance(value.get("value"), str):
            return value.get("value")
        if "name" in value and isinstance(value.get("name"), str):
            return value.get("name")
        if "displayName" in value and isinstance(value.get("displayName"), str):
            return value.get("displayName")
        if "id" in value and len(value) == 1:
            return value.get("id")
        normalized_dict = {}
        for key, item in value.items():
            normalized_item = normalize_custom_field_value(item)
            if not is_empty_like(normalized_item):
                normalized_dict[key] = normalized_item
        return normalized_dict or None

    return value


def normalize_custom_fields(
    issue_fields: dict[str, Any],
    field_name_map: dict[str, str],
    id_to_field: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    # ID is identity; display names may be duplicated across fields/projects.
    custom_fields: dict[str, Any] = {}
    for field_id, value in issue_fields.items():
        if not field_id.startswith("customfield_"):
            continue
        field_name = field_name_map.get(field_id, field_id)
        field_def = (id_to_field or {}).get(field_id, {})
        normalized_value = normalize_custom_field_value(value, field_def)
        custom_fields[field_id] = {"name": field_name, "value": normalized_value, "raw": value, "schema": field_def.get("schema")}
    return custom_fields
