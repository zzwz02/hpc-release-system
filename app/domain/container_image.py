"""Parse the full container reference entered for a release base image."""
from __future__ import annotations

import re


def parse_base_image_reference(value: str) -> dict[str, str]:
    reference = re.sub(r"^docker\s+pull(?:\s+|$)", "", str(value or "").strip()).strip()
    if not reference:
        raise ValueError("请填写完整镜像引用")
    if any(character.isspace() for character in reference):
        raise ValueError("一行只能填写一个镜像引用，不能包含空格或换行")
    address, separator, digest = reference.partition("@")
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", address)
        or "//" in address
        or address.endswith(("/", ":"))
    ):
        raise ValueError("镜像地址格式错误，请填写仓库/镜像:tag 或仓库/镜像@sha256:…")
    if separator and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise ValueError("镜像 digest 格式错误，@ 后应为 sha256: 加 64 位十六进制字符")
    return {"name": address.rsplit("/", 1)[-1], "image": address, "digest": digest.lower()}
