"""Read the public, pinned repo manifests archived by the Jenkins pipeline."""
from __future__ import annotations

import re
import ssl
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit

import httpx

from app import identity, runtime_config
from app.api.errors import ApiError

# Manifests are small XML files; bound upstream time and response size.
MANIFEST_TIMEOUT_SECONDS = 15
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ArchiveNotFound(ValueError):
    """This exact version/build manifest was not archived."""


def app_info_commit(path: str, expected_url: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z0-9_./-]+\.xml", path) or any(
        part in ("", ".", "..") for part in path.split("/")
    ):
        raise ValueError("历史 manifest 归档路径无效")
    section = "hpc_manifest_archive"
    values = runtime_config.section(section)
    base = runtime_config.required(section, values, "BASE_URL").rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise runtime_config.ConfigError(f"release_system.conf [{section}] BASE_URL 必须是无账号、查询参数的 HTTPS 地址")
    url = f"{base}/{quote(path, safe='/')}"
    try:
        with httpx.Client(timeout=MANIFEST_TIMEOUT_SECONDS, verify=ssl.create_default_context(),
                          trust_env=False, follow_redirects=False) as client:
            with client.stream("GET", url) as response:
                if response.status_code == 404:
                    raise ArchiveNotFound(f"未找到该构建的固定 manifest 归档：{path}")
                if response.status_code != 200:
                    raise ApiError(502, f"manifest 归档查询失败（HTTP {response.status_code}）")
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_MANIFEST_BYTES:
                        raise ApiError(502, "manifest 归档超过大小限制")
    except httpx.HTTPError as exc:
        raise ApiError(502, f"manifest 归档查询失败（{type(exc).__name__}）") from exc
    try:
        project_url, commit = identity.manifest_app_info_identity(content)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError("历史 manifest 无效或无法唯一确定 app_info 项目") from exc
    if project_url != expected_url:
        raise ValueError("历史 manifest 的 app_info 项目与登记的 Gerrit 仓库不匹配")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("历史 manifest 未固定 app_info commit，不能使用分支最新版本")
    return {"git_url": project_url, "commit_id": commit.lower(), "manifest_url": url}
