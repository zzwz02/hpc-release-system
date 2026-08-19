from __future__ import annotations

from app.api.routers import cicd as cicd_router
from app.config import settings
from app.integrations import jira


def test_browse_url_uses_jira_conf_without_exposing_token(tmp_path):
    config_path = tmp_path / "jira.conf"
    config_path.write_text(
        "JIRA_BASE_URL=https://jira.example.test/\nJIRA_TOKEN=secret-token\n",
        encoding="utf-8",
    )

    assert jira.browse_url(config_path) == "https://jira.example.test/browse/"


def test_cicd_public_config_returns_only_browser_safe_jira_url(tmp_path, monkeypatch):
    config_path = tmp_path / "jira.conf"
    config_path.write_text(
        "JIRA_BASE_URL=https://jira.example.test\nJIRA_TOKEN=secret-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "jira_conf_path", config_path)

    result = cicd_router.get_config(user={"username": "rm", "role": "RM"})

    assert result == {"jira_browse_url": "https://jira.example.test/browse/"}
    assert "secret-token" not in repr(result)
