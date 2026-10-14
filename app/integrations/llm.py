"""LLM integration — the one way site features call the LLM.

Every feature that asks the LLM something (QA log analysis, 周报, ...)
goes through ask_json(): one OpenAI-compatible endpoint, streamed, the reply
parsed as one JSON object, a failed or unparseable reply asked again.
Callers run it from a worker thread; it blocks.

Reads the [llm] section of release_system.conf at call time:

  LLM_BASE_URL  OpenAI-compatible endpoint, e.g. http://10.x.x.x:8000/v1
  LLM_MODEL     model name passed in the payload
  LLM_API_KEY   bearer token (optional; sent only if set)
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from app import runtime_config

SECTION = "llm"
# The section's name before it served every feature; still recognised only to
# say what to rename.
OLD_SECTION = "qa_llm"
LLM_CONFIG_KEYS = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
# Rough characters per token for mixed Chinese / English / table text; the
# server's own count replaces the estimate once a call reports usage.
SENT_CHARS_PER_TOKEN = 3


class LLMConfigError(RuntimeError):
    """Endpoint not configured — surfaced to the UI as a clear setup message."""


class LLMCallError(RuntimeError):
    """Endpoint returned an error or unparseable body."""


def llm_settings() -> dict[str, str]:
    values = runtime_config.section(SECTION)
    return {key: values.get(key, "") for key in LLM_CONFIG_KEYS}


def require_config() -> dict[str, str]:
    """Return the [llm] settings, or raise LLMConfigError naming what to fill in."""
    settings = llm_settings()
    if settings["LLM_BASE_URL"] and settings["LLM_MODEL"]:
        return settings
    if runtime_config.section(OLD_SECTION):
        raise LLMConfigError(
            "release_system.conf 的 [qa_llm] 已改名为 [llm]：请把节名改为 [llm]，"
            "键名 QA_LLM_BASE_URL / QA_LLM_MODEL / QA_LLM_API_KEY 改为 LLM_BASE_URL / LLM_MODEL / LLM_API_KEY"
        )
    raise LLMConfigError("未配置 LLM：请在 release_system.conf 的 [llm] 中填写 LLM_BASE_URL 和 LLM_MODEL")


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
    """Stream one reply in JSON mode and return its raw text (see ask_json).

    ``progress`` receives a stats dict after each chunk: ``content_chunks``,
    ``reasoning_chunks`` and, once the server reports it, ``usage``
    ({prompt_tokens, completion_tokens})."""
    settings = require_config()
    base = settings["LLM_BASE_URL"].rstrip("/")
    model = settings["LLM_MODEL"]
    key = settings["LLM_API_KEY"]
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


class LlmMeter:
    """Running totals of one job's LLM calls, read by its progress display.

    Every feature counts the same way, so their displays read alike.  Calls
    may run on several threads at once; every update takes the lock.
    ``prompt_tokens`` / ``completion_tokens`` add up what the server reports
    when a call ends; ``received`` counts streamed chunks (about one token
    each) as they arrive, so it moves while calls are still under way.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values = {
            "calls": 0, "finished": 0, "sent_chars": 0, "received": 0, "reasoning": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "usage_calls": 0,
        }

    def snapshot(self) -> dict[str, int]:
        """The totals, plus ``sent_tokens_est``: tokens sent, guessed from characters
        until the server reports them."""
        with self._lock:
            values = dict(self._values)
        values["sent_tokens_est"] = values["sent_chars"] // SENT_CHARS_PER_TOKEN
        return values

    def _add(self, **amounts: int) -> None:
        with self._lock:
            for key, amount in amounts.items():
                self._values[key] += amount

    def wrap(
        self,
        call: Callable[..., str],
        *,
        streaming: bool,
        listener: Callable[[dict[str, int]], None] | None = None,
    ) -> Callable[[str, str], str]:
        """Count *call*; a streaming call (chat_json) also reports chunks and usage.

        *listener* receives the totals after every streamed chunk.
        """
        def counted(system: str, prompt: str) -> str:
            self._add(calls=1, sent_chars=len(system) + len(prompt))
            seen = {"content_chunks": 0, "reasoning_chunks": 0}
            usage: dict[str, int] = {}

            def on_chunk(stats: dict[str, Any]) -> None:
                # Stats are cumulative per call; add only what is new.
                self._add(
                    received=stats.get("content_chunks", 0) - seen["content_chunks"],
                    reasoning=stats.get("reasoning_chunks", 0) - seen["reasoning_chunks"],
                )
                seen.update(content_chunks=stats.get("content_chunks", 0), reasoning_chunks=stats.get("reasoning_chunks", 0))
                if stats.get("usage"):
                    usage.update(stats["usage"])
                if listener:
                    listener(self.snapshot())

            try:
                return call(system, prompt, progress=on_chunk) if streaming else call(system, prompt)
            finally:
                self._add(finished=1)
                if usage:
                    self._add(
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        usage_calls=1,
                    )
        return counted


def parse_json_object(reply: str) -> dict[str, Any]:
    """The JSON object in a reply, tolerating a code fence or text around it."""
    text = reply.strip()
    try:
        value = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise LLMCallError(f"LLM 输出不是 JSON：{text[:300]}") from None
        try:
            value = json.loads(match.group(0))
        except ValueError as exc:
            raise LLMCallError(f"LLM 输出不是有效的 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise LLMCallError(f"LLM 输出不是 JSON 对象：{text[:300]}")
    return value


def ask_json(
    system: str,
    user: str,
    *,
    attempts: int = 2,
    timeout: int = 180,
    meter: LlmMeter | None = None,
    progress: Callable[[dict[str, int]], None] | None = None,
    on_attempt: Callable[[int, int], None] | None = None,
    on_reply: Callable[[], None] | None = None,
    on_retry: Callable[[Exception], None] | None = None,
    call: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Ask the LLM and return the JSON object it answers with.

    A call that fails or answers something unparseable is made again, up to
    *attempts* times in all, after a short pause; a configuration error is
    raised at once.  Every call is counted on *meter* (see LlmMeter), and
    ``progress`` receives its totals after each streamed chunk.
    ``on_attempt(n, attempts)`` runs before each call, ``on_reply()`` when a
    reply has arrived and ``on_retry(error)`` before a retry.  *call* replaces
    the endpoint (tests, offline runs) and is not streamed.
    """
    meter = meter or LlmMeter()
    if call is None:
        call = meter.wrap(
            lambda system_text, user_text, progress: chat_json(
                system_text, user_text, timeout=timeout, progress=progress,
            ),
            streaming=True,
            listener=progress,
        )
    else:
        call = meter.wrap(call, streaming=False)
    attempts = max(1, attempts)
    for attempt in range(1, attempts + 1):
        if on_attempt:
            on_attempt(attempt, attempts)
        try:
            reply = call(system, user)
            if on_reply:
                on_reply()
            return parse_json_object(reply)
        except LLMConfigError:
            raise
        except Exception as exc:
            if attempt >= attempts:
                raise
            if on_retry:
                on_retry(exc)
            time.sleep(min(attempt, 3))
    raise AssertionError("unreachable")
