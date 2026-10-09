#!/usr/bin/env python3
"""测试更新 Jira 字段接口。"""

import argparse
import json

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试更新 Jira 字段接口")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    parser.add_argument("--description", default="这是通过测试脚本更新后的描述", help="新的描述内容")
    args = parser.parse_args()

    fields = {
        "description": args.description,
        "Expected ETA": "2026-03-30",
    }
    return run_script(
        "update_issue.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--issue-id",
            args.issue_id,
            "--fields-json",
            json.dumps(fields, ensure_ascii=False),
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
