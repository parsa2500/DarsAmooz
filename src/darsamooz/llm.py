from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from darsamooz.config import ProviderSettingsView, Settings, get_settings

logger = logging.getLogger(__name__)

_HTTP_STATUS_RE = re.compile(r"HTTP\s+(\d{3})", re.IGNORECASE)


def is_fallback_worthy(exc: BaseException) -> bool:
    """True for quota/rate-limit, 5xx, timeouts, transport, and other LLM API failures.

    Does NOT treat local validation / JSON-parse / empty-content as API errors —
    those mean the request reached a model and returned something unusable.
    """
    if isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.TransportError,
            httpx.NetworkError,
            httpx.ConnectError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True

    msg = str(exc)
    lower = msg.lower()

    # Explicit HTTP status from our OpenAICompatibleProvider raise path
    m = _HTTP_STATUS_RE.search(msg)
    if m:
        code = int(m.group(1))
        # Any HTTP API error from the primary (4xx/5xx) is fallback-worthy,
        # including 429 quota/rate limit and 5xx.
        if code >= 400:
            return True

    # Common quota / rate / overload phrasing without a status code
    for needle in (
        "rate limit",
        "quota",
        "resource_exhausted",
        "too many requests",
        "overloaded",
        "temporarily unavailable",
        "service unavailable",
        "gateway timeout",
        "bad gateway",
        "timed out",
        "timeout",
        "connection reset",
        "connection refused",
        "llm request failed",
    ):
        if needle in lower:
            return True

    return False


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return parsed JSON object from the model."""


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        settings: Settings | ProviderSettingsView | None = None,
        *,
        model_override: str | None = None,
    ):
        self.settings = settings or get_settings()
        self._model_override = (model_override or "").strip() or None
        self.last_usage: dict[str, Any] | None = None
        self.last_call: dict[str, Any] | None = None
        self.call_history: list[dict[str, Any]] = []

    @property
    def provider_label(self) -> str:
        return self.settings.provider_name

    @property
    def model_label(self) -> str:
        if self._model_override:
            return self._model_override
        return self.settings.model

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if not self.settings.api_key:
            raise RuntimeError(
                f"No API key configured for provider {self.settings.provider_name!r}. "
                "Set GEMINI_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY."
            )
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter optionally uses these; harmless elsewhere
        if self.settings.provider_name == "openrouter":
            headers.setdefault("HTTP-Referer", "https://github.com/local/darsamooz")
            headers.setdefault("X-Title", "DarsAmooz")

        model = self.model_label
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        }
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        self.last_usage = None
        self.last_call = {
            "provider": self.provider_label,
            "model": model,
            "fallback_used": False,
            "fallback_from": None,
            "fallback_reason": None,
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"LLM request failed HTTP {resp.status_code}: {resp.text[:500]}"
                )
            data = resp.json()
        usage = data.get("usage")
        if isinstance(usage, dict):
            self.last_usage = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM payload: {data!r}") from exc
        if not content or not str(content).strip():
            raise RuntimeError("LLM returned empty content")
        text = str(content).strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"LLM did not return valid JSON: {text[:400]}") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM JSON root must be an object")
        self.call_history.append(dict(self.last_call))
        return parsed


class FallbackLLMProvider(LLMProvider):
    """Try primary; on API/transport errors, visibly fall back to secondary.

    Visibility: warning log + last_call / call_history / fallback_summary for
    run items, metrics.json, and the web UI.
    """

    def __init__(
        self,
        primary: LLMProvider,
        fallback: LLMProvider,
        *,
        primary_name: str,
        primary_model: str,
        fallback_name: str,
        fallback_model: str,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_name = primary_name
        self.primary_model = primary_model
        self.fallback_name = fallback_name
        self.fallback_model = fallback_model
        self.last_usage: dict[str, Any] | None = None
        self.last_call: dict[str, Any] | None = None
        self.call_history: list[dict[str, Any]] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        try:
            result = self.primary.complete_json(system, user)
            usage = getattr(self.primary, "last_usage", None)
            call = {
                "provider": self.primary_name,
                "model": getattr(self.primary, "model_label", self.primary_model),
                "fallback_used": False,
                "fallback_from": None,
                "fallback_reason": None,
            }
            self.last_usage = usage
            self.last_call = call
            self.call_history.append(dict(call))
            return result
        except Exception as primary_exc:
            if not is_fallback_worthy(primary_exc):
                raise
            reason = f"{type(primary_exc).__name__}: {primary_exc}"
            logger.warning(
                "LLM primary provider %s/%s failed (%s); falling back to %s/%s",
                self.primary_name,
                self.primary_model,
                reason[:300],
                self.fallback_name,
                self.fallback_model,
            )
            try:
                result = self.fallback.complete_json(system, user)
            except Exception as fallback_exc:
                raise RuntimeError(
                    "LLM primary and fallback both failed. "
                    f"Primary ({self.primary_name}/{self.primary_model}): {primary_exc}. "
                    f"Fallback ({self.fallback_name}/{self.fallback_model}): {fallback_exc}."
                ) from fallback_exc
            usage = getattr(self.fallback, "last_usage", None)
            call = {
                "provider": self.fallback_name,
                "model": getattr(self.fallback, "model_label", self.fallback_model),
                "fallback_used": True,
                "fallback_from": self.primary_name,
                "fallback_reason": reason[:500],
            }
            self.last_usage = usage
            self.last_call = call
            self.call_history.append(dict(call))
            return result

    def fallback_summary(self) -> dict[str, Any]:
        history = list(self.call_history)
        by_provider: dict[str, int] = {}
        fallback_count = 0
        reasons: list[str] = []
        for c in history:
            p = str(c.get("provider") or "unknown")
            by_provider[p] = by_provider.get(p, 0) + 1
            if c.get("fallback_used"):
                fallback_count += 1
                r = c.get("fallback_reason")
                if r:
                    reasons.append(str(r)[:200])
        return {
            "fallback_enabled": True,
            "fallback_provider": self.fallback_name,
            "fallback_model": self.fallback_model,
            "primary_provider": self.primary_name,
            "primary_model": self.primary_model,
            "llm_calls": len(history),
            "fallback_count": fallback_count,
            "calls_by_provider": by_provider,
            "fallback_reasons": reasons,
            "call_history": history,
        }



def provider_call_summary(provider: LLMProvider, *, settings: Settings | None = None) -> dict[str, Any]:
    """Normalize per-run LLM visibility fields for metrics.json / UI."""
    if isinstance(provider, FallbackLLMProvider):
        return provider.fallback_summary()
    history = list(getattr(provider, "call_history", []) or [])
    # If history empty but last_call exists, include it
    last = getattr(provider, "last_call", None)
    if not history and isinstance(last, dict):
        history = [dict(last)]
    by_provider: dict[str, int] = {}
    for c in history:
        p = str(c.get("provider") or "unknown")
        by_provider[p] = by_provider.get(p, 0) + 1
    primary = None
    model = None
    if settings is not None:
        primary = settings.provider_name
        try:
            model = settings.model
        except RuntimeError:
            model = None
    if primary is None and history:
        primary = history[0].get("provider")
        model = history[0].get("model")
    return {
        "fallback_enabled": False,
        "fallback_provider": None,
        "fallback_model": None,
        "primary_provider": primary,
        "primary_model": model,
        "llm_calls": len(history),
        "fallback_count": 0,
        "calls_by_provider": by_provider,
        "fallback_reasons": [],
        "call_history": history,
    }


def get_provider(
    settings: Settings | None = None,
    *,
    model_override: str | None = None,
) -> LLMProvider:
    settings = settings or get_settings()
    primary_view = ProviderSettingsView(
        settings, settings.provider_name, model_override=model_override
    )
    primary = OpenAICompatibleProvider(primary_view, model_override=model_override)

    fb_name = settings.fallback_provider_name
    if not fb_name:
        return primary

    # Require a configured key for the fallback provider; otherwise raise clearly
    # rather than silently skipping the configured fallback.
    fb_view = ProviderSettingsView(settings, fb_name, model_override=model_override)
    if not fb_view.api_key:
        raise RuntimeError(
            f"LLM_FALLBACK_PROVIDER={fb_name!r} is set but no API key is configured "
            f"for that provider. Set the matching *_API_KEY or clear LLM_FALLBACK_PROVIDER."
        )
    try:
        fb_model = fb_view.model
    except RuntimeError as exc:
        raise RuntimeError(
            f"LLM_FALLBACK_PROVIDER={fb_name!r} is set but its model env is missing: {exc}"
        ) from exc

    fallback = OpenAICompatibleProvider(fb_view, model_override=model_override)
    return FallbackLLMProvider(
        primary,
        fallback,
        primary_name=settings.provider_name,
        primary_model=primary.model_label,
        fallback_name=fb_name,
        fallback_model=fb_model if not model_override else model_override,
    )
