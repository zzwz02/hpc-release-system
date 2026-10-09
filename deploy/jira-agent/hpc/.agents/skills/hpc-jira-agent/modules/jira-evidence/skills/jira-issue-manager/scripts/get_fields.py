#!/usr/bin/env python3
"""获取 Jira 字段列表。"""

import argparse
from common import read_token

from common import DEFAULT_BASE_URL, build_field_maps, emit_json, fetch_json


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 Jira 字段列表")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    parser.add_argument("--raw", action="store_true", help="返回 Jira 原始字段列表")
    args = parser.parse_args()
    args.token = read_token(args.token)

    try:
        field_list = fetch_json(
            "GET",
            args.base_url,
            "/rest/api/2/field",
            args.token,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    if args.raw:
        return emit_json({"success": True, "data": field_list})

    field_name_map, id_to_field = build_field_maps(field_list)
    summary_fields = []
    custom_field_count = 0
    for field_id, field_name in field_name_map.items():
        field_def = id_to_field.get(field_id, {})
        is_custom = field_id.startswith("customfield_")
        if is_custom:
            custom_field_count += 1
        summary_fields.append(
            {
                "id": field_id,
                "name": field_name,
                "custom": is_custom,
                "schema_type": ((field_def.get("schema") or {}).get("type")),
            }
        )

    result = {
        "total": len(summary_fields),
        "custom_field_count": custom_field_count,
        "fields": summary_fields,
    }
    return emit_json({"success": True, "data": result})


if __name__ == "__main__":
    raise SystemExit(main())
