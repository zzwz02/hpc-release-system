from __future__ import annotations

from pathlib import Path

import pytest

from app import runtime_config
from app.api.routers import cicd_agent
from app.config import settings
from app.integrations import ldap
from app.services import jira_agent_runner


@pytest.fixture()
def conf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "release_system.conf"
    monkeypatch.setattr(settings, "runtime_conf_path", path)
    return path


def test_sections_keep_key_case_and_missing_file_is_empty(conf: Path) -> None:
    assert runtime_config.section("jira") == {}
    assert runtime_config.agent_group_sections() == {}

    conf.write_text(
        "# comment\n[jira]\nJIRA_BASE_URL = http://jira/\nJIRA_TOKEN = a=b%c\n"
        "[ldap]\nENABLED = true\nURI = ldap://ad\n",
        encoding="utf-8",
    )

    assert runtime_config.section("jira") == {"JIRA_BASE_URL": "http://jira/", "JIRA_TOKEN": "a=b%c"}
    assert runtime_config.section("qa_llm") == {}


def test_agent_groups_are_collected_by_prefix(conf: Path) -> None:
    conf.write_text(
        "[jira]\nJIRA_BASE_URL = http://jira\n"
        "[jira_agent:HPC]\nCODEX_WS_URL = ws://b:1\nMODEL =\n"
        "[jira_agent:PYTORCH]\nCODEX_WS_URL = ws://p:1\n",
        encoding="utf-8",
    )

    assert runtime_config.agent_group_sections() == {
        "HPC": {"CODEX_WS_URL": "ws://b:1", "MODEL": ""},
        "PYTORCH": {"CODEX_WS_URL": "ws://p:1"},
    }


def test_edits_apply_on_next_read(conf: Path) -> None:
    conf.write_text("[cicd_agent]\nBASE_URL = http://old:1/\nTIMEOUT_SECONDS = 5\n", encoding="utf-8")
    assert cicd_agent._agent_url("/x") == "http://old:1/x"
    assert cicd_agent._agent_timeout_seconds() == 5

    conf.write_text("[cicd_agent]\nBASE_URL = http://new:2\nTIMEOUT_SECONDS = 7\n", encoding="utf-8")
    assert cicd_agent._agent_url("/x") == "http://new:2/x"
    assert cicd_agent._agent_timeout_seconds() == 7


def test_missing_required_keys_are_named_not_defaulted(conf: Path) -> None:
    conf.write_text("[cicd_agent]\nBASE_URL = http://agent\nTIMEOUT_SECONDS = soon\n", encoding="utf-8")
    status, payload = cicd_agent._request_agent_payload("GET", "/x")
    assert status == 503
    assert payload == {
        "ok": False,
        "error": "release_system.conf [cicd_agent] TIMEOUT_SECONDS 必须是整数，当前为 'soon'",
    }

    conf.write_text("[jira_agent:HPC]\nCODEX_WS_URL = ws://b:1\nWORKSPACE_ROOT = /w\n", encoding="utf-8")
    with pytest.raises(runtime_config.ConfigError, match=r"\[jira_agent:HPC\] 缺少 MAX_CONCURRENT"):
        jira_agent_runner.load_groups()


def test_ldap_is_off_without_section_and_strict_once_enabled(conf: Path) -> None:
    assert ldap.current_ldap_config() == {"enabled": False}
    assert ldap.ldap_status() == {"enabled": False, "uri": ""}

    conf.write_text("[ldap]\nENABLED = true\nURI = ldap://ad\n", encoding="utf-8")
    assert ldap.ldap_status() == {"enabled": True, "uri": "ldap://ad"}
    with pytest.raises(runtime_config.ConfigError, match=r"\[ldap\] 缺少 BASE"):
        ldap.current_ldap_config()


def test_example_file_parses_into_every_section(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.domain import jira_agent as domain

    example = Path(__file__).resolve().parents[1] / "release_system.conf.example"
    monkeypatch.setattr(settings, "runtime_conf_path", example)

    assert ldap.current_ldap_config()["uri"].startswith("ldap://")
    assert runtime_config.section("jira")["JIRA_BASE_URL"]
    assert runtime_config.section("qa_llm")["QA_LLM_MODEL"]
    assert cicd_agent._agent_timeout_seconds() == 90
    [group] = domain.groups_from_sections(runtime_config.agent_group_sections()).values()
    assert (group.name, group.max_concurrent, group.components) == ("HPC", 2, ("PDE_HPC",))


def test_qa_llm_reads_only_the_file(conf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.integrations import llm

    conf.write_text("[qa_llm]\nQA_LLM_BASE_URL = http://file/v1\nQA_LLM_MODEL = file-model\n", encoding="utf-8")
    monkeypatch.setenv("QA_LLM_MODEL", "env-model")

    assert llm.llm_settings() == {
        "QA_LLM_BASE_URL": "http://file/v1",
        "QA_LLM_API_KEY": "",
        "QA_LLM_MODEL": "file-model",
    }


def test_site_public_base_url_builds_comment_links(conf: Path) -> None:
    assert jira_agent_runner.conversation_url("c1") == ""

    conf.write_text("[site]\nPUBLIC_BASE_URL = http://site/\n", encoding="utf-8")
    assert jira_agent_runner.conversation_url("c1") == "http://site/jira-agent?conversation=c1"
