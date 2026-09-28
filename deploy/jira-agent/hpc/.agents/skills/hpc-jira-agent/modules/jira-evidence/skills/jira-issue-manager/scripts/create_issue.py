#!/usr/bin/env python3
"""创建 Jira 问题。"""

from __future__ import annotations

import argparse
from common import read_token
from typing import Any, Optional
from urllib.parse import urlencode

from common import (
    DEFAULT_BASE_URL,
    build_name_to_id_map,
    emit_json,
    fetch_json,
    load_json_arg,
    map_field_keys_to_ids,
)


def find_issue_type_meta(
    meta_payload: dict[str, Any],
    project_value: dict[str, Any],
    issue_type_value: dict[str, Any],
) -> Optional[dict[str, Any]]:
    project_key = project_value.get("key")
    project_id = project_value.get("id")
    issue_type_name = issue_type_value.get("name")
    issue_type_id = issue_type_value.get("id")

    for project in meta_payload.get("projects") or []:
        if project_key and project.get("key") != project_key:
            continue
        if not project_key and project_id and project.get("id") != str(project_id):
            continue

        for issue_type in project.get("issuetypes") or []:
            if issue_type_name and issue_type.get("name") == issue_type_name:
                return issue_type
            if not issue_type_name and issue_type_id and issue_type.get("id") == str(issue_type_id):
                return issue_type
    return None


def build_create_meta_path(project_value: dict[str, Any], issue_type_value: dict[str, Any]) -> str:
    path = "/rest/api/2/issue/createmeta"
    query_params = {"expand": "projects.issuetypes.fields"}
    if project_value.get("key"):
        query_params["projectKeys"] = project_value["key"]
    elif project_value.get("id"):
        query_params["projectIds"] = str(project_value["id"])
    if issue_type_value.get("name"):
        query_params["issuetypeNames"] = issue_type_value["name"]
    elif issue_type_value.get("id"):
        query_params["issuetypeIds"] = str(issue_type_value["id"])
    return f"{path}?{urlencode(query_params)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 Jira 问题")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument(
        "--fields-json",
        required=True,
        help='问题字段 JSON，例如 {"project":{"key":"CCPM"},"summary":"标题","issuetype":{"name":"Task"}}',
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
        project_value = payload["fields"].get("project")
        issue_type_value = payload["fields"].get("issuetype")
        if not isinstance(project_value, dict) or not isinstance(issue_type_value, dict):
            return emit_json({"success": False, "error": "创建 issue 前必须提供 project 和 issuetype 字段"})

        create_meta = fetch_json(
            "GET",
            args.base_url,
            build_create_meta_path(project_value, issue_type_value),
            args.token,
            timeout=args.timeout,
        )
        issue_type_meta = find_issue_type_meta(create_meta, project_value, issue_type_value)
        if not issue_type_meta:
            return emit_json(
                {
                    "success": False,
                    "error": "未找到当前 project 和 issuetype 对应的创建元数据，无法校验必填字段",
                }
            )

        missing_required_fields = []
        for field_id, field_def in (issue_type_meta.get("fields") or {}).items():
            if not field_def.get("required"):
                continue
            if field_def.get("hasDefaultValue"):
                continue
            if field_id not in payload["fields"]:
                missing_required_fields.append(field_def.get("name") or field_id)

        if missing_required_fields:
            return emit_json(
                {
                    "success": False,
                    "error": "缺少必填字段: " + ", ".join(missing_required_fields),
                }
            )

        response_data = fetch_json(
            "POST",
            args.base_url,
            "/rest/api/2/issue",
            args.token,
            payload=payload,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        return emit_json({"success": False, "error": str(exc)})

    data = {
        "id": response_data.get("id"),
        "key": response_data.get("key"),
        "self": response_data.get("self"),
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
