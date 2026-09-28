#!/usr/bin/env python3
"""测试获取 Jira 创建元数据接口。"""

import argparse

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试获取 Jira 创建元数据接口")
    add_common_args(parser)
    parser.add_argument("--project-key", required=True, help="项目键")
    parser.add_argument("--issue-type", required=True, help="问题类型名称")
    args = parser.parse_args()

    return run_script(
        "get_create_meta.py",
        [
            "--base-url", args.base_url,
            "--token", args.access_token,
            "--project-key", args.project_key,
            "--issue-type",  args.issue_type,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())