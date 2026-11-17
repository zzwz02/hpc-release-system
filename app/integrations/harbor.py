"""Read-only artifact lookup on the internal HPC Harbor registry."""
from __future__ import annotations

from urllib.parse import quote

import httpx

from app.api.errors import ApiError
from app.integrations.read_session import http_client

# The HPC release registry is a fixed internal integration, like the Gerrit project.
HARBOR_HOST = "harbor.sh.mxcr.io"
HARBOR_API_BASE = f"https://{HARBOR_HOST}/api/v2.0"
HARBOR_TIMEOUT_SECONDS = 15
# Harbor's public API supports at most 100 artifacts per page.
ARTIFACT_PAGE_SIZE = 100


def get_artifact(project: str, repository: str, digest: str) -> dict:
    # Harbor requires double encoding for repository names containing a slash.
    repository_path = quote(quote(repository, safe=""), safe="")
    url = (f"{HARBOR_API_BASE}/projects/{quote(project, safe='')}/repositories/"
           f"{repository_path}/artifacts/{quote(digest, safe='')}")
    try:
        # The system trust store contains the internal CA. Do not bypass TLS or
        # send intranet traffic through proxy settings inherited from the shell.
        with http_client(HARBOR_TIMEOUT_SECONDS) as client:
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


def list_artifacts(project: str, repository: str, version: str) -> list[dict]:
    """List live tags in a version family; a missing repository means no images."""
    repository_path = quote(quote(repository, safe=""), safe="")
    url = f"{HARBOR_API_BASE}/projects/{quote(project, safe='')}/repositories/{repository_path}/artifacts"
    artifacts, page, seen_pages = [], 1, set()
    with http_client(HARBOR_TIMEOUT_SECONDS) as client:
        while True:
            try:
                response = client.get(url, params={
                    "q": f"tags=~{version}", "with_tag": "true",
                    "page_size": ARTIFACT_PAGE_SIZE, "page": page,
                })
            except httpx.HTTPError as exc:
                raise ApiError(502, f"Harbor 查询失败（{type(exc).__name__}），请稍后重试") from exc
            if response.status_code == 404:
                return []
            if response.status_code in (401, 403):
                raise ApiError(502, "Harbor 拒绝读取镜像列表，请检查仓库的读取权限")
            if response.status_code != 200:
                raise ApiError(502, f"Harbor 镜像列表查询失败（HTTP {response.status_code}）")
            try:
                data = response.json()
            except ValueError as exc:
                raise ApiError(502, "Harbor 未返回有效 JSON") from exc
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise ApiError(502, "Harbor 镜像列表格式错误")
            signature = tuple(str(item.get("digest", "")) for item in data)
            if data and signature in seen_pages:
                raise ApiError(502, "Harbor 镜像列表分页重复，请重试")
            seen_pages.add(signature)
            artifacts.extend(data)
            if len(data) < ARTIFACT_PAGE_SIZE:
                return artifacts
            page += 1
