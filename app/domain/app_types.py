"""Standard App categories grouped by documentation target."""
from app.domain.shared_metadata import mapping_section


APP_TYPES_BY_DOC_TARGET: dict[str, tuple[str, ...]] = {}
for _target, _types in mapping_section("app_types").items():
    if not isinstance(_types, list) or not _types or any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in _types
    ):
        raise RuntimeError(f"Invalid App categories for {_target}")
    APP_TYPES_BY_DOC_TARGET[_target] = tuple(_types)
if set(APP_TYPES_BY_DOC_TARGET) != set(mapping_section("doc_targets")):
    raise RuntimeError("App categories must cover every documentation target")


def list_app_types() -> list[str]:
    return [value for values in APP_TYPES_BY_DOC_TARGET.values() for value in values]


if len(list_app_types()) != len(set(list_app_types())):
    raise RuntimeError("Standard App categories must be unique")
