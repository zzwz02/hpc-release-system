"""Resolve image commit tags and select the historical app_info test scope."""
from __future__ import annotations

import re
from typing import Any

from app.domain.container_image import delivered_image_reference, parse_base_image_reference
from app.domain.test_commands import (
    IMAGE_TARGET_SUFFIX,
    build_target_matches,
    command_rows,
    docker_cmd,
    image_tag_matches,
    image_tag_version,
    image_values_for_build,
    normalize_arch,
)
from app.domain.test_platforms import build_platforms, chip_summary, release_tests


def parse_lookup_image(reference: str) -> dict[str, str]:
    image = parse_base_image_reference(reference)
    parts = image["image"].split("/", 2)
    if len(parts) != 3 or not all(parts) or not image["digest"]:
        raise ValueError("请输入完整镜像名：Harbor地址/项目/镜像@sha256:…")
    host, project, repository = parts
    # The optional input tag is untrusted. Immutable identity is repository +
    # digest; only Harbor's current tags are used to select the build.
    path = repository.rsplit("/", 1)
    path[-1] = path[-1].partition(":")[0]
    repository = "/".join(path)
    if any(not re.fullmatch(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*", part)
           for part in [project, *repository.split("/")]):
        raise ValueError("镜像仓库路径无效，请填写仓库/镜像@sha256:…")
    return {"host": host, "project": project, "repository": repository,
            "digest": image["digest"], "image": f"{host}/{project}/{repository}@{image['digest']}"}


def matches_delivery(image: dict[str, str], delivery: dict) -> bool:
    """Match registry/project/repository and digest; a saved tag does not change identity."""
    reference = delivered_image_reference(delivery.get("image") or delivery.get("reference", ""), delivery.get("digest", ""))
    if not reference:
        return False
    parsed = parse_base_image_reference(reference)
    address = parsed["image"].rsplit("/", 1)
    address[-1] = address[-1].partition(":")[0]
    return "/".join(address) == f'{image["host"]}/{image["project"]}/{image["repository"]}' and parsed["digest"] == image["digest"]


class CommitTagMissing(ValueError):
    """No commit tag exists; repo builds may instead have a pinned manifest."""


def commit_tags(tags: list[str]) -> tuple[str, list[str]]:
    candidates: list[tuple[str, str]] = []
    for tag in tags:
        # App versions such as 2026-1-fep-gpu themselves contain hyphens.
        # The final build number is excluded; the complete app_info pattern is
        # still checked after reading the selected commit.
        for field in tag.split("-")[1:-1]:
            if re.fullmatch(r"[0-9a-fA-F]{7,40}", field):
                candidates.append((field.lower(), tag))
    if not candidates:
        raise CommitTagMissing("该镜像没有有效 commit tag，无法定位历史 app_info")
    commit = max((item[0] for item in candidates), key=len)
    if any(not commit.startswith(other) for other, _ in candidates):
        raise ValueError("该镜像的 tags 包含多个不同 commit id，无法唯一定位历史 app_info")
    return commit, sorted({tag for _, tag in candidates})


def repo_build_tags(tags: list[str]) -> list[dict]:
    """Pair a literal repo tag with its release alias and exact Jenkins build."""
    builds = {}
    for tag in tags:
        match = re.fullmatch(r"(.+)-repo-(.+)-(\d+)", tag)
        if not match:
            continue
        app_version, platform, build = match.groups()
        prefix, suffix = f"{app_version}-", f"-{platform}-{build}"
        for alias in tags:
            if not (alias.startswith(prefix) and alias.endswith(suffix)):
                continue
            version = alias[len(prefix):-len(suffix)]
            if re.fullmatch(r"\d+(?:\.\d+)+", version):
                builds.setdefault((version, build), set()).add(tag)
    return [
        {"version": version, "build": build, "tags": sorted(selected)}
        for (version, build), selected in sorted(builds.items())
    ]


def repository_aliases(repository: str) -> set[str]:
    """Historical HPCC and MACA compiler flavors share app_info build metadata.

    Only the final compiler suffix is interchangeable. Preserve the App/spec
    prefix and release/dbg distinction, and never change the queried image.
    """
    for suffix in ("-dbg", ""):
        maca, hpcc = f"-maca{suffix}", f"-hpcc{suffix}"
        for original, alternate in ((maca, hpcc), (hpcc, maca)):
            if repository.endswith(original):
                return {repository, repository[: -len(original)] + alternate}
    return {repository}


def image_repositories(raw: dict[str, Any]) -> set[str]:
    """Build image names plus their historical HPCC compiler-flavor aliases."""
    return {
        repository
        for cfg in (raw.get("app_build") or {}).values()
        if isinstance(cfg, dict)
        for target in IMAGE_TARGET_SUFFIX
        if build_target_matches(cfg, target)
        for _, image in image_values_for_build(raw, cfg, target)
        for repository in repository_aliases(image.partition(":")[0])
    }


def _matches_tag(variant: str, image_name: str, tags: list[str], tag_version: str | None = None) -> bool:
    name, _, pattern = variant.partition(":")
    if name not in repository_aliases(image_name):
        return False
    for tag in tags:
        if not image_tag_matches(pattern, tag):
            continue
        value = image_tag_version(pattern, tag)
        if tag_version is None or value == tag_version or (
            value and re.fullmatch(r"[0-9a-fA-F]{7,40}", value) and tag_version.startswith(value.lower())
        ):
            return True
    return False


def historical_image_commands(
    raw: dict[str, Any], *, image: str, repository: str, tags: list[str], architecture: str,
    rows: list[dict] | None = None,
    tag_version: str | None = None,
) -> dict[str, Any]:
    """Use the same platform/chip join as App details and QA reports."""
    image_name = repository.rsplit("/", 1)[-1]
    builds = [
        platform for platform in build_platforms(raw)
        if (not architecture or platform["arch"] == normalize_arch(architecture))
        and any(
            _matches_tag(variant, image_name, tags, tag_version)
            for target in IMAGE_TARGET_SUFFIX if build_target_matches(platform["config"], target)
            for _, variant in image_values_for_build(raw, platform["config"], target)
        )
    ]
    if not builds:
        raise ValueError("历史 app_info 的镜像名称、版本或构建平台与 Harbor tag 不匹配")
    configs = dict(release_tests(raw))
    commands = [
        {**row, "image_pattern": row["image"], "image": image, "command": docker_cmd(configs[row["test"]], image)}
        for row in (command_rows(raw) if rows is None else rows)
        if (not architecture or row["arch"] == normalize_arch(architecture))
        and _matches_tag(row["image"], image_name, tags, tag_version)
    ]
    return {
        "app_name": raw.get("app_name", ""), "app_version": raw.get("app_version", ""),
        **chip_summary(builds), "test_commands": commands,
    }


def hpc_versions(raw: dict, repository: str, tags: list[str], *, selected_tags=None) -> list[str]:
    """Read release aliases matching a build identified by this digest's commit tags."""
    name = repository.rsplit("/", 1)[-1]
    selected = commit_tags(tags)[1] if selected_tags is None else selected_tags
    variants = {
        variant
        for platform in build_platforms(raw)
        for target in IMAGE_TARGET_SUFFIX
        if build_target_matches(platform["config"], target)
        for _, variant in image_values_for_build(raw, platform["config"], target)
        if _matches_tag(variant, name, selected)
    }
    # A commit tag is not an HPC release version. Never use tag order to choose.
    return sorted(
        {
            version
            for tag in tags
            for variant in variants
            if (version := image_tag_version(variant.partition(":")[2], tag))
            and re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", version)
        }
    )
