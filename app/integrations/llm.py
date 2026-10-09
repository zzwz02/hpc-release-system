"""LLM integration — QA analysis via the local OpenAI-compatible endpoint.

Background QA jobs call this synchronous adapter from a worker thread.

Reads the [qa_llm] section of release_system.conf at call time.

  QA_LLM_BASE_URL  e.g. http://10.x.x.x:8000/v1
  QA_LLM_API_KEY   bearer token (optional; sent only if set)
  QA_LLM_MODEL     model name passed in the payload
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from app import runtime_config


class LLMConfigError(RuntimeError):
    """Endpoint not configured — surfaced to the UI as a clear setup message."""


class LLMCallError(RuntimeError):
    """Endpoint returned an error or unparseable body."""


LLM_CONFIG_KEYS = ("QA_LLM_BASE_URL", "QA_LLM_API_KEY", "QA_LLM_MODEL")


def llm_settings() -> dict[str, str]:
    values = runtime_config.section("qa_llm")
    return {key: values.get(key, "") for key in LLM_CONFIG_KEYS}


def _stream_delta_text(chunk, field: str) -> str:
    try:
        choice = chunk.choices[0]
    except (AttributeError, IndexError, TypeError):
        return ""
    delta = getattr(choice, "delta", None)
    if isinstance(delta, dict):
        value = delta.get(field)
    else:
        value = getattr(delta, field, None)
    return value if isinstance(value, str) else ""


def _stream_usage(chunk) -> dict[str, int] | None:
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return None
    get = usage.get if isinstance(usage, dict) else lambda key: getattr(usage, key, None)
    prompt, completion = get("prompt_tokens"), get("completion_tokens")
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    return {"prompt_tokens": prompt, "completion_tokens": completion}


def chat_json(
    system: str,
    user: str,
    *,
    timeout: int = 180,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Stream a JSON reply.  ``progress`` receives a stats dict after each
    chunk: ``content_chunks``, ``reasoning_chunks`` and, once the server
    reports it, ``usage`` ({prompt_tokens, completion_tokens})."""
    settings = llm_settings()
    base = settings["QA_LLM_BASE_URL"].rstrip("/")
    model = settings["QA_LLM_MODEL"]
    if not base or not model:
        raise LLMConfigError(
            "未配置本地 LLM 服务：请在 release_system.conf 的 [qa_llm] 中填写 QA_LLM_BASE_URL 和 QA_LLM_MODEL"
        )
    key = settings["QA_LLM_API_KEY"]
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LLMConfigError("服务器未安装 OpenAI Python SDK：请运行 python -m pip install openai") from exc

    client = OpenAI(base_url=base, api_key=key or "not-needed", timeout=timeout)
    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": True,
    }
    try:
        try:
            stream = client.chat.completions.create(**request, stream_options={"include_usage": True})
        except Exception as exc:
            # Servers that reject stream_options answer 400/422; retry without it.
            if getattr(exc, "status_code", None) not in (400, 422):
                raise
            stream = client.chat.completions.create(**request)
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status:
            raise LLMCallError(f"LLM HTTP {status}: {str(exc)[:400]}") from exc
        raise LLMCallError(f"LLM 调用失败：{str(exc)[:400]}") from exc

    chunks: list[str] = []
    stats: dict[str, Any] = {"content_chunks": 0, "reasoning_chunks": 0, "usage": None}
    try:
        for chunk in stream:
            usage = _stream_usage(chunk)
            if usage:
                stats["usage"] = usage
            content = _stream_delta_text(chunk, "content")
            reasoning = _stream_delta_text(chunk, "reasoning_content")
            if content:
                chunks.append(content)
                stats["content_chunks"] += 1
            if reasoning:
                stats["reasoning_chunks"] += 1
            if progress and (content or reasoning or usage):
                progress(dict(stats))
        content = "".join(chunks)
        if not content:
            raise ValueError("empty content")
        return content
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise LLMCallError(f"LLM 返回格式异常：{str(chunks)[:400]}") from exc
    except Exception as exc:
        raise LLMCallError(f"LLM 流式返回失败：{str(exc)[:400]}") from exc
