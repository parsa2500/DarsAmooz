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


def provider_api_key(provider: str) -> str | None:
    name = provider.strip().lower()
    if name == "gemini":
        return _first_env("GEMINI_API_KEY")
    if name == "openrouter":
        return _first_env("OPENROUTER_API_KEY")
    if name == "openai":
        return _first_env("OPENAI_API_KEY")
    raise ValueError(f"unknown provider {provider!r}")


def provider_base_url(provider: str) -> str:
    name = provider.strip().lower()
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
    if name == "openai":
        return (
            _first_env("OPENAI_BASE_URL", default="https://api.openai.com/v1")
            or "https://api.openai.com/v1"
        )
    raise ValueError(f"unknown provider {provider!r}")


def provider_model(provider: str) -> str:
    name = provider.strip().lower()
    if name == "gemini":
        m = _first_env("GEMINI_MODEL")
        if not m:
            raise RuntimeError(
                "GEMINI_MODEL is required when using gemini "
                "(e.g. gemini-3.6-flash). No silent model default."
            )
        return m
    if name == "openrouter":
        m = _first_env("OPENROUTER_MODEL")
        if not m:
            raise RuntimeError(
                "OPENROUTER_MODEL is required when using openrouter "
                "(e.g. google/gemma-4-31b-it:free). No silent model default."
            )
        return m
    if name == "openai":
        m = _first_env("OPENAI_MODEL")
        if not m:
            raise RuntimeError(
                "OPENAI_MODEL is required when using openai. No silent model default."
            )
        return m
    raise ValueError(f"unknown provider {provider!r}")


class Settings(BaseSettings):
    """LLM provider via LLM_PROVIDER. Optional visible fallback via LLM_FALLBACK_PROVIDER."""

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
    llm_fallback_provider: str | None = None

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
    def fallback_provider_name(self) -> str | None:
        raw = (
            self.llm_fallback_provider
            or _first_env("LLM_FALLBACK_PROVIDER")
            or ""
        ).strip().lower()
        if not raw or raw in {"none", "off", "false", "0", "-"}:
            return None
        if raw not in _VALID_PROVIDERS:
            raise RuntimeError(
                f"Unsupported LLM_FALLBACK_PROVIDER={raw!r}. "
                "Use 'gemini', 'openrouter', 'openai', or leave empty to disable."
            )
        if raw == self.provider_name:
            return None
        return raw

    @property
    def api_key(self) -> str | None:
        return provider_api_key(self.provider_name)

    @property
    def base_url(self) -> str:
        return provider_base_url(self.provider_name)

    @property
    def model(self) -> str:
        return provider_model(self.provider_name)

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


class ProviderSettingsView:
    """Settings-like view pinned to one provider (for primary/fallback clients)."""

    def __init__(
        self,
        base: Settings,
        provider: str,
        *,
        model_override: str | None = None,
    ) -> None:
        self._base = base
        self._provider = provider.strip().lower()
        if self._provider not in _VALID_PROVIDERS:
            raise RuntimeError(f"Unsupported provider {provider!r}")
        self._model_override = (model_override or "").strip() or None
        # expose path attrs used by some callers
        self.data_dir = base.data_dir
        self.textbooks_dir = base.textbooks_dir
        self.extracted_dir = base.extracted_dir
        self.exports_dir = base.exports_dir
        self.manifests_dir = base.manifests_dir

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def fallback_provider_name(self) -> str | None:
        return None

    @property
    def api_key(self) -> str | None:
        return provider_api_key(self._provider)

    @property
    def base_url(self) -> str:
        return provider_base_url(self._provider)

    @property
    def model(self) -> str:
        if self._model_override:
            return self._model_override
        return provider_model(self._provider)


def get_settings() -> Settings:
    return Settings()
