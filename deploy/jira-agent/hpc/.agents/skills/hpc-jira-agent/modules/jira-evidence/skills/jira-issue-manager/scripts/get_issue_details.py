#!/usr/bin/env python3
"""获取 Jira 问题详情，默认包含评论、自定义字段与附件列表。"""

import argparse
import re
from datetime import datetime, timezone
from requests.exceptions import RequestException
from common import read_token

from common import (
    DEFAULT_BASE_URL,
    build_field_maps,
    compact_text,
    emit_json,
    fetch_json,
    fetch_all_comments,
    normalize_attachments,
    normalize_comments,
    normalize_custom_fields,
    normalize_user,
    read_failure,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 Jira 问题详情")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", args.issue_id) or args.timeout <= 0:
        return emit_json({"success": False, "error_code": "invalid_issue_or_timeout"})
    started = datetime.now(timezone.utc).isoformat()
    try:
        args.token = read_token(args.token)
        issue_payload = fetch_json(
            "GET",
            args.base_url,
            f"/rest/api/2/issue/{args.issue_id}",
            args.token,
            timeout=args.timeout,
        )
        if not isinstance(issue_payload, dict) or issue_payload.get("key") != args.issue_id or not isinstance(issue_payload.get("fields"), dict):
            raise ValueError("issue_identity_or_fields_invalid")
    except (OSError, RuntimeError, ValueError, RequestException) as exc:
        return emit_json({"success": False, "error_code": read_failure(exc), "error": "Issue could not be acquired; no complete snapshot produced."})
    fields = issue_payload["fields"]
    parts = {"issue": {"status": "complete"},
             "attachment_metadata": {"status": "complete" if isinstance(fields.get("attachment"), list) else "not_provided"}}
    try:
        field_list = fetch_json("GET", args.base_url, "/rest/api/2/field", args.token, timeout=args.timeout)
        if not isinstance(field_list, list) or any(not isinstance(item, dict) for item in field_list):
            raise ValueError("invalid_field_definitions")
        parts["field_definitions"] = {"status": "complete"}
    except (RuntimeError, ValueError, RequestException) as exc:
        field_list = []
        parts["field_definitions"] = {"status": "unavailable", "error_code": read_failure(exc)}
    field_name_map, id_to_field = build_field_maps(field_list)
    comment_payload = fetch_all_comments(args.base_url, args.issue_id, args.token, args.timeout, allow_partial=True)
    parts["comments"] = comment_payload["coverage"]
    embedded = fields.get("comment")
    if not comment_payload["comments"] and parts["comments"]["status"] != "complete" and isinstance(embedded, dict):
        items = embedded.get("comments")
        if isinstance(items, list) and all(isinstance(item, dict) and type(item.get("id")) in (str, int) for item in items):
            ids = [str(item["id"]) for item in items]
            if all(ids) and len(set(ids)) == len(ids):
                comment_payload["comments"] = items
                parts["comments"].update({"fallback": "issue_embedded_page", "collected": len(items),
                                         "status": "partial" if items else "unavailable"})
    status = "complete" if all(item["status"] == "complete" for item in parts.values()) else "partial"
    data = {
        "key": issue_payload.get("key"),
        "acquisition": {"status": status, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                        "scope": "currently_visible_api_data_not_an_atomic_snapshot", "parts": parts},
        "raw_fields": fields,
        "field_definitions": field_list,
        "summary": fields.get("summary"),
        "description": compact_text(fields.get("description")),
        "status": ((fields.get("status") or {}).get("name")),
        "assignee": normalize_user(fields.get("assignee")),
        "reporter": normalize_user(fields.get("reporter")),
        "priority": ((fields.get("priority") or {}).get("name")),
        "labels": fields.get("labels") or [],
        "created": fields.get("created"),
        "updated": fields.get("updated"),
        "custom_fields": normalize_custom_fields(fields, field_name_map, id_to_field),
        "comments": normalize_comments(comment_payload),
        "attachments": normalize_attachments(fields["attachment"]) if isinstance(fields.get("attachment"), list) else [],
    }
    return emit_json({"success": True, "completeness": status, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
