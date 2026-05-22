"""Thin OpenAI-compatible chat client for the local/intranet LLM endpoint.

Reads three environment variables at call time:
  QA_LLM_BASE_URL  e.g. http://10.x.x.x:8000/v1
  QA_LLM_API_KEY   bearer token (optional; sent only if set)
  QA_LLM_MODEL     model name passed in the payload
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


class LLMConfigError(RuntimeError):
    """Endpoint not configured — surfaced to the UI as a clear setup message."""


class LLMCallError(RuntimeError):
    """Endpoint returned an error or unparseable body."""


def chat_json(system: str, user: str, *, timeout: int = 180) -> str:
    base = os.environ.get("QA_LLM_BASE_URL", "").rstrip("/")
    model = os.environ.get("QA_LLM_MODEL", "")
    if not base or not model:
        raise LLMConfigError(
            "未配置本地 LLM 服务：请设置环境变量 QA_LLM_BASE_URL 和 QA_LLM_MODEL"
        )
    key = os.environ.get("QA_LLM_API_KEY", "")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise LLMCallError(f"LLM HTTP {exc.code}: {detail[:400]}") from exc
    except urllib.error.URLError as exc:
        raise LLMCallError(f"LLM 不可达：{exc.reason}") from exc
    try:
        data = json.loads(body)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, json.JSONDecodeError) as exc:
        raise LLMCallError(f"LLM 返回格式异常：{body[:400]}") from exc
