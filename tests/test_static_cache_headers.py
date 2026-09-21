"""Static-asset cache headers.

A deploy replaces index.html and the fingerprinted bundle it points at.  If
index.html may be cached, browsers keep serving the previous one after the
deploy and the site looks unchanged, so index.html must always be revalidated
while /assets/* (content-hashed) may be cached indefinitely.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_DIST = PROJECT_ROOT / "web_dist"


@pytest.fixture()
def client() -> TestClient:
    if not WEB_DIST.exists():
        pytest.skip("web_dist not built")
    return TestClient(create_app())


def _asset_path() -> str:
    assets = sorted((WEB_DIST / "assets").glob("*.js"))
    if not assets:
        pytest.skip("no built assets")
    return f"/assets/{assets[0].name}"


def test_index_html_is_never_cached(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]


def test_spa_deep_link_fallback_is_never_cached(client: TestClient) -> None:
    response = client.get("/apps")
    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]


def test_missing_asset_is_a_real_404(client: TestClient) -> None:
    """A deploy deletes the previous chunks; a stale tab must get a 404.

    Answering with index.html would hand the browser HTML where it expects a
    JavaScript module, turning a clear 404 into a module-parse error.
    """
    response = client.get("/assets/index-Deleted0.js")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_fingerprinted_assets_are_cached_forever(client: TestClient) -> None:
    response = client.get(_asset_path())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
