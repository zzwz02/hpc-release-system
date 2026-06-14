"""LLM integration — QA analysis via local OpenAI-compatible endpoint.

Thin wrapper around release_system/llm.py, which is used by background
threads (not the event loop).  The underlying chat_json function is blocking;
callers should invoke this from a daemon thread, not from an async handler.
"""
from __future__ import annotations


def analyze_qa_log(
    log_content: str,
    *,
    llm_config: dict,
) -> str:
    """Run LLM analysis on a QA log and return the result text.

    This is a thin wrapper kept for interface compatibility.  The actual
    analysis is driven by release_system.core.qa_analyze_log (which calls
    release_system.llm.chat_json internally).  Direct callers that need
    more control (progress callbacks, retry logic) should call
    release_system.core.qa_analyze_log directly.
    """
    from release_system.core import _QA_ANALYSIS_SYSTEM  # type: ignore[attr-defined]
    from release_system.llm import chat_json

    return chat_json(_QA_ANALYSIS_SYSTEM, log_content)
