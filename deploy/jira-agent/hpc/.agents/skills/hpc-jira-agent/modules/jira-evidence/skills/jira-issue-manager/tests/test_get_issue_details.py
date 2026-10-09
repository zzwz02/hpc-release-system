#!/usr/bin/env python3
"""测试获取 Jira 问题详情接口。"""

import argparse

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试获取 Jira 问题详情接口")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    args = parser.parse_args()
    return run_script(
        "get_issue_details.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--issue-id",
            args.issue_id,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
