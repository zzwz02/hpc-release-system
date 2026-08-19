"""CICD App configuration schema, defaults, and normalization."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.shared_metadata import mapping_section

_CONFIG = mapping_section("cicd_config")


def _mapping(name: str) -> dict[str, Any]:
    value = _CONFIG.get(name)
    if not isinstance(value, dict) or not value:
        raise RuntimeError(f"cicd_config must define non-empty {name}")
    return value


_REPO_TYPE_METADATA = _mapping("repo_types")
_COMMUNITY_ARTIFACT_METADATA = _mapping("community_artifacts")
_APP_FIELD_METADATA = _mapping("app_fields")

CICD_REPO_TYPE_OPTIONS = tuple(
    repo_type
    for repo_type, _ in sorted(
        _REPO_TYPE_METADATA.items(),
        key=lambda item: int(item[1].get("order", -1)),
    )
)
_DEFAULT_REPO_TYPES = [
    repo_type
    for repo_type, metadata in _REPO_TYPE_METADATA.items()
    if metadata.get("default") is True
]
if len(_DEFAULT_REPO_TYPES) != 1:
    raise RuntimeError("cicd_config.repo_types must define exactly one default")
CICD_REPO_TYPE_DEFAULT = _DEFAULT_REPO_TYPES[0]
CICD_TEST_TIMEOUT_DEFAULT = int(_CONFIG["default_test_timeout"])

CICD_COMMUNITY_ARTIFACT_OPTIONS = tuple(
    artifact
    for artifact, _ in sorted(
        _COMMUNITY_ARTIFACT_METADATA.items(),
        key=lambda item: int(item[1].get("order", -1)),
    )
)
_COMMUNITY_ARTIFACT_ALIASES = {
    str(alias).strip().lower(): artifact
    for artifact, metadata in _COMMUNITY_ARTIFACT_METADATA.items()
    for alias in [artifact, *(metadata.get("aliases") or [])]
}

CICD_APP_CONFIG_FIELDS: frozenset[str] = frozenset(_APP_FIELD_METADATA)
CICD_APP_CONFIG_LABELS: dict[str, str] = {
    field: str(metadata["label"])
    for field, metadata in _APP_FIELD_METADATA.items()
}
CICD_APP_FIELD_TO_PAYLOAD_FIELD: dict[str, str] = {
    field: str(metadata["payload_field"])
    for field, metadata in _APP_FIELD_METADATA.items()
}
CICD_PAYLOAD_FIELD_TO_APP_FIELD: dict[str, str] = {
    payload_field: app_field
    for app_field, payload_field in CICD_APP_FIELD_TO_PAYLOAD_FIELD.items()
}
CICD_PAYLOAD_CONFIG_FIELDS: frozenset[str] = frozenset(CICD_PAYLOAD_FIELD_TO_APP_FIELD)
CICD_PAYLOAD_CONFIG_LABELS: dict[str, str] = {
    CICD_APP_FIELD_TO_PAYLOAD_FIELD[field]: label
    for field, label in CICD_APP_CONFIG_LABELS.items()
}


def normalize_repo_type(value: object) -> str:
    repo_type = str(value or "").strip()
    return repo_type if repo_type in _REPO_TYPE_METADATA else CICD_REPO_TYPE_DEFAULT


def normalize_test_timeout(value: object) -> int:
    try:
        timeout = int(str(value or "").strip() or CICD_TEST_TIMEOUT_DEFAULT)
    except (TypeError, ValueError):
        timeout = CICD_TEST_TIMEOUT_DEFAULT
    return timeout if timeout > 0 else CICD_TEST_TIMEOUT_DEFAULT


def normalize_community_artifacts(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items: Iterable[object] = value.replace("，", ",").split(",")
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        raw_items = value
    else:
        raw_items = []
    artifacts: list[str] = []
    for item in raw_items:
        normalized = _COMMUNITY_ARTIFACT_ALIASES.get(str(item).strip().lower())
        if normalized and normalized not in artifacts:
            artifacts.append(normalized)
    return artifacts


def community_artifacts_app_value(value: object) -> str:
    return ", ".join(normalize_community_artifacts(value))


def normalize_app_config(fields: Mapping[str, object] | None) -> dict[str, str]:
    """Normalize supported App storage fields and discard unknown keys."""
    normalized: dict[str, str] = {}
    for field, value in (fields or {}).items():
        if field not in CICD_APP_CONFIG_FIELDS:
            continue
        if field == CICD_PAYLOAD_FIELD_TO_APP_FIELD["repo_type"]:
            normalized[field] = normalize_repo_type(value)
        elif field == CICD_PAYLOAD_FIELD_TO_APP_FIELD["community_artifact"]:
            normalized[field] = community_artifacts_app_value(value)
        elif field == CICD_PAYLOAD_FIELD_TO_APP_FIELD["test_timeout"]:
            normalized[field] = str(normalize_test_timeout(value))
        else:
            normalized[field] = str(value or "").strip()
    return normalized
