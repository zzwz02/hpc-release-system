"""Account token management uses browser login; validation is CI-only."""
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_db, require_login
from app.repositories import api_tokens_repo
from app.services import api_token_service

router = APIRouter(tags=["account-api-tokens"])


def private_response(response: Response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def same_origin(request: Request):
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin and (origin == "null" or urlsplit(origin).netloc != request.url.netloc)
    ):
        raise HTTPException(403, "Cross-origin token management is not allowed")


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)


class TokenCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["failure_report", "test_results", "release_gate_failure"]


@router.get("/api/account/tokens", dependencies=[Depends(private_response)])
def list_tokens(user=Depends(require_login), conn=Depends(get_db)):
    return {"tokens": api_tokens_repo.list_tokens(conn, user["username"]),
            "ci_allowed": user["role"] in api_token_service.CI_ROLES}


@router.post("/api/account/tokens", dependencies=[Depends(private_response), Depends(same_origin)])
def create_token(body: TokenCreate, user=Depends(require_login), conn=Depends(get_db)):
    return {"token": api_token_service.create(conn, user, body.name)}


@router.post("/api/account/tokens/{token_id}/revoke", dependencies=[Depends(private_response), Depends(same_origin)])
def revoke_token(token_id: int, user=Depends(require_login), conn=Depends(get_db)):
    return {"token": api_token_service.revoke(conn, user, token_id)}


@router.delete("/api/account/tokens/{token_id}", dependencies=[Depends(private_response), Depends(same_origin)])
def delete_token(token_id: int, user=Depends(require_login), conn=Depends(get_db)):
    api_token_service.delete(conn, user, token_id)
    return {"ok": True}


@router.post("/api/ci/authenticate", dependencies=[Depends(private_response)])
def authenticate(body: TokenCheck, authorization: str = Header(default=""), conn=Depends(get_db)):
    return {"identity": api_token_service.authenticate(conn, authorization, body.operation),
            "authorized": True, "operation": body.operation}
