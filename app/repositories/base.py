"""Base repository utilities.

Provides shared helpers for row-to-dict conversion, pagination (future),
and common query patterns.

# TODO Phase 1 — implement as needed
"""
from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)
