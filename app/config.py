"""Application settings — mirrors server.py module-level constants.

Loaded once at startup via FastAPI lifespan.  All paths default to values
relative to the project root (same as server.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: two levels up from this file (app/config.py → app/ → project/)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_INTEGRATIONS_PATH = _PROJECT_ROOT / "shared" / "integrations.json"


def _load_gerrit_defaults() -> tuple[str, str, str, str]:
    """Load the Gerrit defaults shared by the backend and React frontend."""
    raw = json.loads(_INTEGRATIONS_PATH.read_text(encoding="utf-8"))
    try:
        gerrit = raw["gerrit"]
        ssh_base_url = str(gerrit["ssh_base_url"]).strip().rstrip("/")
        hpc_project = str(gerrit["hpc_project"]).strip().strip("/")
        manifest_project = str(gerrit["manifest_project"]).strip().strip("/")
        manifest_branch = str(gerrit["manifest_branch"]).strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "shared/integrations.json must define gerrit ssh_base_url, "
            "hpc_project, manifest_project, and manifest_branch"
        ) from exc
    if not ssh_base_url or not hpc_project or not manifest_project or not manifest_branch:
        raise RuntimeError("shared Gerrit configuration values must be non-empty")
    return ssh_base_url, hpc_project, manifest_project, manifest_branch


(
    DEFAULT_GERRIT_SSH_BASE_URL,
    DEFAULT_GERRIT_HPC_PROJECT,
    DEFAULT_GERRIT_MANIFEST_PROJECT,
    DEFAULT_GERRIT_MANIFEST_BRANCH,
) = _load_gerrit_defaults()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Database ---------------------------------------------------------------
    db_path: Path = _PROJECT_ROOT / "release_system.db"

    # --- Auth -------------------------------------------------------------------
    admin_password_file: Path = _PROJECT_ROOT / "admin_password.local"

    # --- Runtime service configuration -----------------------------------------
    # LDAP, Jira, QA LLM, CICD Agent and JIRA agent groups all live in this one
    # sectioned file (see app/runtime_config.py and release_system.conf.example).
    runtime_conf_path: Path = _PROJECT_ROOT / "release_system.conf"

    # --- CICD Assistant ---------------------------------------------------------
    assistant_database_url: str = f"sqlite:///{(_PROJECT_ROOT / 'assistant_conversations.db').as_posix()}"
    assistant_history_limit: int = 12
    assistant_summary_trigger_messages: int = 20
    assistant_summary_keep_messages: int = 12
    assistant_summary_max_chars: int = 4000

    # --- JIRA Agent -------------------------------------------------------------
    # Per-group digital employees.  The [jira_agent:<group>] sections of
    # runtime_conf_path hold each group's Codex app-server URL and token;
    # knowledge, skills and execution credentials live on that group's
    # app-server host, not on this website.
    jira_agent_database_url: str = f"sqlite:///{(_PROJECT_ROOT / 'jira_agent_tasks.db').as_posix()}"
    jira_agent_data_dir: Path = _PROJECT_ROOT / "jira_agent_data"
    jira_agent_runner_enabled: bool = True

    # --- Gerrit -----------------------------------------------------------------
    # One deploy-time override for the Gerrit SSH origin. Project paths come
    # from shared/integrations.json, which is also consumed by the frontend.
    gerrit_ssh_base_url: str = DEFAULT_GERRIT_SSH_BASE_URL

    @property
    def gerrit_hpc_project(self) -> str:
        return DEFAULT_GERRIT_HPC_PROJECT

    @property
    def gerrit_manifest_project(self) -> str:
        return DEFAULT_GERRIT_MANIFEST_PROJECT

    @property
    def gerrit_manifest_branch(self) -> str:
        return DEFAULT_GERRIT_MANIFEST_BRANCH

    @property
    def gerrit_hpc_base_url(self) -> str:
        return (
            f"{self.gerrit_ssh_base_url.rstrip('/')}"
            f"/{self.gerrit_hpc_project.strip('/')}"
        )

    @property
    def hpc_gerrit_prefix(self) -> str:
        return f"{self.gerrit_hpc_base_url}/"

    @property
    def hpc_gerrit_root(self) -> str:
        return f"{self.gerrit_ssh_base_url.rstrip('/')}/"

    @property
    def manifest_repo_url(self) -> str:
        return f"{self.gerrit_hpc_base_url}/{self.gerrit_manifest_project.strip('/')}"

    @property
    def manifest_repo_base(self) -> str:
        return self.hpc_gerrit_prefix


# Singleton — import and use directly: `from app.config import settings`
settings = Settings()
