from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    data_dir: Path = Path("data")
    textbooks_dir: Path = Path("data/textbooks")
    extracted_dir: Path = Path("data/extracted")
    exports_dir: Path = Path("data/exports")
    manifests_dir: Path = Path("data/manifests")


def get_settings() -> Settings:
    return Settings()
