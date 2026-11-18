from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env early so os.environ fallbacks see GEMINI_* / OPENROUTER_* / OPENAI_*
load_dotenv()

_VALID_PROVIDERS = frozenset({"gemini", "openrouter", "openai"})
VALID_PROVIDERS = _VALID_PROVIDERS

# Suggested model ids shown in UI (free-text still allowed).
_MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "gemini": ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-2.0-flash"],
    "openrouter": [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "baidu/ernie-5.5-flash-preview:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ],
    "openai": ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1"],
}


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


def tavily_api_key(settings: Settings | None = None) -> str | None:
    """Tavily key from settings field or TAVILY_API_KEY env."""
    if settings is not None:
        field = (settings.tavily_api_key or "").strip()
        if field:
            return field
    return _first_env("TAVILY_API_KEY")


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
    """LLM provider via LLM_PROVIDER (default openrouter). Optional visible fallback via LLM_FALLBACK_PROVIDER."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    textbooks_dir: Path = Path("data/textbooks")
    uploads_dir: Path = Path("data/uploads")
    extracted_dir: Path = Path("data/extracted")
    exports_dir: Path = Path("data/exports")
    manifests_dir: Path = Path("data/manifests")

    # Explicit selector: gemini | openrouter | openai
    llm_provider: str | None = None
    llm_fallback_provider: str | None = None
    # Optional: Tavily web search for the handout flow. Also read via tavily_api_key().
    tavily_api_key: str | None = None

    @property
    def provider_name(self) -> str:
        explicit = (self.llm_provider or _first_env("LLM_PROVIDER") or "").strip().lower()
        if explicit in _VALID_PROVIDERS:
            return explicit
        if _first_env("OPENROUTER_API_KEY"):
            return "openrouter"
        if _first_env("GEMINI_API_KEY"):
            return "gemini"
        if _first_env("OPENAI_API_KEY"):
            return "openai"
        return "openrouter"

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

    @property
    def has_tavily_key(self) -> bool:
        return bool(tavily_api_key(self))

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
        self.uploads_dir = base.uploads_dir
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


def normalize_provider_id(provider: str | None) -> str | None:
    if provider is None:
        return None
    name = provider.strip().lower()
    if not name:
        return None
    if name not in _VALID_PROVIDERS:
        raise ValueError(
            f"Unsupported provider {provider!r}. Use: {', '.join(sorted(_VALID_PROVIDERS))}."
        )
    return name


def list_provider_catalog(settings: Settings | None = None) -> list[dict]:
    """Providers + key status + default/suggested models for UI/CLI."""
    settings = settings or get_settings()
    catalog: list[dict] = []
    for name in ("openrouter", "gemini", "openai"):
        key = provider_api_key(name)
        default_model = None
        try:
            default_model = provider_model(name)
        except RuntimeError:
            default_model = None
        suggestions = list(_MODEL_SUGGESTIONS.get(name, []))
        if default_model and default_model not in suggestions:
            suggestions = [default_model, *suggestions]
        catalog.append(
            {
                "id": name,
                "label": {"gemini": "Gemini", "openrouter": "OpenRouter", "openai": "OpenAI"}.get(
                    name, name
                ),
                "has_api_key": bool(key),
                "default_model": default_model,
                "model_suggestions": suggestions,
                "is_default": name == settings.provider_name,
            }
        )
    return catalog


def resolve_llm_selection(
    settings: Settings | None = None,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, str | None, ProviderSettingsView]:
    """
    Resolve runtime primary provider/model for one generate call.

    Returns (provider_id, model_or_none, ProviderSettingsView).
    model may be None only when reading catalog; for live calls model is required
    via env or explicit override.
    """
    settings = settings or get_settings()
    provider_id = normalize_provider_id(provider) or settings.provider_name
    model_override = (model or "").strip() or None
    view = ProviderSettingsView(settings, provider_id, model_override=model_override)
    resolved_model: str | None
    try:
        resolved_model = view.model
    except RuntimeError:
        resolved_model = model_override
    return provider_id, resolved_model, view
