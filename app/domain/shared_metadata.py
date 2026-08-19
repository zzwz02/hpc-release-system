"""Validated access to static domain metadata shared with the React client."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_METADATA_PATH = Path(__file__).resolve().parents[2] / "shared" / "domain_metadata.json"


def _load_metadata() -> dict[str, Any]:
    raw = json.loads(_METADATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("shared/domain_metadata.json must contain a JSON object")
    return raw


DOMAIN_METADATA = _load_metadata()


def mapping_section(name: str) -> dict[str, Any]:
    """Return a required object section, failing fast on malformed metadata."""
    section = DOMAIN_METADATA.get(name)
    if not isinstance(section, dict) or not section:
        raise RuntimeError(f"shared/domain_metadata.json must define non-empty {name}")
    return section
