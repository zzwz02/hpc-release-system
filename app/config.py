"""Application settings — mirrors server.py module-level constants.

Loaded once at startup via FastAPI lifespan.  All paths default to values
relative to the project root (same as server.py).
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: two levels up from this file (app/config.py → app/ → project/)
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


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

    # --- Gerrit -----------------------------------------------------------------
    # Gerrit URL prefixes — mirrors server.py:1428-1429
    hpc_gerrit_prefix: str = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/"
    hpc_gerrit_root: str = "ssh://sw-gerrit-devops.metax-internal.com:29418/"

    # Manifest repo URL for Google-repo .xml identity resolution (plan §4.2)
    manifest_repo_url: str = (
        "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/manifest"
    )
    manifest_repo_base: str = (
        "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/"
    )

    # --- Server -----------------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000


# Singleton — import and use directly: `from app.config import settings`
settings = Settings()
