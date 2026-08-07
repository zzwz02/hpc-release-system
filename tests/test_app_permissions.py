from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.main import create_app


def test_direct_create_api_is_removed(temp_db):
    app = create_app()

    def override_db():
        yield temp_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: {
        "username": "rm",
        "role": "RM",
        "display_name": "RM",
    }

    body = {
        "release_id": "rel-1",
        "official_name": "GuestApp",
        "git_url": "ssh://example/guest",
        "git_branch": "main",
        "release_decision": "release",
    }
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/api/apps/new", json=body)

    # The SPA fallback owns unmatched paths and rejects non-GET methods with
    # 405; OpenAPI absence proves there is no direct-create API handler.
    assert resp.status_code == 405
    assert "/api/apps/new" not in app.openapi()["paths"]
