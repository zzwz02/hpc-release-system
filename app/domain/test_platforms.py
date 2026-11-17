"""Join test chip declarations to their exact enabled app_build platforms."""
from __future__ import annotations

import itertools
import re
from collections.abc import Callable
from typing import Any

from app.domain.textutil import order_chips


def is_enabled_value(value: Any) -> bool:
    return value is not False and str(value).strip().lower() != "false"


def normalize_arch(value: Any) -> str:
    arch = str(value or "").strip().lower()
    if arch in ("arm", "arm64", "aarch64"):
        return "arm64"
    if arch in ("x86", "x86_64", "amd64"):
        return "amd64"
    return arch


def normalized_chips(values: list[str]) -> list[str]:
    return order_chips(value.upper() for value in order_chips(values))


def split_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    values = value if isinstance(value, (list, tuple, set)) else re.split(r"[,，、;；/]+", str(value))
    return list(dict.fromkeys(str(item or "").strip() for item in values if str(item or "").strip()))


def walk_objects(value: Any, visitor: Callable[[dict[str, Any], list[str]], None], path: list[str] | None = None) -> None:
    path = path or []
    if isinstance(value, dict):
        visitor(value, path)
        for key, child in value.items():
            walk_objects(child, visitor, path + [str(key)])
    elif isinstance(value, list):
        for index, child in enumerate(value):
            walk_objects(child, visitor, path + [str(index)])


def release_tests(raw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    tests = []

    def visit(node: dict[str, Any], path: list[str]) -> None:
        if ("test_cmd" in node and is_enabled_value(node.get("enabled"))
                and str(node.get("test_period") or "").strip().lower() != "weekly"
                and not node.get("ignore_release")):
            tests.append((".".join(path), node))

    walk_objects(raw.get("app_test") or {}, visit)
    return tests


def build_target_matches(cfg: dict[str, Any], target: str) -> bool:
    return target in ([item.lower() for item in split_values(cfg.get("build_target"))] or ["release"])


def _alias_platform(alias: str) -> tuple[str, str]:
    match = re.fullmatch(r"(.+)[_-](amd64|x86_64|x86|arm64|aarch64|arm)", alias, re.I)
    return (match[1], match[2]) if match else ("", "")


def build_alias_is_redundant(alias: str, os: str, arch: str) -> bool:
    if not alias:
        return True
    alias_os, alias_arch = _alias_platform(alias)
    return bool(alias_os) and alias_os.casefold() == os.casefold() and normalize_arch(alias_arch) == normalize_arch(arch)


def build_platforms(raw: dict[str, Any]) -> list[dict[str, Any]]:
    platforms = []
    for alias, cfg in (raw.get("app_build") or {}).items():
        if not isinstance(cfg, dict) or not is_enabled_value(cfg.get("enabled")):
            continue
        alias_os, alias_arch = _alias_platform(str(alias))
        defaults = {"os": alias_os, "arch": alias_arch}
        fields = ["os", "arch", "python_label", "pytorch_label"]
        options = [split_values(cfg.get(field) or raw.get(field) or defaults.get(field)) or [""] for field in fields]
        for values in itertools.product(*options):
            expanded = {**cfg, **dict(zip(fields, values))}
            platforms.append({
                "build": str(alias), "os": expanded["os"], "arch": normalize_arch(expanded["arch"]),
                "python_label": expanded["python_label"], "pytorch_label": expanded["pytorch_label"],
                "chips": normalized_chips(cfg.get("supported_chip") or []), "config": expanded,
            })
    return platforms


def _matches_reference(reference: Any, platform: dict[str, Any]) -> bool:
    if str(reference) == platform["build"]:
        return True
    # Older files sometimes use a bare architecture. Full build aliases never
    # fall back to architecture matching, which would mix different OS builds.
    return (str(reference).strip().lower() in ("amd64", "x86_64", "x86", "arm64", "aarch64", "arm")
            and normalize_arch(reference) == platform["arch"])


def test_platforms(raw: dict[str, Any], cfg: dict[str, Any], *, platforms: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    targets = [target.lower() for target in split_values(cfg.get("img_target"))] or ["release"]
    supported = cfg.get("supported_chip")
    result = []
    for platform in (build_platforms(raw) if platforms is None else platforms):
        build_cfg = platform["config"]
        image_targets = [target for target in targets if build_target_matches(build_cfg, target)]
        if not image_targets:
            continue
        if isinstance(supported, dict):
            requested = [chip for chip, refs in supported.items()
                         if any(_matches_reference(ref, platform) for ref in (refs if isinstance(refs, list) else [refs]))]
        elif isinstance(supported, list):
            requested = supported
        else:
            requested = platform["chips"]
        chips = normalized_chips(requested)
        if isinstance(build_cfg.get("supported_chip"), list):
            chips = [chip for chip in chips if chip in platform["chips"]]
        if not chips and (isinstance(supported, (dict, list)) or isinstance(build_cfg.get("supported_chip"), list)):
            continue
        result.append({**platform, "chips": chips, "image_targets": image_targets})
    return result


def public_platform(platform: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in platform.items() if key != "config"}


def chip_summary(platforms: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {key: order_chips(chip for platform in platforms if platform["arch"] == arch for chip in platform["chips"])
            for key, arch in [("x86_chips", "amd64"), ("arm_chips", "arm64")]}
