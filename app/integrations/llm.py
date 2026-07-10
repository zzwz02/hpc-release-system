"""LLM integration — QA analysis via the local OpenAI-compatible endpoint.

Background QA jobs call this synchronous adapter from a worker thread.
"""
from __future__ import annotations

from collections.abc import Callable

from release_system.llm import chat_json as _legacy_chat_json


def chat_json(
    system: str,
    user: str,
    *,
    progress: Callable[[int], None] | None = None,
) -> str:
    """Call the configured JSON chat endpoint through the app integration."""
    return _legacy_chat_json(system, user, progress=progress)
