"""LLM integration — QA analysis via local OpenAI-compatible endpoint.

Background threads use this (not the event loop).

# TODO Phase 2 — wrap release_system/llm.py
"""
from __future__ import annotations


def analyze_qa_log(
    log_content: str,
    *,
    llm_config: dict,
) -> str:
    """Run LLM analysis on a QA log and return the result text.

    # TODO Phase 2
    """
    raise NotImplementedError
