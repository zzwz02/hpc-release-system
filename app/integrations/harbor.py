"""Read-only artifact lookup on the internal HPC Harbor registry."""
from __future__ import annotations

import ssl
from urllib.parse import quote

import httpx

from app.api.errors import ApiError

# The HPC release registry is a fixed internal integration, like the Gerrit project.
HARBOR_HOST = "harbor.sh.mxcr.io"
HARBOR_API_BASE = f"https://{HARBOR_HOST}/api/v2.0"
HARBOR_TIMEOUT_SECONDS = 15


def get_artifact(project: str, repository: str, digest: str) -> dict:
    # Harbor requires double encoding for repository names containing a slash.
    repository_path = quote(quote(repository, safe=""), safe="")
    url = (f"{HARBOR_API_BASE}/projects/{quote(project, safe='')}/repositories/"
           f"{repository_path}/artifacts/{quote(digest, safe='')}")
    try:
        # The system trust store contains the internal CA. Do not bypass TLS or
        # send intranet traffic through proxy settings inherited from the shell.
        with httpx.Client(timeout=HARBOR_TIMEOUT_SECONDS, verify=ssl.create_default_context(),
                          trust_env=False, follow_redirects=False) as client:
            response = client.get(url, params={"with_tag": "true"})
    except httpx.HTTPError as exc:
        raise ApiError(502, f"Harbor 查询失败（{type(exc).__name__}），请稍后重试") from exc
    if response.status_code == 404:
        raise ValueError("Harbor 中未找到该镜像，请检查镜像名和 digest")
    if response.status_code in (401, 403):
        raise ApiError(502, "Harbor 拒绝读取该镜像，请检查仓库的读取权限")
    if response.status_code != 200:
        raise ApiError(502, f"Harbor 查询失败（HTTP {response.status_code}）")
    try:
        artifact = response.json()
    except ValueError as exc:
        raise ApiError(502, "Harbor 未返回有效 JSON") from exc
    if not isinstance(artifact, dict) or artifact.get("digest") != digest:
        raise ApiError(502, "Harbor 返回的镜像 digest 与查询不一致")
    return artifact
