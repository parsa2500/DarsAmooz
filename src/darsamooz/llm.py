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
    """OpenAI Chat Completions API (AgentRouter and other compatible bases)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise RuntimeError(
                "No API key set. Add AGENTROUTER_API_KEY (or OPENAI_API_KEY) to .env "
                "(see .env.example). Sample mode works without a key: "
                "`darsamooz generate --chapter 1 --sample`."
            )

    def complete_json(self, system: str, user: str) -> Any:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                # Do not include request headers (Bearer) in errors
                raise RuntimeError(
                    f"LLM HTTP {resp.status_code}: {resp.text[:500]}"
                )
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _parse_json_payload(content)


def _parse_json_payload(content: str) -> Any:
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", content)
        if not m:
            raise
        return json.loads(m.group(0))


def get_provider(settings: Settings | None = None) -> LLMProvider:
    return OpenAICompatibleProvider(settings)
