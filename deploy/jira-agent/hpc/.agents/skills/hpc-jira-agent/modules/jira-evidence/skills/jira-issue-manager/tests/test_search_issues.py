#!/usr/bin/env python3
"""测试按 JQL 搜索 Jira 问题接口。"""

import argparse

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试按 JQL 搜索 Jira 问题接口")
    add_common_args(parser)
    parser.add_argument(
        "--jql",
        default='project = MC3 AND summary ~ "测试" AND status = "Open" ORDER BY created DESC',
        help="JQL 查询语句",
    )
    args = parser.parse_args()
    return run_script(
        "search_issues.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--jql",
            args.jql,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
