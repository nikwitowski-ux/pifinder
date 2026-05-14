from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_YAML = PROJECT_ROOT / "config.yaml"


class Settings(BaseSettings):
    """Env-driven settings. Values are pulled from a .env in PROJECT_ROOT (gitignored)."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    google_places_api_key: SecretStr | None = Field(default=None)
    meta_ad_library_access_token: SecretStr | None = Field(default=None)
    google_maps_embed_api_key: SecretStr | None = Field(default=None)

    pifinder_db_path: str = "data/firms.db"
    pifinder_cache_dir: str = "data/cache"
    pifinder_log_level: str = "INFO"
    pifinder_http_timeout_s: float = 20.0
    pifinder_user_agent: str = "pifinder/0.1 (+contact: you@example.com)"

    @property
    def db_path(self) -> Path:
        p = Path(self.pifinder_db_path)
        return p if p.is_absolute() else PROJECT_ROOT / p

    @property
    def cache_dir(self) -> Path:
        p = Path(self.pifinder_cache_dir)
        return p if p.is_absolute() else PROJECT_ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_yaml_config(path: str | None = None) -> dict[str, Any]:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_YAML
    if not cfg_path.exists():
        return {}
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_scoring_weights() -> dict[str, int]:
    return dict(get_yaml_config().get("scoring", {}).get("weights", {}))


def get_score_buckets() -> dict[str, int]:
    return dict(get_yaml_config().get("scoring", {}).get("buckets", {}))


def get_discovery_defaults() -> dict[str, Any]:
    return dict(get_yaml_config().get("discovery", {}))


def get_http_config() -> dict[str, Any]:
    return dict(get_yaml_config().get("http", {}))
