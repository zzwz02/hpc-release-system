"""Markdown rendering helpers for artifacts — ported from core.py:3142-3259.

Pure functions: no HTTP, no DB, unit-testable in isolation.
"""
from __future__ import annotations

import re
from typing import Any


def md_title(title: str, level: int = 1) -> str:
    hashes = "#" * max(1, min(6, level))
    return f"{hashes} {title}\n\n"


def code_block(content: str, lang: str = "shell", *, indent: str = "") -> str:
    if not content:
        return "\n"
    text = str(content).replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(f"{indent}{line}" for line in text.split("\n"))
    return f"{indent}```{lang}\n{body}\n{indent}```\n\n"


def inline_code(content: str) -> str:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ").strip()
    if not text:
        return "``"
    max_ticks = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    ticks = "`" * (max_ticks + 1)
    if max_ticks:
        return f"{ticks} {text} {ticks}"
    return f"`{text}`"


def markdown_fences_on_new_lines(content: str) -> str:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n")
    split_lines = []
    for line in text.split("\n"):
        fence_at = line.find("```")
        if fence_at < 0:
            split_lines.append(line)
            continue

        indent_len = len(line) - len(line.lstrip(" \t"))
        if fence_at == indent_len:
            split_lines.append(line)
            continue

        before_fence = line[:fence_at].lstrip(" \t")
        if before_fence and re.fullmatch(r"(>\s*)+", before_fence):
            split_lines.append(line)
            continue

        before = line[:fence_at].rstrip()
        fence = line[fence_at:].lstrip(" \t")
        if before:
            split_lines.append(before)
        split_lines.append(line[:indent_len] + fence)

    lines = []
    in_fence = False
    fence_indent = ""
    code_lines = []

    def flush_code_lines() -> None:
        if fence_indent and not all(line.startswith(fence_indent) for line in code_lines if line):
            lines.extend(fence_indent + line for line in code_lines)
        else:
            lines.extend(code_lines)

    for line in split_lines:
        stripped = line.lstrip(" \t")
        is_fence = stripped.startswith("```")
        if not in_fence:
            lines.append(line)
            if is_fence:
                in_fence = True
                fence_indent = line[:len(line) - len(stripped)]
                code_lines = []
            continue

        if is_fence:
            flush_code_lines()
            lines.append(fence_indent + stripped)
            in_fence = False
            fence_indent = ""
            code_lines = []
        else:
            code_lines.append(line)

    if in_fence:
        flush_code_lines()

    return "\n".join(lines)


def owner_markdown_block(content: str) -> str:
    content = markdown_fences_on_new_lines(content)
    if not content:
        return "\n"
    if content.endswith("\n\n"):
        return content
    if content.endswith("\n"):
        return content + "\n"
    return content + "\n\n"


def guide_test_doc_field(label: str, value: Any, *, indent: str = "  ", quote: bool = False) -> str:
    text = markdown_fences_on_new_lines("" if value is None else str(value))
    lines = text.split("\n")
    continuation_indent = indent + "  "
    if quote:
        rendered = [f"{indent}- {label}："]
        rendered.extend(f"{continuation_indent}>{line}" for line in lines)
        return "\n".join(rendered) + "\n"
    if lines[0].lstrip(" \t").startswith("```"):
        rendered = [f"{indent}- {label}："]
        rendered.extend(f"{continuation_indent}{line}" if line else "" for line in lines)
    else:
        rendered = [f"{indent}- {label}：{lines[0]}"]
        rendered.extend(f"{continuation_indent}{line}" if line else "" for line in lines[1:])
    return "\n".join(rendered) + "\n"


def md_cell(value: Any) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\\", "\\\\").replace("|", "\\|")
    return s.replace("\r\n", " ").replace("\n", "<br>").replace("\r", " ")
