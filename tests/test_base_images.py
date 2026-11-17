"""Full base-image references: parsing, storage, lookup and RM permissions."""
import pytest
from fastapi.testclient import TestClient

from app.deps import get_db, require_login
from app.domain.container_image import parse_base_image_reference
from app.main import create_app
from app.repositories import releases_repo
from app.services import cycle_service
from tests.conftest import close_qa, close_signup, seed_app, seed_cycle, seed_signup

DIGEST = "sha256:f47fe6f2055e1d792689047debda38355a6dd2a7d1482bb3d21636152f361447"
ADDRESS = "harbor.sh.mxcr.io/hpc-release/amgx-maca"
REFERENCE = f"{ADDRESS}@{DIGEST}"


@pytest.mark.parametrize("reference,name,address,digest", [
    (REFERENCE, "amgx-maca", ADDRESS, DIGEST),
    (f"docker pull {REFERENCE}", "amgx-maca", ADDRESS, DIGEST),
    (f"  docker\tpull   {REFERENCE}  ", "amgx-maca", ADDRESS, DIGEST),
    (f"registry:5000/hpc/base:3.11@{DIGEST}", "base:3.11", "registry:5000/hpc/base:3.11", DIGEST),
    ("harbor.example/hpc/base:latest", "base:latest", "harbor.example/hpc/base:latest", ""),
    (f"  {ADDRESS}@{DIGEST.upper().replace('SHA256', 'sha256')}  ", "amgx-maca", ADDRESS, DIGEST),
])
def test_parse_full_image_reference(reference, name, address, digest):
    assert parse_base_image_reference(reference) == {"name": name, "image": address, "digest": digest}


@pytest.mark.parametrize("reference", [
    "", "docker pull", "docker pull hpc/a:1 hpc/b:2", "hpc/a:1 hpc/b:2", "hpc/a:1\nhpc/b:2", "https://harbor.example/hpc/base:1",
    "harbor.example/hpc/", "harbor.example/hpc/base:", f"@{DIGEST}",
    f"{ADDRESS}@sha256:abc", f"{ADDRESS}@sha256:{'z' * 64}", f"{REFERENCE}@{DIGEST}",
])
def test_invalid_references_explain_the_error(reference):
    with pytest.raises(ValueError):
        parse_base_image_reference(reference)


@pytest.fixture()
def api(temp_db):
    app = create_app()
    user = {"username": "rm", "role": "RM", "display_name": "RM"}
    app.dependency_overrides[get_db] = lambda: temp_db
    app.dependency_overrides[require_login] = lambda: user
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, user


def test_http_save_edit_lookup_and_legacy_record_round_trip(temp_db, api):
    client, _ = api
    release_id = seed_cycle(temp_db)
    legacy = cycle_service.save_base_image(
        temp_db, release_id, {"name": "Existing name", "image": "hpc/legacy:1", "digest": "", "note": "old"},
        user="rm", role="RM",
    )[0]
    response = client.post("/api/releases/base-image", json={
        "release_id": release_id, "image": {"reference": f"docker pull {REFERENCE}", "note": " RM 的说明 "},
    })
    assert response.status_code == 200
    image = response.json()["base_images"][-1]
    assert {key: image[key] for key in ("name", "image", "digest", "note")} == {
        "name": "amgx-maca", "image": ADDRESS, "digest": DIGEST, "note": "RM 的说明",
    }
    assert releases_repo.get_base_image(temp_db, legacy["id"]) == legacy
    found = client.get("/api/lookup/digest", params={"digest": DIGEST}).json()["base_images"]
    assert [row["id"] for row in found] == [image["id"]]
    app_id = seed_app(temp_db)
    entry = seed_signup(temp_db, release_id, app_id, base_image_id=image["id"])
    assert entry["base_image_id"] == image["id"]
    changed = client.post("/api/releases/base-image", json={
        "release_id": release_id, "image": {"id": image["id"], "reference": f"registry:5000/hpc/base:v2@{DIGEST}", "note": "updated"},
    })
    assert changed.status_code == 200
    saved = client.get("/api/state", params={"release_id": release_id}).json()["release"]["base_images"][-1]
    assert saved["id"] == image["id"]
    assert (saved["name"], saved["image"], saved["digest"], saved["note"]) == (
        "base:v2", "registry:5000/hpc/base:v2", DIGEST, "updated",
    )


def test_failed_parse_leaves_existing_record_and_audit_unchanged(temp_db, api):
    client, _ = api
    release_id = seed_cycle(temp_db)
    image = cycle_service.save_base_image(temp_db, release_id, {"reference": REFERENCE}, user="rm", role="RM")[0]
    audit_count = temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    for supplied in [{"reference": f"{ADDRESS}@sha256:short"}, {"id": image["id"], "reference": ""}]:
        response = client.post("/api/releases/base-image", json={"release_id": release_id, "image": supplied})
        assert response.status_code == 400
        assert response.json()["error"]
    assert releases_repo.list_base_images(temp_db, release_id) == [image]
    assert temp_db.execute("SELECT COUNT(*) FROM audit").fetchone()[0] == audit_count


def test_base_images_remain_editable_after_signup_deadline_but_lock_on_publication(temp_db, api):
    client, _ = api
    release_id = seed_cycle(temp_db)
    close_signup(temp_db, release_id)
    payload = {"release_id": release_id, "image": {"reference": REFERENCE}}
    assert client.post("/api/releases/base-image", json=payload).status_code == 200
    close_qa(temp_db, release_id)
    cycle_service.publish(temp_db, release_id, user="rm", role="RM")
    response = client.post("/api/releases/base-image", json=payload)
    assert response.status_code == 400
    assert "只读" in response.json()["error"]


@pytest.mark.parametrize("role", ["Owner", "QA", "Guest", "Admin", "SPD"])
def test_only_rm_can_save_full_image_references(temp_db, api, role):
    client, user = api
    release_id = seed_cycle(temp_db)
    user["role"] = role
    response = client.post("/api/releases/base-image", json={"release_id": release_id, "image": {"reference": REFERENCE}})
    assert response.status_code == 403
    assert releases_repo.list_base_images(temp_db, release_id) == []
