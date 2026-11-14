"""Resolve image commit tags and select the historical app_info test scope."""
from __future__ import annotations

import re
from typing import Any

from app.domain.container_image import delivered_image_reference, parse_base_image_reference
from app.domain.test_platforms import build_platforms, chip_summary, release_tests
from app.domain.test_commands import (
    IMAGE_TARGET_SUFFIX, build_target_matches, command_rows, docker_cmd, image_values_for_build,
    normalize_arch, image_tag_matches,
)


def parse_lookup_image(reference: str) -> dict[str, str]:
    image = parse_base_image_reference(reference)
    parts = image["image"].split("/", 2)
    if len(parts) != 3 or not all(parts) or not image["digest"]:
        raise ValueError("请输入完整镜像名：Harbor地址/项目/镜像@sha256:…")
    host, project, repository = parts
    if any(not re.fullmatch(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*", part)
           for part in [project, *repository.split("/")]):
        raise ValueError("请使用不含 tag 的完整镜像名：仓库/镜像@sha256:…")
    return {"host": host, "project": project, "repository": repository,
            "digest": image["digest"], "image": f"{image['image']}@{image['digest']}"}


def matches_delivery(image: dict[str, str], delivery: dict) -> bool:
    """Match registry/project/repository and digest; a saved tag does not change identity."""
    reference = delivered_image_reference(delivery.get("image") or delivery.get("reference", ""), delivery.get("digest", ""))
    if not reference:
        return False
    parsed = parse_base_image_reference(reference)
    address = parsed["image"].rsplit("/", 1)
    address[-1] = address[-1].partition(":")[0]
    return "/".join(address) == f'{image["host"]}/{image["project"]}/{image["repository"]}' and parsed["digest"] == image["digest"]


def commit_tags(tags: list[str]) -> tuple[str, list[str]]:
    candidates: list[tuple[str, str]] = []
    for tag in tags:
        fields = tag.split("-", 2)
        if len(fields) == 3 and re.fullmatch(r"[0-9a-fA-F]{7,40}", fields[1]):
            candidates.append((fields[1].lower(), tag))
    if not candidates:
        raise ValueError("该镜像没有第二字段为 commit id 的 tag，无法定位历史 app_info")
    commit = max((item[0] for item in candidates), key=len)
    if any(not commit.startswith(other) for other, _ in candidates):
        raise ValueError("该镜像的 tags 包含多个不同 commit id，无法唯一定位历史 app_info")
    return commit, sorted({tag for _, tag in candidates})


def image_repositories(raw: dict[str, Any]) -> set[str]:
    """Use the same naming rules as QA reports, including build-level spec."""
    return {
        image.partition(":")[0]
        for cfg in (raw.get("app_build") or {}).values()
        if isinstance(cfg, dict)
        for target in IMAGE_TARGET_SUFFIX if build_target_matches(cfg, target)
        for _, image in image_values_for_build(raw, cfg, target)
    }


def _matches_tag(variant: str, image_name: str, tags: list[str]) -> bool:
    name, _, pattern = variant.partition(":")
    if name != image_name:
        return False
    for tag in tags:
        commit = tag.split("-", 2)[1]
        if image_tag_matches(pattern, tag, hpc_version=commit):
            return True
    return False


def historical_image_commands(
    raw: dict[str, Any], *, image: str, repository: str, tags: list[str], architecture: str,
) -> dict[str, Any]:
    """Use the same platform/chip join as App details and QA reports."""
    image_name = repository.rsplit("/", 1)[-1]
    builds = [
        platform for platform in build_platforms(raw)
        if (not architecture or platform["arch"] == normalize_arch(architecture))
        and any(
            _matches_tag(variant, image_name, tags)
            for target in IMAGE_TARGET_SUFFIX if build_target_matches(platform["config"], target)
            for _, variant in image_values_for_build(raw, platform["config"], target)
        )
    ]
    if not builds:
        raise ValueError("历史 app_info 的镜像名称、版本或构建平台与 Harbor tag 不匹配")
    configs = dict(release_tests(raw))
    commands = [
        {**row, "image": image, "command": docker_cmd(configs[row["test"]], image)}
        for row in command_rows(raw)
        if (not architecture or row["arch"] == normalize_arch(architecture))
        and _matches_tag(row["image"], image_name, tags)
    ]
    return {
        "app_name": raw.get("app_name", ""), "app_version": raw.get("app_version", ""),
        **chip_summary(builds), "test_commands": commands,
    }
