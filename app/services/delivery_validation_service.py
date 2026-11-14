"""Verify delivered artifacts against their assigned, locked app_info builds."""
from __future__ import annotations

from app.api.errors import ApiError
from app.domain.image_lookup import parse_lookup_image
from app.domain.test_commands import image_tag_matches
from app.integrations import harbor


def validate(images: list[dict], *, artifacts: dict[tuple[str, str, str], dict]) -> None:
    """Read Harbor once per artifact in this save; never trust an entered tag."""
    for image in images:
        label = f'{image.get("build") or "未指定构建"} / {image.get("image_target") or "未知类型"}'
        try:
            if image.get("build_key") == "legacy" or not image.get("os") or not image.get("arch"):
                raise ValueError("无法确定 OS/CPU 架构，请将镜像填写到明确的构建行")
            # A stored tag may be present, but Harbor must be queried by digest.
            address = image["image"].rsplit("/", 1)
            address[-1] = address[-1].partition(":")[0]
            parsed = parse_lookup_image(f'{"/".join(address)}@{image["digest"]}')
            if parsed["host"] != harbor.HARBOR_HOST:
                raise ValueError(f"仅支持 {harbor.HARBOR_HOST} 的交付镜像")
            key = (parsed["project"], parsed["repository"], parsed["digest"])
            if key not in artifacts:
                artifacts[key] = harbor.get_artifact(*key)
            artifact = artifacts[key]
            tags = artifact.get("tags")
            names = [tag["name"] for tag in tags
                     if isinstance(tag, dict) and isinstance(tag.get("name"), str)] if isinstance(tags, list) else []
            pattern = image["image_pattern"].partition(":")[2]
            if not names:
                raise ValueError("Harbor 镜像没有可用 tag，无法确认 OS/CPU 架构和构建配置")
            if not pattern or not any(image_tag_matches(pattern, tag) for tag in names):
                config = (f'OS {image["os"]}，CPU 架构 {image["arch"]}，'
                          f'Python {image.get("python_label") or "未声明"}，'
                          f'PyTorch {image.get("pytorch_label") or "未声明"}')
                raise ValueError(f'Harbor 实际 tags 与构建配置不匹配（{config}，期望 tag：{pattern}）')
        except ApiError as exc:
            raise ApiError(exc.status_code, f'{label}，{image.get("reference", "")}：{exc}') from exc
        except ValueError as exc:
            raise ValueError(f'{label}，{image.get("reference", "")}：{exc}') from exc
