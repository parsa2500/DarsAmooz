from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env early so os.environ fallbacks see AGENTROUTER_* / OPENAI_*
load_dotenv()


def _first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip() != "":
            return val.strip()
    return default


class Settings(BaseSettings):
    """Prefer AGENTROUTER_*; fall back to OPENAI_*."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    textbooks_dir: Path = Path("data/textbooks")
    extracted_dir: Path = Path("data/extracted")
    exports_dir: Path = Path("data/exports")
    manifests_dir: Path = Path("data/manifests")

    @property
    def api_key(self) -> str | None:
        return _first_env("AGENTROUTER_API_KEY", "OPENAI_API_KEY")

    @property
    def base_url(self) -> str:
        return _first_env(
            "AGENTROUTER_BASE_URL",
            "OPENAI_BASE_URL",
            default="https://agentrouter.org/v1",
        ) or "https://agentrouter.org/v1"

    @property
    def model(self) -> str:
        return _first_env(
            "AGENTROUTER_MODEL",
            "OPENAI_MODEL",
            default="deepseek-v4-flash",
        ) or "deepseek-v4-flash"

    # Back-compat for callers that still read openai_* names
    @property
    def openai_api_key(self) -> str | None:
        return self.api_key

    @property
    def openai_base_url(self) -> str:
        return self.base_url

    @property
    def openai_model(self) -> str:
        return self.model


def get_settings() -> Settings:
    return Settings()
