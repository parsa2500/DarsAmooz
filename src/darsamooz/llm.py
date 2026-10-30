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
    """OpenAI Chat Completions API (or any compatible base URL)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to .env (see .env.example) "
                "or export OPENAI_API_KEY. Sample mode works without a key."
            )

    def complete_json(self, system: str, user: str) -> Any:
        url = self.settings.openai_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
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
