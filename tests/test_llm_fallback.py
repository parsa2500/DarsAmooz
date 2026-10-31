from __future__ import annotations

import logging

import httpx
import pytest

from darsamooz.llm import (
    FallbackLLMProvider,
    OpenAICompatibleProvider,
    get_provider,
    is_fallback_worthy,
    provider_call_summary,
)


class BoomProvider:
    def __init__(self, exc: BaseException):
        self.exc = exc
        self.calls = 0
        self.last_usage = None
        self.last_call = None
        self.call_history: list = []
        self.model_label = "boom-model"

    def complete_json(self, system: str, user: str):
        self.calls += 1
        raise self.exc


class OkProvider:
    def __init__(self, payload=None, label: str = "ok", model: str = "ok-model"):
        self.payload = payload or {"questions": []}
        self.calls = 0
        self.last_usage = {"prompt_tokens": 3, "completion_tokens": 5}
        self.last_call = None
        self.call_history: list = []
        self.provider_label = label
        self.model_label = model

    def complete_json(self, system: str, user: str):
        self.calls += 1
        self.last_call = {
            "provider": self.provider_label,
            "model": self.model_label,
            "fallback_used": False,
            "fallback_from": None,
            "fallback_reason": None,
        }
        self.call_history.append(dict(self.last_call))
        return dict(self.payload)


def test_is_fallback_worthy_http_and_timeout():
    assert is_fallback_worthy(RuntimeError("LLM request failed HTTP 429: quota"))
    assert is_fallback_worthy(RuntimeError("LLM request failed HTTP 503: unavailable"))
    assert is_fallback_worthy(httpx.TimeoutException("timed out"))
    assert is_fallback_worthy(ConnectionError("refused"))
    # Local validation / bad JSON should NOT trigger fallback
    assert not is_fallback_worthy(RuntimeError("LLM did not return valid JSON: {"))
    assert not is_fallback_worthy(RuntimeError("LLM returned empty content"))
    assert not is_fallback_worthy(ValueError("not an api error"))


def test_fallback_on_429_uses_secondary(caplog):
    primary = BoomProvider(RuntimeError("LLM request failed HTTP 429: RESOURCE_EXHAUSTED"))
    secondary = OkProvider({"ok": True}, label="openrouter", model="fb-model")
    fb = FallbackLLMProvider(
        primary,
        secondary,
        primary_name="gemini",
        primary_model="g-model",
        fallback_name="openrouter",
        fallback_model="fb-model",
    )
    with caplog.at_level(logging.WARNING):
        out = fb.complete_json("sys", "user")
    assert out == {"ok": True}
    assert primary.calls == 1
    assert secondary.calls == 1
    assert fb.last_call["fallback_used"] is True
    assert fb.last_call["provider"] == "openrouter"
    assert fb.last_call["fallback_from"] == "gemini"
    assert "429" in (fb.last_call["fallback_reason"] or "")
    assert fb.fallback_summary()["fallback_count"] == 1
    assert "falling back" in caplog.text.lower() or "fallback" in caplog.text.lower()


def test_fallback_both_fail_raises_clear_error():
    primary = BoomProvider(RuntimeError("LLM request failed HTTP 500: boom"))
    secondary = BoomProvider(RuntimeError("LLM request failed HTTP 502: also boom"))
    fb = FallbackLLMProvider(
        primary,
        secondary,
        primary_name="gemini",
        primary_model="g",
        fallback_name="openrouter",
        fallback_model="o",
    )
    with pytest.raises(RuntimeError, match="primary and fallback both failed"):
        fb.complete_json("s", "u")


def test_non_api_error_does_not_fallback():
    primary = BoomProvider(RuntimeError("LLM did not return valid JSON: nope"))
    secondary = OkProvider()
    fb = FallbackLLMProvider(
        primary,
        secondary,
        primary_name="gemini",
        primary_model="g",
        fallback_name="openrouter",
        fallback_model="o",
    )
    with pytest.raises(RuntimeError, match="valid JSON"):
        fb.complete_json("s", "u")
    assert secondary.calls == 0


def test_primary_success_no_fallback():
    primary = OkProvider({"a": 1}, label="gemini", model="g")
    secondary = OkProvider({"a": 2}, label="openrouter", model="o")
    fb = FallbackLLMProvider(
        primary,
        secondary,
        primary_name="gemini",
        primary_model="g",
        fallback_name="openrouter",
        fallback_model="o",
    )
    out = fb.complete_json("s", "u")
    assert out == {"a": 1}
    assert secondary.calls == 0
    assert fb.last_call["fallback_used"] is False
    assert fb.fallback_summary()["fallback_count"] == 0


def test_get_provider_builds_fallback_when_env_set(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "ok")
    monkeypatch.setenv("OPENROUTER_MODEL", "or-test")
    # Clear settings cache side effects by constructing fresh
    from darsamooz.config import Settings

    settings = Settings()
    provider = get_provider(settings)
    assert isinstance(provider, FallbackLLMProvider)
    assert provider.primary_name == "gemini"
    assert provider.fallback_name == "openrouter"
    summary = provider_call_summary(provider, settings=settings)
    assert summary["fallback_enabled"] is True
    assert summary["fallback_provider"] == "openrouter"


def test_get_provider_no_fallback_when_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "none")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from darsamooz.config import Settings

    provider = get_provider(Settings())
    assert isinstance(provider, OpenAICompatibleProvider)
    assert not isinstance(provider, FallbackLLMProvider)


def test_get_provider_missing_fallback_key_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gk")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL", "or-test")
    from darsamooz.config import Settings

    with pytest.raises(RuntimeError, match="no API key"):
        get_provider(Settings())
