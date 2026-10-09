#!/usr/bin/env python3
"""获取 Jira 问题评论列表。"""

import argparse
from common import read_token

from common import DEFAULT_BASE_URL, emit_json, fetch_all_comments, normalize_comments


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 Jira 问题评论列表")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    args = parser.parse_args()
    args.token = read_token(args.token)

    try:
        payload = fetch_all_comments(args.base_url, args.issue_id, args.token, args.timeout)
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    data = {
        "total": payload.get("total", len(payload.get("comments", []))),
        "max_results": payload.get("maxResults", len(payload.get("comments", []))),
        "start_at": payload.get("startAt", 0),
        "comments": normalize_comments(payload),
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
