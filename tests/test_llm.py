"""The shared LLM entry (app/integrations/llm.py): config, streaming, JSON replies."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

import pytest

from app.config import settings
from app.integrations import llm


@pytest.fixture(autouse=True)
def conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "release_system.conf"
    path.write_text("[llm]\nLLM_BASE_URL = http://local-llm/v1\nLLM_MODEL = m\nLLM_API_KEY = k\n", encoding="utf-8")
    monkeypatch.setattr(settings, "runtime_conf_path", path)
    return path


def _chunk(content=None, reasoning=None, usage=None, choices=True):
    delta = types.SimpleNamespace(content=content, reasoning_content=reasoning)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=delta)] if choices else [],
        usage=usage,
    )


def _run(monkeypatch, create):
    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert (kwargs["base_url"], kwargs["api_key"]) == ("http://local-llm/v1", "k")
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


def test_config_comes_only_from_the_llm_section(conf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert llm.require_config() == {"LLM_BASE_URL": "http://local-llm/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m"}

    conf.write_text("[llm]\nLLM_BASE_URL = http://local-llm/v1\n", encoding="utf-8")
    with pytest.raises(llm.LLMConfigError, match=r"\[llm\] 中填写 LLM_BASE_URL 和 LLM_MODEL"):
        llm.require_config()


def test_the_old_section_name_says_what_to_rename(conf: Path) -> None:
    conf.write_text("[qa_llm]\nQA_LLM_BASE_URL = http://local-llm/v1\nQA_LLM_MODEL = m\n", encoding="utf-8")
    with pytest.raises(llm.LLMConfigError, match=r"\[qa_llm\] 已改名为 \[llm\]"):
        llm.chat_json("s", "u")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"items": []}', {"items": []}),
        ('好的：\n```json\n{"a": 1}\n```', {"a": 1}),
        ('结果如下 {"a": {"b": 2}} 以上', {"a": {"b": 2}}),
    ],
)
def test_parse_json_object_tolerates_text_around_the_object(reply: str, expected: dict) -> None:
    assert llm.parse_json_object(reply) == expected


@pytest.mark.parametrize("reply", ["没有 JSON", "[1, 2]", "{broken"])
def test_parse_json_object_rejects_anything_else(reply: str) -> None:
    with pytest.raises(llm.LLMCallError):
        llm.parse_json_object(reply)


def test_ask_json_retries_a_failed_or_unparseable_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    replies = iter([RuntimeError("timeout"), "不是 JSON", '{"ok": true}'])
    attempts: list[tuple[int, int]] = []
    retries: list[str] = []

    def call(system: str, user: str) -> str:
        assert (system, user) == ("s", "u")
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    result = llm.ask_json(
        "s", "u", attempts=3, call=call,
        on_attempt=lambda n, total: attempts.append((n, total)),
        on_retry=lambda exc: retries.append(str(exc)),
    )

    assert result == {"ok": True}
    assert attempts == [(1, 3), (2, 3), (3, 3)]
    assert retries[0] == "timeout" and retries[1].startswith("LLM 输出不是 JSON")


def test_ask_json_gives_up_after_the_last_attempt_and_never_retries_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm.time, "sleep", lambda _seconds: None)
    calls: list[int] = []

    def always_text(_system: str, _user: str) -> str:
        calls.append(1)
        return "还是文本"

    with pytest.raises(llm.LLMCallError, match="不是 JSON"):
        llm.ask_json("s", "u", attempts=2, call=always_text)
    assert len(calls) == 2

    def unconfigured(_system: str, _user: str) -> str:
        calls.append(1)
        raise llm.LLMConfigError("未配置 LLM")

    with pytest.raises(llm.LLMConfigError):
        llm.ask_json("s", "u", attempts=3, call=unconfigured)
    assert len(calls) == 3


def test_meter_adds_up_streamed_chunks_and_reported_usage() -> None:
    meter = llm.LlmMeter()
    heard: list[dict] = []

    def streaming_call(system: str, prompt: str, progress) -> str:
        progress({"content_chunks": 0, "reasoning_chunks": 2, "usage": None})
        progress({"content_chunks": 3, "reasoning_chunks": 2, "usage": None})
        progress({"content_chunks": 5, "reasoning_chunks": 2, "usage": {"prompt_tokens": 40, "completion_tokens": 6}})
        return "{}"

    counted = meter.wrap(streaming_call, streaming=True, listener=heard.append)
    counted("sys", "prompt")
    counted("sys", "p")

    assert meter.snapshot() == {
        "calls": 2, "finished": 2, "sent_chars": 13, "received": 10, "reasoning": 4,
        "prompt_tokens": 80, "completion_tokens": 12, "usage_calls": 2, "sent_tokens_est": 4,
    }
    # The listener hears running totals, not one call's own counts.
    assert [entry["received"] for entry in heard] == [0, 3, 5, 5, 8, 10]


def test_ask_json_counts_streamed_calls_on_the_given_meter(monkeypatch) -> None:
    def create(**_kwargs):
        return iter([
            _chunk(reasoning="think"),
            _chunk(content='{"ok": '),
            _chunk(content="true}"),
            _chunk(choices=False, usage=types.SimpleNamespace(prompt_tokens=120, completion_tokens=7)),
        ])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    meter = llm.LlmMeter()
    heard: list[dict] = []
    replies: list[bool] = []
    with mock.patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeOpenAI)}):
        result = llm.ask_json("s", "u", meter=meter, progress=heard.append, on_reply=lambda: replies.append(True))

    assert result == {"ok": True}
    assert replies == [True]
    totals = meter.snapshot()
    assert (totals["calls"], totals["received"], totals["reasoning"]) == (1, 2, 1)
    assert (totals["prompt_tokens"], totals["completion_tokens"], totals["usage_calls"]) == (120, 7, 1)
    assert heard[-1]["received"] == 2
