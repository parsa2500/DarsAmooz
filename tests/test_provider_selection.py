from __future__ import annotations

import pytest

from darsamooz.config import (
    list_provider_catalog,
    normalize_provider_id,
    resolve_llm_selection,
)
from darsamooz.web.app import create_app

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient


def test_normalize_provider_id():
    assert normalize_provider_id("Gemini") == "gemini"
    assert normalize_provider_id(None) is None
    with pytest.raises(ValueError):
        normalize_provider_id("anthropic")


def test_list_provider_catalog_shape():
    catalog = list_provider_catalog()
    ids = {p["id"] for p in catalog}
    assert ids == {"gemini", "openrouter", "openai"}
    assert any(p.get("is_default") for p in catalog)
    for p in catalog:
        assert "has_api_key" in p
        assert "model_suggestions" in p


def test_resolve_llm_selection_override_provider():
    pid, mid, view = resolve_llm_selection(provider="gemini")
    assert pid == "gemini"
    assert view.provider_name == "gemini"
    # model may come from env; if key+model present it should resolve
    if view.api_key:
        assert mid


def test_meta_includes_providers():
    app = create_app()
    with TestClient(app) as client:
        res = client.get("/api/meta")
        assert res.status_code == 200
        data = res.json()
        assert "providers" in data
        assert len(data["providers"]) == 3
