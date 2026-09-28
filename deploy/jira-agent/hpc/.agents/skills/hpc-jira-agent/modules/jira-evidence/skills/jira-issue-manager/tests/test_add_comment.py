#!/usr/bin/env python3
"""测试添加 Jira 评论接口。"""

import argparse

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试添加 Jira 评论接口")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    parser.add_argument("--comment", default="这是通过测试脚本添加的评论", help="评论内容")
    args = parser.parse_args()
    return run_script(
        "add_comment.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--issue-id",
            args.issue_id,
            "--comment",
            args.comment,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
