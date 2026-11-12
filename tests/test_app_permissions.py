from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.main import create_app
from tests.conftest import seed_app, seed_cycle


@pytest.fixture()
def client(temp_db):
    """A TestClient whose session is *temp_db*, logged in as RM by default."""
    app = create_app()

    def override_db():
        yield temp_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_login] = lambda: {
        "username": "rm",
        "role": "RM",
        "display_name": "RM",
    }
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.app_instance = app
        yield test_client


def test_apps_are_created_only_through_cicd_requests(client):
    """There is no direct create API; new Apps come from a CICD create request."""
    resp = client.post(
        "/api/apps/new",
        json={"official_name": "GuestApp", "git_url": "ssh://example/guest", "git_branch": "main"},
    )
    # The SPA fallback owns unmatched paths and rejects non-GET methods with
    # 405; OpenAPI absence proves there is no direct-create API handler.
    assert resp.status_code == 405
    paths = client.app_instance.openapi()["paths"]
    assert "/api/apps/new" not in paths
    assert "/api/cicd/apps/new" in paths


def test_owner_may_only_edit_their_own_app_profile(temp_db, client):
    seed_app(temp_db, "owned", owners=["owner_test"])
    seed_app(temp_db, "foreign", owners=["someone_else"])
    client.app_instance.dependency_overrides[require_login] = lambda: {
        "username": "owner_test",
        "role": "Owner",
        "display_name": "Owner",
    }

    ok = client.post(
        "/api/apps/profile",
        json={"app_id": "owned", "fields": {"description": "我的应用"}},
    )
    assert ok.status_code == 200
    assert ok.json()["app"]["description"] == "我的应用"

    denied = client.post(
        "/api/apps/profile",
        json={"app_id": "foreign", "fields": {"description": "别人的应用"}},
    )
    assert denied.status_code == 403

    rm_only = client.post(
        "/api/apps/profile",
        json={"app_id": "owned", "fields": {"owners": ["owner_test", "other"]}},
    )
    assert rm_only.status_code == 403


def test_guest_cannot_sign_an_app_up(temp_db, client):
    app_id = seed_app(temp_db)
    release_id = seed_cycle(temp_db)
    client.app_instance.dependency_overrides[require_login] = lambda: {
        "username": "guest",
        "role": "Guest",
        "display_name": "Guest",
    }

    resp = client.post(
        "/api/entries/signup",
        json={"release_id": release_id, "app_id": app_id, "target_version": "1.0"},
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role,expected", [("RM", 200), ("Owner", 200), ("QA", 200), ("Guest", 403), ("SPD", 403), ("Admin", 403)])
def test_history_and_dependencies_access_including_exports(temp_db, client, role, expected):
    app_id = seed_app(temp_db)
    client.app_instance.dependency_overrides[require_login] = lambda: {
        "username": "reader", "role": role, "display_name": "Reader",
    }
    for path, params in [
        ("/api/lookup/app", {"app_id": app_id}),
        ("/api/lookup/digest", {"digest": "sha256:missing"}),
        ("/api/dependencies", {}),
        ("/api/dependencies/compat.csv", {}),
    ]:
        response = client.get(path, params=params)
        assert response.status_code == expected, (role, path, response.text)
