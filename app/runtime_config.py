"""Runtime service configuration — one sectioned INI file, release_system.conf.

Sections: [ldap] [jira] [qa_llm] [cicd_agent] and one [jira_agent:<group>]
per JIRA agent digital employee; release_system.conf.example documents every
key.  Accessors re-read the file on every call (it is a few KB), so an edit
applies on the next use without a restart.

Code never substitutes a value for a missing key: an absent section means the
integration is not configured, and a missing required key is a ConfigError
naming it.  Optional keys left empty only switch their feature off.
"""
from __future__ import annotations

import configparser

from app.config import settings

AGENT_GROUP_PREFIX = "jira_agent:"


class ConfigError(RuntimeError):
    """A required key of release_system.conf is missing or malformed."""


def _parser() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # keep key case: JIRA_TOKEN, uri, ...
    return parser


def _read() -> configparser.ConfigParser:
    parser = _parser()
    path = settings.runtime_conf_path
    if path.exists():
        parser.read(path, encoding="utf-8")
    return parser


def _values(parser: configparser.ConfigParser, name: str) -> dict[str, str]:
    return {key: value.strip() for key, value in parser[name].items()}


def section(name: str) -> dict[str, str]:
    """Return one section's raw string values; {} when the section is absent."""
    parser = _read()
    return _values(parser, name) if parser.has_section(name) else {}


def agent_group_sections() -> dict[str, dict[str, str]]:
    """Return every [jira_agent:<group>] section, keyed by group name."""
    parser = _read()
    return {
        name[len(AGENT_GROUP_PREFIX):]: _values(parser, name)
        for name in parser.sections()
        if name.startswith(AGENT_GROUP_PREFIX)
    }



def required(section_name: str, values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise ConfigError(f"release_system.conf [{section_name}] 缺少 {key}")
    return value


def required_int(section_name: str, values: dict[str, str], key: str) -> int:
    raw = required(section_name, values, key)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"release_system.conf [{section_name}] {key} 必须是整数，当前为 {raw!r}") from exc
