"""app_info.json parsing and diffing — ported from core.py:1673-1850, 1925-1938.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import json
from typing import Any

from app.domain.textutil import join_list, order_chips
from app.domain.test_platforms import (
    build_platforms, chip_summary, is_enabled_value, public_platform, release_tests,
    test_platforms, walk_objects,
)
from app.repositories.base import new_id


def parse_app_info(raw: str | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    python_labels: list[str] = []
    pytorch_labels: list[str] = []
    build_os_list: list[str] = []
    build_arch_list: list[str] = []
    build_targets: list[dict[str, Any]] = []
    test_targets: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    enabled_platforms = build_platforms(data)

    def _add_unique(target: list[str], value: Any) -> None:
        v = str(value or "").strip()
        if v and v not in target:
            target.append(v)

    for env, cfg in (data.get("app_build") or {}).items():
        if not isinstance(cfg, dict):
            continue
        arch = str(cfg.get("arch") or env)
        chips = cfg.get("supported_chip") if isinstance(cfg.get("supported_chip"), list) else []
        enabled = is_enabled_value(cfg.get("enabled"))
        if enabled:
            _add_unique(python_labels, cfg.get("python_label"))
            _add_unique(pytorch_labels, cfg.get("pytorch_label"))
            _add_unique(build_os_list, cfg.get("os"))
            _add_unique(build_arch_list, cfg.get("arch"))
        build_targets.append({
            "path": env,
            "arch": arch,
            "chips": chips,
            "enabled": enabled,
            "build_target": cfg.get("build_target", ""),
            "python_label": str(cfg.get("python_label") or "").strip(),
            "pytorch_label": str(cfg.get("pytorch_label") or "").strip(),
            "os": str(cfg.get("os") or "").strip(),
        })

    for test_path, node in release_tests(data):
        path = test_path.split(".")
        supported = node.get("supported_chip") or {}
        if isinstance(supported, dict):
            chips = list(supported.keys())
            arch_list = sorted({str(v) for values in supported.values() for v in (values if isinstance(values, list) else [values])})
        elif isinstance(supported, list):
            chips = [str(v) for v in supported]
            arch_list = []
        else:
            chips = []
            arch_list = []
        test = {
            "id": ".".join(path),
            "name": path[-1] if path else "test",
            "path": ".".join(path),
            "command": str(node.get("test_cmd") or "").strip(),
            "supported_chips": chips,
            "platforms": [public_platform(platform) for platform in test_platforms(data, node, platforms=enabled_platforms)],
            "arch_list": arch_list,
            "enabled": node.get("enabled") is not False,
            "container_args": node.get("container_args", ""),
            "image_target": node.get("img_target", ""),
        }
        tests.append(test)
        test_targets.append(
            {
                "path": test["path"],
                "enabled": test["enabled"],
                "command": test["command"],
                "supported_chips": chips,
                "arch_list": arch_list,
                "platforms": test["platforms"],
                "container_args": test["container_args"],
                "image_target": test["image_target"],
            }
        )

    return {
        "app_name": data.get("app_name", ""),
        "app_version": data.get("app_version", ""),
        **chip_summary(enabled_platforms),
        "python_labels": python_labels,
        "pytorch_labels": pytorch_labels,
        "build_os": build_os_list,
        "build_arches": build_arch_list,
        "build_targets": build_targets,
        "test_targets": test_targets,
        "tests": tests,
        "community_artifacts": derive_community_artifacts(data),
        "raw": data,
    }


def _contains_ai4s(value: Any) -> bool:
    values = value if isinstance(value, (list, tuple, set)) else [value]
    return any(str(item or "").strip().lower() == "ai4s" for item in values)


def derive_community_artifacts(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Derive developer-community artifacts from enabled app_build aliases."""
    image_aliases: list[str] = []
    pkg_aliases: list[str] = []
    app_build = (raw or {}).get("app_build") or {}
    build_items = app_build.items() if isinstance(app_build, dict) else []
    for alias, cfg in build_items:
        if not isinstance(cfg, dict) or not is_enabled_value(cfg.get("enabled")):
            continue
        pub = cfg.get("pub") or {}
        if not isinstance(pub, dict):
            continue
        alias_text = str(alias)
        if _contains_ai4s(pub.get("img")) and alias_text not in image_aliases:
            image_aliases.append(alias_text)
        if _contains_ai4s(pub.get("pkg")) and alias_text not in pkg_aliases:
            pkg_aliases.append(alias_text)

    image_count = len(image_aliases)
    pkg_count = len(pkg_aliases)
    parts: list[str] = []
    if image_count:
        parts.append(f"镜像：{image_count}")
    if pkg_count:
        parts.append(f"二进制包：{pkg_count}")
    artifact_types: list[str] = []
    if image_count:
        artifact_types.append("image")
    if pkg_count:
        artifact_types.append("pkg")
    return {
        "release_status": "，".join(parts),
        "image_count": image_count,
        "pkg_count": pkg_count,
        "image_aliases": image_aliases,
        "pkg_aliases": pkg_aliases,
        "artifact_types": artifact_types,
    }


