from __future__ import annotations

import sys
import types
from unittest import mock

from app.integrations import llm


def _chunk(content=None, reasoning=None, usage=None, choices=True):
    delta = types.SimpleNamespace(content=content, reasoning_content=reasoning)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=delta)] if choices else [],
        usage=usage,
    )


def _run(monkeypatch, create):
    monkeypatch.setenv("QA_LLM_BASE_URL", "http://local-llm/v1")
    monkeypatch.setenv("QA_LLM_MODEL", "m")
    monkeypatch.setenv("QA_LLM_API_KEY", "k")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    stats: list[dict] = []
    with mock.patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
        result = llm.chat_json("s", "u", progress=stats.append)
    return result, stats


def test_counts_reasoning_and_reports_usage(monkeypatch):
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        return iter([
            _chunk(reasoning="think"),
            _chunk(content='{"ok"'),
            _chunk(content=": true}"),
            _chunk(choices=False, usage=types.SimpleNamespace(prompt_tokens=120, completion_tokens=7)),
        ])

    result, stats = _run(monkeypatch, create)
    assert result == '{"ok": true}'
    assert calls[0]["stream_options"] == {"include_usage": True}
    assert [(s["content_chunks"], s["reasoning_chunks"]) for s in stats] == [(0, 1), (1, 1), (2, 1), (2, 1)]
    assert stats[-1]["usage"] == {"prompt_tokens": 120, "completion_tokens": 7}


def test_retries_without_stream_options_when_rejected(monkeypatch):
    calls: list[dict] = []

    class Rejected(Exception):
        status_code = 400

    def create(**kwargs):
        calls.append(kwargs)
        if "stream_options" in kwargs:
            raise Rejected("unknown field stream_options")
        return iter([_chunk(content="{}")])

    result, stats = _run(monkeypatch, create)
    assert result == "{}"
    assert len(calls) == 2 and "stream_options" not in calls[1]
    assert stats[-1]["usage"] is None
