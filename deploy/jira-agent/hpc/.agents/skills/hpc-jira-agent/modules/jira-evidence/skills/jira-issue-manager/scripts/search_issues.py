#!/usr/bin/env python3
"""按 JQL 检索 Jira 问题。"""

import argparse
from common import read_token

from common import DEFAULT_BASE_URL, emit_json, fetch_json, normalize_user


def simplify_issue(issue: dict) -> dict:
    fields = issue.get("fields", {})
    return {
        "key": issue.get("key"),
        "summary": fields.get("summary"),
        "status": ((fields.get("status") or {}).get("name")),
        "assignee": normalize_user(fields.get("assignee")),
        "priority": ((fields.get("priority") or {}).get("name")),
        "created": fields.get("created"),
        "description": fields.get("description", ""),
        #"updated": fields.get("updated"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="按 JQL 检索 Jira 问题")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--jql", required=True, help="JQL 查询语句")
    parser.add_argument("--start-at", type=int, default=0, help="分页起始位置")
    parser.add_argument("--max-results", type=int, default=20, help="返回数量上限")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    parser.add_argument("--raw", action="store_true", help="返回 Jira 原始响应")
    args = parser.parse_args()
    args.token = read_token(args.token)

    from urllib.parse import quote

    encoded_jql = quote(args.jql, safe="")
    path = f"/rest/api/2/search?jql={encoded_jql}&startAt={args.start_at}&maxResults={args.max_results}"
    try:
        payload = fetch_json(
            "GET",
            args.base_url,
            path,
            args.token,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    if args.raw:
        return emit_json({"success": True, "data": payload})

    issues = [simplify_issue(issue) for issue in payload.get("issues", [])]
    result = {
        "total": payload.get("total", len(issues)),
        "start_at": payload.get("startAt", args.start_at),
        "max_results": payload.get("maxResults", args.max_results),
        "issues": issues,
    }
    return emit_json({"success": True, "data": result})


if __name__ == "__main__":
    raise SystemExit(main())
