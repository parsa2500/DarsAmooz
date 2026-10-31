from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env early so os.environ fallbacks see GEMINI_* / OPENROUTER_* / OPENAI_*
load_dotenv()

_VALID_PROVIDERS = frozenset({"gemini", "openrouter", "openai"})


def _first_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip() != "":
            return val.strip()
    return default


class Settings(BaseSettings):
    """LLM provider via LLM_PROVIDER (gemini | openrouter | openai). No silent switch."""

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

    # Explicit selector: gemini | openrouter | openai
    llm_provider: str | None = None

    @property
    def provider_name(self) -> str:
        explicit = (self.llm_provider or _first_env("LLM_PROVIDER") or "").strip().lower()
        if explicit in _VALID_PROVIDERS:
            return explicit
        if _first_env("GEMINI_API_KEY"):
            return "gemini"
        if _first_env("OPENROUTER_API_KEY"):
            return "openrouter"
        if _first_env("OPENAI_API_KEY"):
            return "openai"
        return "gemini"

    @property
    def api_key(self) -> str | None:
        name = self.provider_name
        if name == "gemini":
            return _first_env("GEMINI_API_KEY")
        if name == "openrouter":
            return _first_env("OPENROUTER_API_KEY")
        return _first_env("OPENAI_API_KEY")

    @property
    def base_url(self) -> str:
        name = self.provider_name
        if name == "gemini":
            return (
                _first_env(
                    "GEMINI_BASE_URL",
                    default="https://generativelanguage.googleapis.com/v1beta/openai/",
                )
                or "https://generativelanguage.googleapis.com/v1beta/openai/"
            )
        if name == "openrouter":
            return (
                _first_env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
                or "https://openrouter.ai/api/v1"
            )
        return (
            _first_env("OPENAI_BASE_URL", default="https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        )

    @property
    def model(self) -> str:
        name = self.provider_name
        if name == "gemini":
            m = _first_env("GEMINI_MODEL")
            if not m:
                raise RuntimeError(
                    "GEMINI_MODEL is required when LLM_PROVIDER=gemini "
                    "(e.g. gemini-3.6-flash). No silent model default."
                )
            return m
        if name == "openrouter":
            m = _first_env("OPENROUTER_MODEL")
            if not m:
                raise RuntimeError(
                    "OPENROUTER_MODEL is required when LLM_PROVIDER=openrouter "
                    "(e.g. google/gemma-4-31b-it:free). No silent model default."
                )
            return m
        m = _first_env("OPENAI_MODEL")
        if not m:
            raise RuntimeError(
                "OPENAI_MODEL is required when LLM_PROVIDER=openai. "
                "No silent model default."
            )
        return m

    # Back-compat aliases
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
