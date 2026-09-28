#!/usr/bin/env python3
"""根据 Jira 附件 url 下载附件。"""

import argparse
import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from common import read_token
from pathlib import Path
from urllib.parse import urlparse, unquote, urljoin, parse_qs

import requests

from common import DEFAULT_BASE_URL, emit_json, validate_url, fetch_json


def infer_filename(url: str) -> str:
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    return name or "attachment.bin"


def failure(code, message, record):
    return emit_json({"success": False, "error": message, "error_code": code,
                      "download": {**record, "status": "not_fetched"}})


def main() -> int:
    parser = argparse.ArgumentParser(description="根据 Jira 附件 url 下载附件")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Jira 基础地址")
    parser.add_argument("--token", default=None, help="Jira Personal Access Token")
    parser.add_argument("--issue-id", required=True, help="当前任务已确认的实际问题编号")
    parser.add_argument("--url", required=True, help="附件下载地址")
    parser.add_argument("--output-dir", default=".", help="附件保存目录")
    parser.add_argument("--filename", default=None, help="保存文件名，可选")
    parser.add_argument("--timeout", type=int, default=60, help="请求超时秒数")
    parser.add_argument("--max-bytes", type=int, default=104857600, help="单附件下载预算")
    parser.add_argument("--diagnose-auth-redirect", action="store_true",
                        help="仅在原地址跳转至同源 login.jsp 时，以原凭据增加 os_authType=basic 再探测一次；不跟随跳转")
    args = parser.parse_args()
    args.token = read_token(args.token)

    if not re.fullmatch(r"[A-Z][A-Z0-9_]*-[0-9]+", args.issue_id) or args.max_bytes < 1 or args.timeout < 1:
        return emit_json({"success": False, "error": "issue ID 或下载预算无效"})
    validate_url(args.url)
    details = fetch_json("GET", args.base_url, f"/rest/api/2/issue/{args.issue_id}?fields=attachment", args.token, timeout=args.timeout)
    attachments = details.get("fields", {}).get("attachment", [])
    selected = next((item for item in attachments if item.get("content") == args.url), None)
    if selected is None:
        return emit_json({"success": False, "error": "URL 不属于该 Jira 返回的附件，拒绝携带凭据访问"})
    expected = selected.get("size")
    if type(expected) is not int or expected < 0:
        return emit_json({"success": False, "error": "附件元数据缺少有效大小，需重新核对"})
    if expected > args.max_bytes:
        return emit_json({"success": False, "error": "附件超过下载预算"})
    output_dir = Path(args.output_dir).resolve() / args.issue_id
    if output_dir.is_symlink():
        return emit_json({"success": False, "error": "拒绝符号链接输出目录"})
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    filename = args.filename or infer_filename(args.url)
    if filename in (".", "..") or Path(filename).name != filename or "\\" in filename:
        return emit_json({"success": False, "error": "附件文件名不能包含路径"})
    output_path = output_dir / filename
    if output_path.exists() or output_path.is_symlink():
        return emit_json({"success": False, "error": "目标已存在；不得覆盖原证据"})

    record = {"issue_id": args.issue_id, "attachment_id": str(selected["id"]),
              "url": args.url, "filename": filename, "metadata_size": expected,
              "metadata_mime_type": selected.get("mimeType"),
              "checked_at": datetime.now(timezone.utc).isoformat(), "attempts": []}

    headers = {
        "Authorization": f"Bearer {args.token}",
        "Accept": "*/*",
    }

    for attempt in range(2 if args.diagnose_auth_redirect else 1):
        try:
            response = requests.get(args.url, headers=headers, timeout=args.timeout, stream=True,
                                    allow_redirects=False, params={"os_authType": "basic"} if attempt else None)
        except requests.RequestException:
            return failure("network_error", "附件请求失败；核对连接、证书和超时，不据此认定凭据失效", record)
        status = response.status_code
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
        location = urlparse(urljoin(args.url, response.headers.get("Location", "")))
        origin = urlparse(args.url)
        login = (300 <= status < 400 and (location.scheme, location.netloc) == (origin.scheme, origin.netloc)
                 and location.path.endswith("/login.jsp"))
        record["attempts"].append({"mode": "auth_challenge" if attempt else "original_url",
                                   "http_status": status, "content_type": content_type,
                                   "login_redirect": login, "redirect_followed": False})
        if args.diagnose_auth_redirect and not attempt and login and "os_authType" not in parse_qs(origin.query):
            response.close()
            continue
        break

    if status != 200:
        response.close()
        code = {401: "authentication_required", 403: "access_denied", 404: "attachment_unavailable"}.get(status)
        code = code or ("login_redirect" if login else "redirect_blocked" if 300 <= status < 400 else "http_error")
        return failure(code, f"附件响应 HTTP {status}；仅记录本次请求结果，不推断文件删除或全局认证失效", record)

    html_expected = (selected.get("mimeType", "").split(";", 1)[0].lower() in ("text/html", "application/xhtml+xml")
                     or Path(selected.get("filename", "")).suffix.lower() in (".htm", ".html", ".xhtml"))
    if content_type in ("text/html", "application/xhtml+xml") and not html_expected:
        response.close()
        return failure("unexpected_html", "预期非 HTML 附件却收到网页，未作为附件保存", record)

    digest = hashlib.sha256()
    size = 0
    try:
        # TemporaryFile is private and removed on failure; publish only verified bytes.
        with response, tempfile.TemporaryFile(dir=output_dir) as staged:
            for chunk in response.iter_content(chunk_size=min(8192, args.max_bytes + 1)):
                if not size and not html_expected and chunk.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    return failure("unexpected_html", "响应内容为网页，未作为附件保存", record)
                size += len(chunk)
                if size > args.max_bytes or size > expected:
                    return failure("size_mismatch", "实际内容超过元数据大小或下载预算，未保存为有效附件", record)
                digest.update(chunk)
                staged.write(chunk)
            if size != expected:
                return failure("size_mismatch", "实际内容与附件元数据大小不一致，未保存为有效附件", record)
            staged.seek(0)
            with open(output_path, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as file_obj:
                while chunk := staged.read(8192):
                    file_obj.write(chunk)
    except requests.RequestException:
        return failure("transfer_incomplete", "传输中断，未保存为有效附件", record)
    except OSError:
        return failure("local_io_error", "本地保存失败；若有残留文件则不可作为成功下载证据，不覆盖重试", record)

    data = {
        **record,
        "issue_id": args.issue_id,
        "url": args.url,
        "filename": filename,
        "saved_path": str(output_path.resolve()),
        "status_code": response.status_code,
        "size": output_path.stat().st_size,
        "sha256": digest.hexdigest(),
        "size_matches_metadata": True,
        "status": "downloaded",
    }
    return emit_json({"success": True, "data": data})


if __name__ == "__main__":
    raise SystemExit(main())
