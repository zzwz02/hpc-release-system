#!/usr/bin/env python3
"""获取 Jira 创建问题元数据。"""

from __future__ import annotations

import argparse
from common import read_token
from typing import Any

from common import DEFAULT_BASE_URL, emit_json, fetch_json


def summarize_allowed_values(values: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    samples = []
    for item in values[:limit]:
        samples.append(
            {
                "id": item.get("id"),
                "name": item.get("name") or item.get("value"),
                "value": item.get("value"),
            }
        )
    return {
        "count": len(values),
        "sample": samples,
        "truncated": len(values) > limit,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="获取 Jira 创建问题元数据")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--project-key", required=True, help="项目键，例如 MC3")
    parser.add_argument("--issue-type", required=True, help="问题类型名称，例如 Feature")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数")
    parser.add_argument("--raw", action="store_true", help="返回 Jira 原始创建元数据")
    parser.add_argument(
        "--allowed-values-limit",
        type=int,
        default=10,
        help="默认输出时，每个字段保留的 allowedValues 示例数量",
    )
    args = parser.parse_args()
    args.token = read_token(args.token)

    path = (
        "/rest/api/2/issue/createmeta"
        f"?projectKeys={args.project_key}"
        f"&issuetypeNames={args.issue_type}"
        "&expand=projects.issuetypes.fields"
    )

    try:
        meta_payload = fetch_json(
            "GET",
            args.base_url,
            path,
            args.token,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    if args.raw:
        return emit_json({"success": True, "data": meta_payload})

    projects = meta_payload.get("projects") or []
    issue_type_meta = None
    for project in projects:
        if project.get("key") != args.project_key:
            continue
        for issue_type in project.get("issuetypes") or []:
            if issue_type.get("name") == args.issue_type:
                issue_type_meta = issue_type
                break
        if issue_type_meta:
            break

    if not issue_type_meta:
        return emit_json(
            {
                "success": False,
                "error": f"未找到项目 {args.project_key} 下问题类型 {args.issue_type} 的创建元数据",
            }
        )

    fields_meta = issue_type_meta.get("fields") or {}
    fields = []
    required_fields = []
    for field_id, field_def in fields_meta.items():
        allowed_values = field_def.get("allowedValues") or []
        field_data = {
            "id": field_id,
            "name": field_def.get("name") or field_id,
            "required": bool(field_def.get("required")),
            "schema_type": ((field_def.get("schema") or {}).get("type")),
            "has_default_value": bool(field_def.get("hasDefaultValue")),
            "allowed_values": summarize_allowed_values(allowed_values, args.allowed_values_limit),
        }
        fields.append(field_data)
        if field_data["required"]:
            required_fields.append(field_data["name"])

    fields.sort(key=lambda item: (not item["required"], item["name"].lower()))

    data = {
        "project_key": args.project_key,
        "issue_type": args.issue_type,
        "field_count": len(fields),
        "required_fields": required_fields,
        "fields": fields,
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
