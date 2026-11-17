"""RM manages admission; a scoped CI credential can only perform checks."""
import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app import runtime_config
from app.deps import get_db, require_capability, require_tab_access
from app.repositories import release_gate_repo, releases_repo
from app.services import release_gate_service

router = APIRouter(prefix="/api/cicd/release-gate", tags=["release-gate"])


class VersionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    maca_version: str = Field(min_length=1, max_length=100)
    release_id: str = Field(min_length=1, max_length=100)
    enabled: StrictBool = True
    note: str = Field(default="", max_length=1000)
    expected_revision: int = Field(default=-1, ge=-1)


class GateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    build_type: str = Field(max_length=30)
    maca_version: str = Field(min_length=1, max_length=100)
    git_url: str = Field(min_length=1, max_length=1024)
    git_branch: str = Field(min_length=1, max_length=255)
    phase: Literal["preflight", "verify"] = "preflight"
    actual_commit: str = Field(default="", max_length=40, pattern=r"^(?:[a-fA-F0-9]{40})?$")
    baseline_commit: str = Field(default="", max_length=40)
    resolved_expected_commit: str = Field(default="", max_length=40)
    source_project: str = Field(default="", max_length=512)


def require_ci(authorization: str = Header(default="")):
    token = runtime_config.section("release_gate").get("RELEASE_BUILD_TOKEN", "")
    if not token:
        raise HTTPException(503, "Release build checks are not configured")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
        raise HTTPException(403, "Trusted release builder required")


@router.get("/versions")
def versions(user=Depends(require_tab_access("cicd")), conn=Depends(get_db)):
    return {"versions": release_gate_repo.list_versions(conn),
            "releases": [{"id": r["id"], "name": r["name"], "released_locked": r["released_locked"]}
                         for r in releases_repo.list_release_rows(conn)]}


@router.post("/versions")
def save_version(body: VersionWrite, user=Depends(require_capability("release.manage")), conn=Depends(get_db)):
    return release_gate_service.save_version(conn, body, user)


@router.post("/check", dependencies=[Depends(require_ci)])
def check(body: GateCheck, conn=Depends(get_db)):
    return release_gate_service.check(conn, body)
