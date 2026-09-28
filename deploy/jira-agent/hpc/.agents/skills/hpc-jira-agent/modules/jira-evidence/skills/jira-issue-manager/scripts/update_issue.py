#!/usr/bin/env python3
"""更新 Jira 问题字段。"""

import argparse
from common import read_token

from common import (
    DEFAULT_BASE_URL,
    build_name_to_id_map,
    emit_json,
    fetch_json,
    load_json_arg,
    map_field_keys_to_ids,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 Jira 问题字段")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument(
        "--fields-json",
        required=True,
        help='字段 JSON，例如 {"description":"新描述","assignee":{"name":"m01052"}}',
    )
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    args = parser.parse_args()
    args.token = read_token(args.token)

    raw_fields = load_json_arg(args.fields_json)
    try:
        field_list = fetch_json(
            "GET",
            args.base_url,
            "/rest/api/2/field",
            args.token,
            timeout=args.timeout,
        )
        payload = {
            "fields": map_field_keys_to_ids(
                raw_fields,
                build_name_to_id_map(field_list),
            )
        }
        fetch_json(
            "PUT",
            args.base_url,
            f"/rest/api/2/issue/{args.issue_id}",
            args.token,
            payload=payload,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    data = {
        "issue_id": args.issue_id,
        "updated_fields": list(payload.get("fields", {}).keys()),
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
