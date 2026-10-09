#!/usr/bin/env python3
"""测试创建 Jira 问题接口。"""

import argparse
import json

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试创建 Jira 问题接口")
    add_common_args(parser)
    parser.add_argument("--project-key", default="MC3", help="项目键")
    parser.add_argument("--summary", default="测试创建问题标题", help="问题标题")
    parser.add_argument("--description", default="这是测试创建的问题描述", help="问题描述")
    parser.add_argument("--issue-type", default="Feature", help="问题类型")
    args = parser.parse_args()

    fields = {
        "project": {"key": args.project_key},
        "summary": args.summary,
        "description": args.description,
        "issuetype": {"name": args.issue_type},
        "components": [{"name": "PDE_AI"}],
        "assignee": {"name": "gyyan"},
        "versions": [{"name": "SW_SDK_3.6.0.16"}],  # Affects Version/s
        "Expected ETA": "2026-03-30",
    }

    return run_script(
        "create_issue.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--fields-json",
            json.dumps(fields, ensure_ascii=False),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
