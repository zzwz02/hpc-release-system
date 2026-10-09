#!/usr/bin/env python3
"""测试上传 Jira 附件接口。"""

import argparse
from pathlib import Path

from common import TESTS_DIR, add_common_args, run_script


def ensure_sample_file() -> Path:
    sample_file = TESTS_DIR / "sample_upload.txt"
    if not sample_file.exists():
        sample_file.write_text("这是一个用于测试 Jira 附件上传的样例文件。\n", encoding="utf-8")
    return sample_file


def main() -> int:
    parser = argparse.ArgumentParser(description="测试上传 Jira 附件接口")
    add_common_args(parser)
    parser.add_argument("--issue-id", required=True, help="问题编号")
    parser.add_argument("--file", default=str(ensure_sample_file()), help="附件文件路径")
    args = parser.parse_args()
    return run_script(
        "upload_attachment.py",
        [
            "--base-url",
            args.base_url,
            "--token",
            args.access_token,
            "--issue-id",
            args.issue_id,
            "--file",
            args.file,
        ],
    )


if __name__ == "__main__":
    raise SystemExit(main())
