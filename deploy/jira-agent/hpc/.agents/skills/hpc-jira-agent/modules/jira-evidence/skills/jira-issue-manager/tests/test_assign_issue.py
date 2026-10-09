#!/usr/bin/env python3
"""测试接取任务接口。"""

import argparse
import json

from common import add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试接取任务接口")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    parser.add_argument("--assignee", default="m01216", help="负责人")
    args = parser.parse_args()

    fields = {
        "assignee": {"name": args.assignee},
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
