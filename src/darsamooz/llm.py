from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from darsamooz.config import Settings, get_settings

_VALID_PROVIDERS = frozenset({"gemini", "openrouter", "openai"})


class LLMProvider(ABC):
    last_usage: dict[str, int | None] | None = None

    @abstractmethod
    def complete_json(self, system: str, user: str) -> Any:
        """Return parsed JSON from the model response.

        Implementations may set ``last_usage`` with prompt/completion token counts.
        """


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible Chat Completions (Gemini, OpenRouter, OpenAI, etc.).

    No client impersonation beyond OpenRouter's documented app headers.
    No silent model fallback — if the configured model fails, raise clearly.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        provider = self.settings.provider_name
        if provider not in _VALID_PROVIDERS:
            raise RuntimeError(
                f"Unsupported LLM_PROVIDER={provider!r}. "
                "Use 'gemini', 'openrouter', or 'openai'."
            )
        if not self.settings.api_key:
            key_hint = {
                "gemini": "GEMINI_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
                "openai": "OPENAI_API_KEY",
            }[provider]
            raise RuntimeError(
                f"No {key_hint} set. Add it to local .env (see .env.example). "
                "Sample mode works without a key: "
                "`darsamooz generate --chapter 1 --sample`."
            )
        # Accessing .model raises if unset — intentional (no silent default).
        self._model = self.settings.model
        self._provider = provider
        self.last_usage = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "darsamooz/0.1 (+https://github.com/parsa2500/DarsAmooz)",
        }
        if self._provider == "openrouter":
            # OpenRouter optional attribution headers (not secrets).
            headers["HTTP-Referer"] = "https://github.com/parsa2500/DarsAmooz"
            headers["X-Title"] = "DarsAmooz"
        return headers

    def _post_chat(self, client: httpx.Client, system: str, user: str) -> httpx.Response:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self._model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return client.post(url, headers=self._headers(), json=payload)

    def _format_error(self, resp: httpx.Response, *, via: str) -> str:
        body = (resp.text or "").strip()
        snippet = body[:800] if body else "(empty body)"
        return (
            f"LLM request failed for provider={self._provider!r} model={self._model!r} "
            f"via {via}: HTTP {resp.status_code}: {snippet}"
        )

    def complete_json(self, system: str, user: str) -> Any:
        with httpx.Client(timeout=180.0) as client:
            chat = self._post_chat(client, system, user)
            if chat.status_code < 400:
                data = chat.json()
                self.last_usage = _extract_usage(data)
                try:
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError) as exc:
                    raise RuntimeError(
                        f"Unexpected chat/completions payload for model {self._model!r}: "
                        f"{str(data)[:400]}"
                    ) from exc
                if not content:
                    raise RuntimeError(
                        f"Model {self._model!r} returned empty content "
                        f"(finish_reason={data['choices'][0].get('finish_reason')!r})"
                    )
                return _parse_json_payload(content)

            self.last_usage = None
            raise RuntimeError(self._format_error(chat, via="/chat/completions"))


def _extract_usage(data: dict[str, Any]) -> dict[str, int | None]:
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    def _as_int(key: str) -> int | None:
        val = usage.get(key)
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    return {
        "prompt_tokens": _as_int("prompt_tokens"),
        "completion_tokens": _as_int("completion_tokens"),
        "total_tokens": _as_int("total_tokens"),
    }


def _parse_json_payload(content: str) -> Any:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", content)
        if not m:
            raise
        return json.loads(m.group(0))


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    return OpenAICompatibleProvider(settings)
