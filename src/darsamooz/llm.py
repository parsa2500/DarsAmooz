from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from darsamooz.config import Settings, get_settings


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> Any:
        """Return parsed JSON from the model response."""


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible Chat Completions + Responses API (AgentRouter, etc.).

    No client impersonation headers. No silent model fallback — if the configured
    model fails, raise a clear error with the provider message.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise RuntimeError(
                "No API key set. Add AGENTROUTER_API_KEY (or OPENAI_API_KEY) to .env "
                "(see .env.example). Sample mode works without a key: "
                "`darsamooz generate --chapter 1 --sample`."
            )
        self._model = self.settings.model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "darsamooz/0.1 (+https://github.com/parsa2500/DarsAmooz)",
        }

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

    def _post_responses(self, client: httpx.Client, system: str, user: str) -> httpx.Response:
        url = self.settings.base_url.rstrip("/") + "/responses"
        payload = {
            "model": self._model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return client.post(url, headers=self._headers(), json=payload)

    def _extract_responses_text(self, data: dict) -> str:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"]
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                text = part.get("text") or part.get("value") or ""
                if text:
                    chunks.append(text)
        if not chunks:
            raise RuntimeError(
                f"Model {self._model!r}: Responses API returned no message text"
            )
        return "\n".join(chunks)

    def _format_error(self, resp: httpx.Response, *, via: str) -> str:
        body = (resp.text or "").strip()
        snippet = body[:800] if body else "(empty body)"
        return (
            f"LLM request failed for model {self._model!r} via {via}: "
            f"HTTP {resp.status_code}: {snippet}"
        )

    def complete_json(self, system: str, user: str) -> Any:
        with httpx.Client(timeout=180.0) as client:
            # Prefer Chat Completions; if the gateway requires Responses for this
            # same model, retry once on /responses (not a model switch).
            chat = self._post_chat(client, system, user)
            if chat.status_code < 400:
                data = chat.json()
                content = data["choices"][0]["message"]["content"]
                return _parse_json_payload(content)

            needs_responses = (
                "/v1/responses" in chat.text
                or "Responses API" in chat.text
                or "Unsupported parameter" in chat.text
            )
            if needs_responses:
                resp = self._post_responses(client, system, user)
                if resp.status_code < 400:
                    content = self._extract_responses_text(resp.json())
                    return _parse_json_payload(content)
                raise RuntimeError(self._format_error(resp, via="/v1/responses"))

            raise RuntimeError(self._format_error(chat, via="/v1/chat/completions"))


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
    return OpenAICompatibleProvider(settings)