def community_fields_from_app_info(
    parsed_or_raw: dict[str, Any],
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build snapshot.community fields from app_info, preserving user notes."""
    raw = parsed_or_raw.get("raw") if isinstance(parsed_or_raw, dict) else {}
    if not isinstance(raw, dict):
        raw = parsed_or_raw if isinstance(parsed_or_raw, dict) else {}
    derived = derive_community_artifacts(raw)
    has_artifacts = derived["image_count"] > 0 or derived["pkg_count"] > 0
    keep_pkg_notes = derived["pkg_count"] > 0
    existing = existing or {}
    return {
        "release_status": derived["release_status"],
        "menu_level1": str(existing.get("menu_level1") or "") if has_artifacts else "",
        "doc_url": str(existing.get("doc_url") or "") if has_artifacts else "",
        "description": str(existing.get("description") or "") if has_artifacts else "",
        "python_version": str(existing.get("python_version") or "") if keep_pkg_notes else "",
        "framework_version": str(existing.get("framework_version") or "") if keep_pkg_notes else "",
        "image_count": derived["image_count"],
        "pkg_count": derived["pkg_count"],
        "image_aliases": derived["image_aliases"],
        "pkg_aliases": derived["pkg_aliases"],
        "derived_from_app_info": True,
    }


def community_artifacts_app_value(parsed_or_raw: dict[str, Any]) -> str:
    raw = parsed_or_raw.get("raw") if isinstance(parsed_or_raw, dict) else {}
    if not isinstance(raw, dict):
        raw = parsed_or_raw if isinstance(parsed_or_raw, dict) else {}
    return ", ".join(derive_community_artifacts(raw)["artifact_types"])


def diff_app_info(old: dict[str, Any] | None, new: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []

    def add(diff_type: str, field: str, old_value: Any, new_value: Any, qa_impact: bool = True) -> None:
        if old_value != new_value:
            diffs.append({"id": new_id("diff"), "type": diff_type, "field": field, "old_value": old_value, "new_value": new_value, "qa_impact": qa_impact})

    old = old or {}
    add("版本变化", "app_version", old.get("app_version", ""), new.get("app_version", ""))
    add("X86芯片变化", "x86_chips", old.get("x86_chips", []), new.get("x86_chips", []))
    add("ARM芯片变化", "arm_chips", old.get("arm_chips", []), new.get("arm_chips", []))
    add("Python label 变化", "python_labels", old.get("python_labels", []), new.get("python_labels", []))
    add("PyTorch label 变化", "pytorch_labels", old.get("pytorch_labels", []), new.get("pytorch_labels", []))
    add("OS 变化", "build_os", old.get("build_os", []), new.get("build_os", []))
    add("Arch 变化", "build_arches", old.get("build_arches", []), new.get("build_arches", []))
    add(
        "Build target变化",
        "build_targets",
        [f"{x.get('path')}:{x.get('enabled')}:{x.get('build_target')}" for x in old.get("build_targets", [])],
        [f"{x.get('path')}:{x.get('enabled')}:{x.get('build_target')}" for x in new.get("build_targets", [])],
    )
    add("Test target变化", "test_targets", old.get("test_targets", []), new.get("test_targets", []))
    old_tests = {t["path"]: t["command"] for t in old.get("tests", [])}
    new_tests = {t["path"]: t["command"] for t in new.get("tests", [])}
    for path, cmd in new_tests.items():
        if path not in old_tests:
            add("test_cmd新增", path, "", cmd)
        elif old_tests[path] != cmd:
            add("test_cmd修改", path, old_tests[path], cmd)
    for path, cmd in old_tests.items():
        if path not in new_tests:
            add("test_cmd删除", path, cmd, "")
    return diffs


def ensure_test_docs(snapshot: dict[str, Any], parsed: dict[str, Any], diffs: list[dict[str, Any]]) -> None:
    snapshot.setdefault("test_docs", [])
    docs_by_path = {doc["path"]: doc for doc in snapshot["test_docs"]}
    current_paths = set()
    for test in parsed.get("tests", []):
        current_paths.add(test["path"])
        doc = docs_by_path.get(test["path"])
        if not doc:
            snapshot["test_docs"].append(
                {
                    "id": new_id("testdoc"),
                    "path": test["path"],
                    "name": test["name"],
                    "command": test["command"],
                    "dataset": "",
                    "content": "",
                    "preconditions": "",
                    "result_view": "",
                    "pass_criteria": "",
                    "coverage": join_list(test.get("supported_chips", [])),
                    "owner_added": False,
                    "obsolete": False,
                }
            )
        else:
            doc["command"] = test["command"]
            doc["obsolete"] = False
    for doc in snapshot["test_docs"]:
        if not doc.get("owner_added") and doc["path"] not in current_paths:
            doc["obsolete"] = True


def qa_scope_additions(old_parsed: dict[str, Any], new_parsed: dict[str, Any]) -> list[str]:
    """Describe how *new_parsed* widens QA scope relative to *old_parsed*.

    Widening = a new chip, a new test, a new chip on an existing test, or a
    new enabled build (OS/arch).  Removals and command edits are not widening.
    """
    additions: list[str] = []
    old_chips = set(old_parsed.get("x86_chips", [])) | set(old_parsed.get("arm_chips", []))
    new_chips = set(new_parsed.get("x86_chips", [])) | set(new_parsed.get("arm_chips", []))
    added_chips = sorted(new_chips - old_chips)
    if added_chips:
        additions.append("新增芯片 " + ", ".join(added_chips))
    old_paths = {test.get("path") for test in old_parsed.get("tests", [])}
    new_paths = {test.get("path") for test in new_parsed.get("tests", [])}
    added_paths = sorted(path for path in new_paths - old_paths if path)
    if added_paths:
        additions.append("新增测试 " + ", ".join(added_paths))

    def test_chips_by_path(parsed: dict[str, Any]) -> dict[str, set[str]]:
        chips_by_path: dict[str, set[str]] = {}
        for test in parsed.get("tests", []):
            path = str(test.get("path") or "").strip()
            if not path:
                continue
            normalized_chips = {
                str(chip).strip().upper()
                for chip in (test.get("supported_chips") or [])
                if str(chip).strip()
            }
            chips_by_path.setdefault(path, set()).update(normalized_chips)
        return chips_by_path

    def builds(parsed: dict[str, Any]) -> set[str]:
        return {
            f"{target.get('os') or ''}/{target.get('arch') or ''}".strip("/")
            or str(target.get("path") or "")
            for target in parsed.get("build_targets", [])
            if target.get("enabled")
        }

    added_builds = sorted(build for build in builds(new_parsed) - builds(old_parsed) if build)
    if added_builds:
        additions.append("新增构建 " + ", ".join(added_builds))

    old_test_chips = test_chips_by_path(old_parsed)
    new_test_chips = test_chips_by_path(new_parsed)
    for path in sorted(old_test_chips.keys() & new_test_chips.keys()):
        added_test_chips = sorted(new_test_chips[path] - old_test_chips[path])
        if added_test_chips:
            additions.append(
                f"测试 {path} 新增芯片 " + ", ".join(added_test_chips)
            )

    def platform_chips(parsed: dict[str, Any]) -> dict[str, set[tuple[str, ...]]]:
        return {
            test["path"]: {
                (platform["build"], platform["os"], platform["arch"], platform["python_label"],
                 platform["pytorch_label"], target, chip)
                for platform in test.get("platforms", [])
                for target in platform.get("image_targets", []) for chip in platform["chips"]
            }
            for test in parsed.get("tests", [])
        }

    old_platforms, new_platforms = platform_chips(old_parsed), platform_chips(new_parsed)
    for path in sorted(old_platforms.keys() & new_platforms.keys()):
        for build, os, arch, python, pytorch, target, chip in sorted(new_platforms[path] - old_platforms[path]):
            # Existing new-chip/build notices already describe these additions.
            if chip not in old_test_chips.get(path, set()) or f"{os}/{arch}" in added_builds:
                continue
            labels = " / ".join(value for value in (build, os, arch, python, pytorch, target) if value)
            additions.append(f"测试 {path} 新增平台芯片 {labels}: {chip}")
    return additions


# ---------------------------------------------------------------------------
# Monthly model helpers
# ---------------------------------------------------------------------------

#: app_info.json field listing the components an App depends on.
DEPENDENCY_FIELD = "app_dependency"


def canonical_json(raw: dict[str, Any]) -> str:
    """Stable text form of an app_info document (used for storage and hashing)."""
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(raw: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()


def declared_dependencies(raw: dict[str, Any]) -> list[str] | None:
    """Component names from ``app_dependency``; None when the field is absent.

    The field is a list of component names, e.g. ``["mcpy", "sdk"]``.  A single
    string is accepted as one component; anything else is ignored.
    """
    if not isinstance(raw, dict) or DEPENDENCY_FIELD not in raw:
        return None
    value = raw.get(DEPENDENCY_FIELD)
    items = value if isinstance(value, list) else [value]
    seen: list[str] = []
    for item in items:
        name = str(item or "").strip()
        if name and not isinstance(item, (dict, list)) and name not in seen:
            seen.append(name)
    return seen


def scope_tests(parsed: dict[str, Any], excluded: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
    """Tests in a parsed app_info that remain in scope after Owner exclusions."""
    skip = set(excluded or [])
    return [test for test in parsed.get("tests", []) if test.get("path") not in skip]
