"""LLM integration — QA analysis via local OpenAI-compatible endpoint.

Background threads use this (not the event loop).

The actual LLM call is driven by release_system.core.qa_analyze_log, which
calls release_system.llm.chat_json internally.  This module is a placeholder
for any future app-layer LLM helpers; currently no public API is needed here.
"""
from __future__ import annotations
