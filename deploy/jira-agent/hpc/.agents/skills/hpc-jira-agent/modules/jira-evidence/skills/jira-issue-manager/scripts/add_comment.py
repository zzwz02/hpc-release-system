#!/usr/bin/env python3
"""给 Jira 问题添加评论。"""

import argparse
from common import read_token

from common import DEFAULT_BASE_URL, compact_text, emit_json, fetch_json, normalize_user


def main() -> int:
    parser = argparse.ArgumentParser(description="给 Jira 问题添加评论")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--comment", required=True, help="评论内容")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    args = parser.parse_args()
    args.token = read_token(args.token)

    payload = {"body": args.comment}
    try:
        response_data = fetch_json(
            "POST",
            args.base_url,
            f"/rest/api/2/issue/{args.issue_id}/comment",
            args.token,
            payload=payload,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    identifier = response_data.get("id") if isinstance(response_data, dict) else None
    if not isinstance(identifier, str) or not identifier.strip():
        return emit_json({"success": False, "error": "服务端未返回有效 comment ID；发布状态未知，先按稳定标记对账，禁止盲目重发"})
    data = {
        "id": response_data.get("id"),
        "issue_id": args.issue_id,
        "author": normalize_user(response_data.get("author")),
        "body": compact_text(response_data.get("body")),
        "created": response_data.get("created"),
        "updated": response_data.get("updated"),
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
