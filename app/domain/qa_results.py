"""Flat QA command rows and the seven-field historical-result identity."""

from __future__ import annotations

import hashlib
import json
import re
import shlex

from app.domain.container_image import parse_base_image_reference
from app.domain.test_commands import denormalize_arch

KEY_COLUMNS = ("app_name", "git_branch", "app_version", "arch", "OS", "GPU", "test_name")
COMMAND_COLUMNS = [
    "app_name",
    "git_branch",
    "app_version",
    "arch",
    "OS",
    "GPU",
    "commit_id",
    "test_name",
    "docker_cmd",
    "perf_golden",
    "perf_unit",
    "Python label",
    "PyTorch label",
]
RESULT_COLUMNS = ["test_result", "test_digest", "test_env"]
COLUMNS = COMMAND_COLUMNS + RESULT_COLUMNS


def identity_values(row: dict) -> tuple[str, ...]:
    values = [str(row.get(column, "") or "").strip() for column in KEY_COLUMNS]
    # Normalize App/platform spelling; preserve repository branch and test-path case.
    values[0] = values[0].casefold()
    values[3] = denormalize_arch(values[3])
    values[4] = values[4].lower()
    values[5] = values[5].upper()
    return tuple(values)


def result_key(row: dict) -> str:
    values = identity_values(row)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def flat_commands(result: dict) -> list[dict]:
    rows = []
    for command in result["test_commands"]:
        for chip in command["chips"] or [""]:
            rows.append(
                {
                    "app_name": result["app_name"],
                    "git_branch": result["git_branch"],
                    "app_version": result["app_version"],
                    "arch": denormalize_arch(command["arch"]),
                    "OS": command["os"],
                    "GPU": chip,
                    "commit_id": result["commit_id"],
                    "test_name": command["test"],
                    "docker_cmd": command["command"],
                    "perf_golden": command["perf_golden"],
                    "perf_unit": command["perf_unit"],
                    "Python label": command.get("python_label", ""),
                    "PyTorch label": command.get("pytorch_label", ""),
                    **{column: "" for column in RESULT_COLUMNS},
                }
            )
    return rows


def result_reference(row: dict) -> str:
    """Accept full image@digest or resolve a bare digest using the Excel command."""
    value = str(row.get("test_digest") or "").strip()
    if not value:
        return ""
    if "@" in value:
        parsed = parse_base_image_reference(value)
    else:
        digest = value if value.startswith("sha256:") else "sha256:" + value
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError("test_digest 应为 64 位 digest、sha256:digest 或完整镜像@digest")
        try:
            tokens = shlex.split(str(row.get("docker_cmd") or ""))
        except ValueError as exc:
            raise ValueError("docker_cmd 引号不完整") from exc
        # Only the Docker image operand (immediately before sh -c), never an image
        # mentioned inside a mount, environment variable or the test shell string.
        indexes = [i for i in range(1, len(tokens) - 1) if tokens[i : i + 2] == ["sh", "-c"]]
        if not indexes:
            raise ValueError("填写裸 digest 时，docker_cmd 中需要有完整 Harbor 镜像名")
        parsed = parse_base_image_reference(tokens[indexes[0] - 1].split("@")[0] + "@" + digest)
    address = parsed["image"].rsplit("/", 1)
    address[-1] = address[-1].partition(":")[0]
    return "/".join(address) + "@" + parsed["digest"]
