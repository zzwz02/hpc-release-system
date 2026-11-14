"""Shared image naming, platform selection and Docker command assembly."""
from __future__ import annotations

import itertools
import re
from typing import Any

from app.domain.test_platforms import (
    build_platforms, build_target_matches, normalize_arch, release_tests, split_values as split_image_values, test_platforms,
)


def denormalize_arch(value: Any) -> str:
    arch = normalize_arch(value)
    if arch == "arm64":
        return "arm"
    if arch == "amd64":
        return "x86"
    return arch


IMAGE_TARGET_SUFFIX = {
    "release": "maca",
    "dbg": "maca-dbg",
}


def image_tag_matches(pattern: str, tag: str, *, hpc_version: str | None = None) -> bool:
    """Match a generated tag, including the optional Jenkins build number."""
    version = re.escape(hpc_version) if hpc_version is not None else r"[^\s:/-]+"
    expected = re.escape(pattern).replace(re.escape("<hpc_version>"), version)
    return re.fullmatch(expected + r"(?:-\d+)?", tag) is not None


def image_field_options(raw: dict[str, Any], build_cfg: dict[str, Any], field: str) -> list[str]:
    if field == "sdk_version":
        return ["<hpc_version>"]
    if field in ("app_name", "app_version"):
        value = raw.get(field)
        if value in (None, ""):
            value = build_cfg.get(field)
    else:
        value = build_cfg.get(field)
        if value in (None, ""):
            value = raw.get(field)
    values = split_image_values(value)
    return values or [""]


def image_values_for_build(
    raw: dict[str, Any],
    build_cfg: dict[str, Any],
    image_target: str,
) -> list[tuple[str, str]]:
    suffix = IMAGE_TARGET_SUFFIX.get(image_target)
    if not suffix:
        return []
    name_fields = [
        image_field_options(raw, build_cfg, "app_name"),
        image_field_options(raw, build_cfg, "spec"),
        [suffix],
    ]
    app_versions = image_field_options(raw, build_cfg, "app_version")
    sdk_versions = image_field_options(raw, build_cfg, "sdk_version")
    pytorch_labels = image_field_options(raw, build_cfg, "pytorch_label")
    python_labels = image_field_options(raw, build_cfg, "python_label")
    os_values = image_field_options(raw, build_cfg, "os")
    arch_values = image_field_options(raw, build_cfg, "arch")

    images: list[tuple[str, str]] = []
    for name_parts in itertools.product(*name_fields):
        image_name = "-".join(part for part in name_parts if part)
        if not image_name:
            continue
        for app_version, sdk_version, pytorch_label, python_label, os_value, arch in itertools.product(
            app_versions, sdk_versions, pytorch_labels, python_labels, os_values, arch_values
        ):
            tag_parts = [app_version, sdk_version, pytorch_label, python_label, os_value, arch]
            tag = "-".join(part for part in tag_parts if part)
            image = f"{image_name}:{tag}" if tag else image_name
            images.append((denormalize_arch(arch), image))
    return images


def docker_cmd(test_cfg: dict[str, Any], docker_image: str | None = None) -> str:
    container_args = str(test_cfg.get("container_args") or "").strip()
    test_cmd = str(test_cfg.get("test_cmd") or "").strip()
    img_target = str(test_cfg.get("img_target") or "").strip().lower()
    if docker_image is not None:
        image = docker_image
    else:
        image = f"[docker_image_{img_target}]" if img_target else "[docker_image]"
    parts = ["docker run --pull always --rm -e MACA_PERF_DIR=/tmp"]
    if test_cfg.get("mount_dataset"):
        parts.append("-v /pde_hpc/dataset:/hpc_dataset:ro")
    if container_args:
        parts.append(container_args)
    parts.append(image)
    quoted_command = "'" + test_cmd.replace("'", "'\"'\"'") + "'"
    parts.append(f"sh -c {quoted_command}")
    return " ".join(parts)


def command_rows(raw: dict[str, Any], excluded: set[str] | None = None) -> list[dict[str, Any]]:
    rows = []
    platforms = build_platforms(raw)
    for path, cfg in release_tests(raw):
        if path in (excluded or set()):
            continue
        for platform in test_platforms(raw, cfg, platforms=platforms):
            for target in platform["image_targets"]:
                for _, image in image_values_for_build(raw, platform["config"], target):
                    rows.append({
                        **{key: value for key, value in platform.items() if key not in ("config", "image_targets")},
                        "test": path, "image_target": target, "image": image,
                        "command": docker_cmd(cfg, image),
                        "perf_golden": str(cfg.get("perf_golden") or ""),
                        "perf_unit": str(cfg.get("perf_unit") or ""),
                    })
    return rows
