from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import httpx

from darsamooz.config import Settings, get_settings

# AgentRouter fingerprints HTTP clients and rejects generic ones.
# These headers match the commonly-working Claude-Code wire image.
_AGENTROUTER_HEADERS = {
    "User-Agent": "claude-cli/1.0.108 (external, cli)",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "anthropic-dangerous-direct-browser-access": "true",
    "x-app": "cli",
    "x-stainless-lang": "js",
    "x-stainless-package-version": "0.55.1",
    "x-stainless-os": "Linux",
    "x-stainless-arch": "x64",
    "x-stainless-runtime": "node",
    "x-stainless-runtime-version": "v22.0.0",
}


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> Any:
        """Return parsed JSON from the model response."""


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible Chat Completions + Responses API (AgentRouter, etc.)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.api_key:
            raise RuntimeError(
                "No API key set. Add AGENTROUTER_API_KEY (or OPENAI_API_KEY) to .env "
                "(see .env.example). Sample mode works without a key: "
                "`darsamooz generate --chapter 1 --sample`."
            )
        self._model = self.settings.model
        self._resolved_model: str | None = None

    def _is_agentrouter(self) -> bool:
        host = urlparse(self.settings.base_url).hostname or ""
        return host.endswith("agentrouter.org")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._is_agentrouter():
            headers.update(_AGENTROUTER_HEADERS)
        return headers

    def _list_models(self, client: httpx.Client) -> list[str]:
        url = self.settings.base_url.rstrip("/") + "/models"
        resp = client.get(url, headers=self._headers())
        if resp.status_code >= 400:
            return []
        data = resp.json()
        items = data.get("data", data if isinstance(data, list) else [])
        return [i["id"] for i in items if isinstance(i, dict) and "id" in i]

    def _extract_responses_text(self, data: dict) -> str:
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"]
        chunks: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                text = part.get("text") or part.get("value") or ""
                if text:
                    chunks.append(text)
        if not chunks:
            raise RuntimeError("Responses API returned no message text")
        return "\n".join(chunks)

    def _post_responses(self, client: httpx.Client, model: str, system: str, user: str) -> httpx.Response:
        url = self.settings.base_url.rstrip("/") + "/responses"
        payload = {
            "model": model,
            "input": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Note: some AgentRouter/OpenAI Responses models reject `temperature`.
        }
        return client.post(url, headers=self._headers(), json=payload)

    def _post_chat(self, client: httpx.Client, model: str, system: str, user: str) -> httpx.Response:
        url = self.settings.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": model,
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        return client.post(url, headers=self._headers(), json=payload)

    def _error_needs_responses(self, body: str) -> bool:
        return "/v1/responses" in body or "Responses API" in body

    def _error_no_channel(self, body: str) -> bool:
        return "无可用渠道" in body or "no available" in body.lower()

    def complete_json(self, system: str, user: str) -> Any:
        models_to_try = [self._resolved_model or self._model]
        with httpx.Client(timeout=180.0) as client:
            # First attempt; may switch API or model
            last_err = ""
            for attempt in range(6):
                model = models_to_try[-1]
                # Prefer Responses on AgentRouter (their listed models often require it)
                prefer_responses = self._is_agentrouter() or attempt > 0
                if prefer_responses:
                    resp = self._post_responses(client, model, system, user)
                else:
                    resp = self._post_chat(client, model, system, user)

                if resp.status_code < 400:
                    self._resolved_model = model
                    data = resp.json()
                    if prefer_responses or "output" in data:
                        content = self._extract_responses_text(data)
                    else:
                        content = data["choices"][0]["message"]["content"]
                    return _parse_json_payload(content)

                body = resp.text
                last_err = f"LLM HTTP {resp.status_code}: {body[:500]}"

                if self._error_needs_responses(body) and not prefer_responses:
                    continue

                if (
                    self._error_no_channel(body)
                    or "Unsupported parameter" in body
                    or "content-blocked" in body
                    or resp.status_code in (400, 503)
                ):
                    available = self._list_models(client)
                    next_models = [m for m in available if m not in models_to_try]
                    if next_models:
                        models_to_try.append(next_models[0])
                        continue
                    if "content-blocked" in body and not prefer_responses:
                        continue
                break

            raise RuntimeError(last_err or "LLM request failed")


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
