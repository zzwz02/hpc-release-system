"""app_info.json parsing and diffing — ported from core.py:1673-1850, 1925-1938.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from app.domain.textutil import join_list, order_chips
from app.repositories.base import new_id


def walk_objects(value: Any, visitor: Callable[[dict[str, Any], list[str]], None], path: list[str] | None = None) -> None:
    path = path or []
    if isinstance(value, dict):
        visitor(value, path)
        for key, child in value.items():
            walk_objects(child, visitor, path + [str(key)])
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            walk_objects(child, visitor, path + [str(idx)])


def parse_app_info(raw: str | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(raw) if isinstance(raw, str) else raw
    x86_chips: set[str] = set()
    arm_chips: set[str] = set()
    python_labels: list[str] = []
    pytorch_labels: list[str] = []
    build_os_list: list[str] = []
    build_arch_list: list[str] = []
    build_targets: list[dict[str, Any]] = []
    test_targets: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []

    def _add_unique(target: list[str], value: Any) -> None:
        v = str(value or "").strip()
        if v and v not in target:
            target.append(v)

    for env, cfg in (data.get("app_build") or {}).items():
        if not isinstance(cfg, dict):
            continue
        arch = str(cfg.get("arch") or env)
        chips = cfg.get("supported_chip") if isinstance(cfg.get("supported_chip"), list) else []
        enabled = cfg.get("enabled") is not False
        if enabled:
            target = arm_chips if re.search(r"arm|aarch64", arch, re.I) else x86_chips
            target.update(str(chip).upper() for chip in chips)
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

    def visitor(node: dict[str, Any], path: list[str]) -> None:
        if "test_cmd" not in node:
            return
        if node.get("enabled") is False:
            return
        if str(node.get("test_period", "")).strip().lower() == "weekly":
            return
        if node.get("ignore_release"):
            return
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
                "container_args": test["container_args"],
                "image_target": test["image_target"],
            }
        )

    walk_objects(data.get("app_test") or {}, visitor)
    return {
        "app_name": data.get("app_name", ""),
        "app_version": data.get("app_version", ""),
        "x86_chips": order_chips(x86_chips),
        "arm_chips": order_chips(arm_chips),
        "python_labels": python_labels,
        "pytorch_labels": pytorch_labels,
        "build_os": build_os_list,
        "build_arches": build_arch_list,
        "build_targets": build_targets,
        "test_targets": test_targets,
        "tests": tests,
        "raw": data,
    }


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
    """Describe QA-scope-expanding additions: new chips or new test paths."""
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
    return additions
