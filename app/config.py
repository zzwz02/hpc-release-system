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


def _load_gerrit_defaults() -> tuple[str, str, str]:
    """Load the Gerrit defaults shared by the backend and React frontend."""
    raw = json.loads(_INTEGRATIONS_PATH.read_text(encoding="utf-8"))
    try:
        gerrit = raw["gerrit"]
        ssh_base_url = str(gerrit["ssh_base_url"]).strip().rstrip("/")
        hpc_project = str(gerrit["hpc_project"]).strip().strip("/")
        manifest_project = str(gerrit["manifest_project"]).strip().strip("/")
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            "shared/integrations.json must define gerrit ssh_base_url, "
            "hpc_project, and manifest_project"
        ) from exc
    if not ssh_base_url or not hpc_project or not manifest_project:
        raise RuntimeError("shared Gerrit configuration values must be non-empty")
    return ssh_base_url, hpc_project, manifest_project


(
    DEFAULT_GERRIT_SSH_BASE_URL,
    DEFAULT_GERRIT_HPC_PROJECT,
    DEFAULT_GERRIT_MANIFEST_PROJECT,
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

    # --- LDAP -------------------------------------------------------------------
    ldap_conf_path: Path = _PROJECT_ROOT / "ldap.conf"

    # --- Jira -------------------------------------------------------------------
    jira_conf_path: Path = _PROJECT_ROOT / "jira.conf"

    # --- QA LLM -----------------------------------------------------------------
    # Path to the qa_llm.env file (overridable via QA_LLM_ENV_FILE env var).
    # release_system/llm.py uses this file as the default config source.
    qa_llm_env_file: Path = _PROJECT_ROOT / "qa_llm.env"

    # --- CICD Agent -------------------------------------------------------------
    # Jenkins failure diagnostics backend.  Frontend calls this service through
    # same-origin /api/cicd-agent/* proxy endpoints.
    cicd_agent_base_url: str = "http://10.2.118.76:8056"
    cicd_agent_timeout_seconds: int = 90

    # --- Gerrit -----------------------------------------------------------------
    # One deploy-time override for the Gerrit SSH origin. Project paths come
    # from shared/integrations.json, which is also consumed by the frontend.
    gerrit_ssh_base_url: str = DEFAULT_GERRIT_SSH_BASE_URL

    # Maximum Gerrit/manifest I/O operations issued concurrently by one bulk
    # app_info fetch.  Database writes remain serial in the request thread.
    gerrit_fetch_max_workers: int = 4

    @property
    def gerrit_hpc_project(self) -> str:
        return DEFAULT_GERRIT_HPC_PROJECT

    @property
    def gerrit_manifest_project(self) -> str:
        return DEFAULT_GERRIT_MANIFEST_PROJECT

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

    # --- Server -----------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000


# Singleton — import and use directly: `from app.config import settings`
settings = Settings()
