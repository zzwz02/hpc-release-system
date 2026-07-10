"""LLM integration — QA analysis via the local OpenAI-compatible endpoint.

Background QA jobs call this synchronous adapter from a worker thread.

Reads settings at call time.  Environment variables take precedence; otherwise
the project-root qa_llm.env file is used.  The file format is plain KEY=VALUE,
optionally with Linux-style "export KEY=VALUE" lines.

  QA_LLM_BASE_URL  e.g. http://10.x.x.x:8000/v1
  QA_LLM_API_KEY   bearer token (optional; sent only if set)
  QA_LLM_MODEL     model name passed in the payload
  QA_LLM_ENV_FILE  optional custom config file path
"""
from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from app.config import settings as app_settings


class LLMConfigError(RuntimeError):
    """Endpoint not configured — surfaced to the UI as a clear setup message."""


class LLMCallError(RuntimeError):
    """Endpoint returned an error or unparseable body."""


LLM_CONFIG_KEYS = ("QA_LLM_BASE_URL", "QA_LLM_API_KEY", "QA_LLM_MODEL")


def configured_env_file() -> Path:
    custom = os.environ.get("QA_LLM_ENV_FILE", "").strip()
    return Path(os.path.expandvars(custom)).expanduser() if custom else app_settings.qa_llm_env_file


def read_env_file(path: str | Path) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in LLM_CONFIG_KEYS:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def llm_settings() -> dict[str, str]:
    file_values = read_env_file(configured_env_file())
    return {key: os.environ.get(key) or file_values.get(key, "") for key in LLM_CONFIG_KEYS}


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
            "未配置本地 LLM 服务：请设置环境变量 QA_LLM_BASE_URL 和 QA_LLM_MODEL，"
            "或在项目根目录创建 qa_llm.env"
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
