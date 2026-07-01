from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.main import create_app


def test_guest_cannot_create_app_via_api(temp_db):
    app = create_app()

    def override_db():
        yield temp_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: {
        "username": "guest",
        "role": "Guest",
        "display_name": "Guest",
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

    assert resp.status_code == 403
