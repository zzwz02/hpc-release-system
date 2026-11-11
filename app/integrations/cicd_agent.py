"""Shared synchronous CICD Agent transport for website routes and MCP tools."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app import runtime_config


def _agent_timeout_seconds() -> int:
    return runtime_config.required_int("cicd_agent", runtime_config.section("cicd_agent"), "TIMEOUT_SECONDS")


def _agent_url(path: str, query: str = "") -> str:
    base = runtime_config.required("cicd_agent", runtime_config.section("cicd_agent"), "BASE_URL").rstrip("/")
    url = f"{base}{path}"
    return f"{url}?{query}" if query else url


def _decode_json(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except ValueError:
        return {"ok": False, "error": text or "CICD Agent returned a non-JSON response"}


def _request_agent_payload(
    method: str,
    path: str,
    *,
    query: str = "",
    body: Any = None,
    actor: dict | None = None,
) -> tuple[int, Any]:
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    gateway_token = runtime_config.section("cicd_agent").get("REPRO_GATEWAY_TOKEN", "")
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    if actor is not None:
        headers["X-Actor"] = str(actor.get("username") or actor.get("display_name") or "frontend")
        headers["X-Actor-Role"] = str(actor.get("role") or "")
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    try:
        url, timeout_seconds = _agent_url(path, query), _agent_timeout_seconds()
    except runtime_config.ConfigError as exc:
        return 503, {"ok": False, "error": str(exc)}
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            payload = _decode_json(response.read())
            return response.status, payload
    except urllib.error.HTTPError as exc:
        payload = _decode_json(exc.read())
        return exc.code, payload
    except urllib.error.URLError as exc:
        return 502, {"ok": False, "error": f"CICD Agent 不可用：{exc.reason}"}
