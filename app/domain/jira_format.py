"""Shared JIRA wiki formatting; preserve literal macro bodies and prose."""
from __future__ import annotations

import re

_MACRO_BLOCK = re.compile(r"\{(noformat|code)(?::[^}\n]*)?\}[\s\S]*?\{\1\}")
_MONOSPACE = re.compile(r"\{\{(?=\S)(.*?\S)\}\}")


def _outside_macros(text: str, transform) -> str:
    """Transform wiki text except {noformat}/{code} blocks, which JIRA shows verbatim."""
    parts, last = [], 0
    for block in _MACRO_BLOCK.finditer(text):
        parts += [transform(text[last:block.start()]), block.group(0)]
        last = block.end()
    return "".join(parts + [transform(text[last:])])


def jira_monospace_spacing(body: str) -> str:
    """JIRA ignores {{code}} touching a letter or digit, CJK included; separate it by a space."""
    def spaced(segment: str) -> str:
        def fix(match: re.Match) -> str:
            before = segment[match.start() - 1] if match.start() else ""
            after = segment[match.end()] if match.end() < len(segment) else ""
            return (" " if before.isalnum() else "") + match.group(0) + (" " if after.isalnum() else "")
        return _MONOSPACE.sub(fix, segment)
    return _outside_macros(body, spaced)


def format_jira_comment(body: str) -> str:
    """Normalize JIRA wiki presentation without rewriting facts or commands.

    Input is JIRA wiki markup, not arbitrary Markdown.
    """
    return jira_monospace_spacing(body)
