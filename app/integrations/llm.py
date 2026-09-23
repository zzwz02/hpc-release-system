"""LLM integration — QA analysis via the local OpenAI-compatible endpoint.

Background QA jobs call this synchronous adapter from a worker thread.

Reads the [qa_llm] section of release_system.conf at call time.

  QA_LLM_BASE_URL  e.g. http://10.x.x.x:8000/v1
  QA_LLM_API_KEY   bearer token (optional; sent only if set)
  QA_LLM_MODEL     model name passed in the payload
"""
from __future__ import annotations

from collections.abc import Callable

from app import runtime_config


class LLMConfigError(RuntimeError):
    """Endpoint not configured — surfaced to the UI as a clear setup message."""


class LLMCallError(RuntimeError):
    """Endpoint returned an error or unparseable body."""


LLM_CONFIG_KEYS = ("QA_LLM_BASE_URL", "QA_LLM_API_KEY", "QA_LLM_MODEL")


def llm_settings() -> dict[str, str]:
    values = runtime_config.section("qa_llm")
    return {key: values.get(key, "") for key in LLM_CONFIG_KEYS}


def _stream_delta_content(chunk) -> str:
    try:
        choice = chunk.choices[0]
    except (AttributeError, IndexError, TypeError):
        return ""
    delta = getattr(choice, "delta", None)
    if isinstance(delta, dict):
        content = delta.get("content")
    else:
        content = getattr(delta, "content", None)
    return content if isinstance(content, str) else ""


def chat_json(
    system: str,
    user: str,
    *,
    timeout: int = 180,
    progress: Callable[[int], None] | None = None,
) -> str:
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
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            stream=True,
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status:
            raise LLMCallError(f"LLM HTTP {status}: {str(exc)[:400]}") from exc
        raise LLMCallError(f"LLM 调用失败：{str(exc)[:400]}") from exc

    chunks: list[str] = []
    token_count = 0
    try:
        for chunk in stream:
            content = _stream_delta_content(chunk)
            if not content:
                continue
            chunks.append(content)
            token_count += 1
            if progress:
                progress(token_count)
        content = "".join(chunks)
        if not content:
            raise ValueError("empty content")
        return content
    except (AttributeError, IndexError, TypeError, ValueError) as exc:
        raise LLMCallError(f"LLM 返回格式异常：{str(chunks)[:400]}") from exc
    except Exception as exc:
        raise LLMCallError(f"LLM 流式返回失败：{str(exc)[:400]}") from exc
