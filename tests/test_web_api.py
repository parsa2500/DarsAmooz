from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from darsamooz.web.app import create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    exports = tmp_path / "exports"
    exports.mkdir()
    from darsamooz import config

    original = config.get_settings

    def patched_get_settings():
        s = original()
        s.exports_dir = exports
        return s

    monkeypatch.setattr("darsamooz.config.get_settings", patched_get_settings)
    monkeypatch.setattr("darsamooz.web.jobs.get_settings", patched_get_settings)
    monkeypatch.setattr("darsamooz.web.app.get_settings", patched_get_settings)

    app = create_app()
    with TestClient(app) as c:
        yield c, exports


def test_meta(client):
    c, _ = client
    res = c.get("/api/meta")
    assert res.status_code == 200
    data = res.json()
    assert "book_title" in data
    assert len(data["chapters"]) >= 5
    assert data["chapters"][0]["number"] == 1


def test_index_serves_html(client):
    c, _ = client
    res = c.get("/")
    assert res.status_code == 200
    assert "درس‌آموز" in res.text


def test_generate_sample_and_export(client):
    c, exports = client
    res = c.post("/api/generate", json={"chapter": 1, "count": 3, "sample": True})
    assert res.status_code == 200, res.text
    job = res.json()
    job_id = job["id"]

    deadline = time.time() + 10
    final = None
    while time.time() < deadline:
        st = c.get(f"/api/jobs/{job_id}")
        assert st.status_code == 200
        final = st.json()
        if final["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.05)

    assert final is not None
    assert final["status"] == "succeeded", final
    assert final["question_count"] == 3
    assert (exports / "phys10-ch1.json").is_file()

    preview = c.get("/api/exports/phys10-ch1")
    assert preview.status_code == 200
    body = preview.json()
    assert body["count"] == 3
    assert len(body["questions"]) == 3
