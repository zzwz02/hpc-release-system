"""Developer-community artifact export via hpcbox."""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from app.config import settings


_SDK_VERSION_RE = re.compile(r"^\d+(?:\.\d+){3,}$")
_CATEGORY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def fetch_community_artifacts(sdkversion: str, category: str = "ai4s") -> dict[str, Any]:
    """Run hpcbox and return three tiny JSON artifacts without caching."""
    sdkversion = str(sdkversion or "").strip()
    category = str(category or "ai4s").strip() or "ai4s"

    if not _SDK_VERSION_RE.match(sdkversion):
        raise ValueError("请输入完整 SDK 版本号，例如 3.9.0.12")
    if not _CATEGORY_RE.match(category):
        raise ValueError("category 只能包含字母、数字、下划线或短横线")

    cmd = [
        settings.hpcbox_bin,
        "qpub",
        sdkversion,
        "--type",
        "all",
        "--category",
        category,
        "--export-json",
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.hpcbox_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到 hpcbox 可执行文件: {settings.hpcbox_bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("hpcbox 查询超时，请稍后重试") from exc

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode != 0:
        detail = stderr or stdout or f"exit code {result.returncode}"
        raise RuntimeError(f"hpcbox 查询失败：{detail}")

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        detail = stderr or stdout[:500] or "stdout 为空"
        raise RuntimeError(f"hpcbox 未返回有效 JSON：{detail}") from exc

    files = payload.get("files")
    if not isinstance(files, list):
        raise RuntimeError("hpcbox 返回缺少 files 字段")
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
            raise RuntimeError("hpcbox 返回的 files 格式不正确")
        if "content" not in item:
            raise RuntimeError(f"hpcbox 返回的 {item.get('filename')} 缺少 content")

    return {
        "sdkversion": str(payload.get("sdkversion") or sdkversion),
        "category": str(payload.get("category") or category),
        "files": files,
    }
