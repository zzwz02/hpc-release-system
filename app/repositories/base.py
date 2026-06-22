"""Base repository utilities — shared helpers for all repository modules.

Convention: repositories are module-level pure functions fn(conn, ...).
No business rules, phase gating, or audit writes here — those live in the
service layer.  Only SQL and data-shape helpers belong in this package.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# JSON helpers — mirror core.py loads_json / dumps_json
# ---------------------------------------------------------------------------

def loads_json(value: str | None, default: Any) -> Any:
    """Deserialize a JSON column; return *default* if value is empty/None."""
    if not value:
        return default
    return json.loads(value)


def dumps_json(value: Any) -> str:
    """Serialize a value to a compact, sorted JSON string."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------

def new_id(prefix: str) -> str:
    """Generate a short random ID like ``apps_abc123``."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
