"""Versioned publication evidence; command generation happens only when capturing it."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domain import app_info, delivery_images
from app.domain.shared_metadata import mapping_section

# Increase only when the persisted document shape changes; readers dispatch by this version.
SCHEMA_VERSION = 1
_SOURCE_METADATA = mapping_section("publication_manifest_sources")


def source_label(source: str) -> str:
    return str(_SOURCE_METADATA.get(source, {}).get("label") or source)


def capture(*, app: dict, app_name: str, release: dict, entry: dict, version: dict,
            images: list[dict], published_at: str, published_by: str) -> dict[str, Any]:
    raw = version["raw"]
    parsed = app_info.parse_app_info(raw)
    declared = app_info.declared_dependencies(raw)
    dependencies = list(dict.fromkeys([*(declared or []), *app_info.python_pytorch_dependencies(raw)]))
    excluded = entry.get("excluded_tests") or []
    return deepcopy({
        "schema_version": SCHEMA_VERSION,
        "published_at": published_at,
        "published_by": published_by,
        "app": {"id": app["id"], "name": app_name, "owners": app.get("owners") or [],
                "git_url": app["git_url"], "git_branch": app["git_branch"]},
        "release": {key: release[key] for key in ("id", "name", "release_date", "environment", "base_images")},
        "entry": entry,
        "app_info": {"id": version["id"], "commit_id": version["commit_id"],
                     "content_hash": version["content_hash"], "raw": raw,
                     "x86_chips": parsed["x86_chips"], "arm_chips": parsed["arm_chips"]},
        "dependencies": dependencies,
        "declared_dependencies": declared,
        "scope_pending": declared is None or not dependencies,
        "builds": delivery_images.builds(raw),
        "scope": {"excluded_tests": list(excluded), "tests": app_info.scope_tests(parsed, excluded)},
        "images": images,
        "commands": delivery_images.published_commands(raw, images, set(excluded)),
        "qa": {"level": "app", "status": entry["qa_status"], "note": entry["qa_note"]},
    })


def validate(manifest: Any, official: dict) -> None:
    """A present but unreadable archive must never silently fall back to reconstruction."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("发布测试清单版本不支持，无法读取已封存记录")
    required_fields = {
        "app": ("id", "name", "owners", "git_url", "git_branch"),
        "release": ("id", "name", "release_date", "environment", "base_images"),
        "entry": ("id", "app_id", "target_version", "qa_status", "qa_note", "excluded_tests"),
        "app_info": ("id", "commit_id", "raw", "x86_chips", "arm_chips"),
        "scope": ("tests", "excluded_tests"),
        "qa": ("level", "status", "note"),
    }
    for key, fields in required_fields.items():
        if not isinstance(manifest.get(key), dict) or any(field not in manifest[key] for field in fields):
            raise ValueError(f"发布测试清单缺少有效的 {key} 字段")
    try:
        if manifest["app"]["id"] != official["app_id"] or manifest["release"]["id"] != official["release_id"]:
            raise ValueError("发布测试清单归属不符")
        if manifest["official_version_id"] != official["id"]:
            raise ValueError("发布测试清单记录 ID 不符")
        if not all(isinstance(manifest[key], list) for key in ("images", "commands", "dependencies", "builds")):
            raise ValueError("发布测试清单列表格式无效")
        for key in ("entry", "app_info", "scope", "qa"):
            if not isinstance(manifest[key], dict):
                raise ValueError("发布测试清单字段格式无效")
        if not isinstance(manifest["scope"]["tests"], list) or any(
            not isinstance(row, dict) or not isinstance(row.get("command"), str)
            for row in manifest["commands"]
        ):
            raise ValueError("发布测试清单命令格式无效")
        if not isinstance(manifest["scope_pending"], bool):
            raise ValueError("发布测试清单依赖范围格式无效")
    except KeyError as exc:
        raise ValueError("发布测试清单缺少必要字段") from exc
