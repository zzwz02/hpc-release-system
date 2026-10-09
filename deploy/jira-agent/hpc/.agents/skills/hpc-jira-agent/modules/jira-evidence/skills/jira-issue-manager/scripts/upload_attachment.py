#!/usr/bin/env python3
"""上传 Jira 附件的简单工具脚本。"""

import argparse
from common import read_token
import os
import hashlib
from typing import Tuple

import requests

from common import DEFAULT_BASE_URL, emit_json, approve_write


def build_url(base_url: str, issue_id: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/rest/api/2/issue/{issue_id}/attachments"


def read_file(path: str) -> Tuple[str, bytes]:
    filename = os.path.basename(path)
    with open(path, "rb") as f:
        data = f.read()
    return filename, data


def main() -> int:
    parser = argparse.ArgumentParser(description="上传 Jira 附件")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Jira 基础地址，如 http://host",
    )
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--file", required=True, help="附件文件路径")
    parser.add_argument("--timeout", type=int, default=30, help="请求超时秒数，默认 30")
    args = parser.parse_args()
    args.token = read_token(args.token)

    if not os.path.isfile(args.file):
        return emit_json({"success": False, "error": f"文件不存在: {args.file}"})

    url = build_url(args.base_url, args.issue_id)
    filename, data = read_file(args.file)
    approve_write("POST", url, {"filename": filename, "sha256": hashlib.sha256(data).hexdigest()})

    headers = {
        "Authorization": f"Bearer {args.token}",
        "X-Atlassian-Token": "no-check",
    }
    files = {
        "file": (filename, data, "application/octet-stream"),
    }

    try:
        resp = requests.post(url, headers=headers, files=files, timeout=args.timeout, allow_redirects=False)
    except requests.RequestException as e:
        return emit_json({"success": False, "error": f"请求失败: {e}"})

    if 200 <= resp.status_code < 300:
        data = {
            "issue_id": args.issue_id,
            "filename": filename,
            "status_code": resp.status_code,
        }
        return emit_json({"success": True, "data": data})

    error = f"上传失败: HTTP {resp.status_code}"
    # Error bodies can contain credentials or private issue content; do not echo.
    return emit_json({"success": False, "error": error})


if __name__ == "__main__":
    raise SystemExit(main())
