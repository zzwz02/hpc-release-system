#!/usr/bin/env python3
"""测试根据 Jira 附件 url 下载附件。"""

import argparse
from pathlib import Path

from common import TESTS_DIR, add_common_args, run_script


def main() -> int:
    parser = argparse.ArgumentParser(description="测试根据 Jira 附件 url 下载附件")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    parser.add_argument("--url", required=True, help="附件下载地址")
    parser.add_argument("--output-dir", default=str(TESTS_DIR / "downloads"), help="附件保存目录")
    parser.add_argument("--filename", default=None, help="保存文件名，可选")
    args = parser.parse_args()

    command = [
        "--base-url",
        args.base_url,
        "--token",
        args.access_token,
        "--issue-id",
        args.issue_id,
        "--url",
        args.url,
        "--output-dir",
        args.output_dir,
    ]
    if args.filename:
        command.extend(["--filename", args.filename])

    return run_script("download_attachment.py", command)


if __name__ == "__main__":
    raise SystemExit(main())
