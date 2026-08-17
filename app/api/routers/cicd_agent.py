"""CICD Agent proxy routes.

The browser stays same-origin with hpc_release_system while this router talks to
the standalone CICD_Agent backend configured by settings.cicd_agent_base_url.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import require_login

router = APIRouter(prefix="/api/cicd-agent", tags=["cicd-agent"])


def _agent_url(path: str, query: str = "") -> str:
    base = settings.cicd_agent_base_url.rstrip("/")
    url = f"{base}{path}"
    return f"{url}?{query}" if query else url


def _decode_json(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"ok": False, "error": text or "CICD Agent returned a non-JSON response"}


def _request_agent(method: str, path: str, *, query: str = "", body: Any = None) -> JSONResponse:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        _agent_url(path, query),
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=settings.cicd_agent_timeout_seconds) as response:
            payload = _decode_json(response.read())
            return JSONResponse(status_code=response.status, content=payload)
    except urllib.error.HTTPError as exc:
        payload = _decode_json(exc.read())
        return JSONResponse(status_code=exc.code, content=payload)
    except urllib.error.URLError as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": f"CICD Agent 不可用：{exc.reason}"},
        )


@router.get("/failures")
def list_failures(
    request: Request,
    _user: dict = Depends(require_login),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures", query=request.url.query)


@router.get("/failures/summary")
def summarize_failures(
    request: Request,
    _user: dict = Depends(require_login),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures/summary", query=request.url.query)


@router.get("/failures/filter-options")
def failure_filter_options(
    request: Request,
    _user: dict = Depends(require_login),
) -> JSONResponse:
    return _request_agent("GET", "/api/v1/failures/filter-options", query=request.url.query)


@router.get("/failures/{record_id}")
def failure_detail(
    record_id: int,
    _user: dict = Depends(require_login),
) -> JSONResponse:
    return _request_agent("GET", f"/api/v1/failures/{record_id}")


@router.post("/failure-chat")
async def failure_chat(
    request: Request,
    _user: dict = Depends(require_login),
) -> JSONResponse:
    body = await request.json()
    return _request_agent("POST", "/api/v1/failure-chat", body=body)
