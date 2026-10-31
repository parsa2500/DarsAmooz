from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from darsamooz import __version__
from darsamooz.config import get_settings
from darsamooz.ingest import PHYSICS10_CHAPTERS
from darsamooz.web.jobs import JobManager

_OPTION_LETTERS = ("الف", "ب", "ج", "د")


class GenerateBody(BaseModel):
    chapter: int = Field(..., ge=1, le=20)
    count: int = Field(10, ge=1, le=100)
    sample: bool = False


def project_root() -> Path:
    # src/darsamooz/web/app.py → repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def static_dir() -> Path:
    return project_root() / "web" / "static"


def create_app() -> FastAPI:
    root = project_root()
    jobs = JobManager(root)
    app = FastAPI(title="DarsAmooz", version=__version__)
    app.state.jobs = jobs
    app.state.root = root

    static = static_dir()
    if not static.is_dir():
        raise RuntimeError(f"Static UI missing: {static}")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static / "index.html")

    @app.get("/api/meta")
    def meta() -> dict:
        settings = get_settings()
        manifest_path = root / "data" / "manifests" / "physics10-riazi-110209.json"
        book_title = "فیزیک (۱) — پایه دهم"
        chapters = [{"number": n, "title": t} for n, t in PHYSICS10_CHAPTERS]
        if manifest_path.is_file():
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            book_title = data.get("title") or book_title
            if data.get("chapters"):
                chapters = [
                    {"number": c["number"], "title": c["title"]} for c in data["chapters"]
                ]
        has_key = bool(settings.api_key)
        provider = settings.provider_name
        model = None
        try:
            if has_key:
                model = settings.model
        except RuntimeError:
            model = None
        exports = settings.exports_dir
        if not exports.is_absolute():
            exports = root / exports
        return {
            "version": __version__,
            "book_title": book_title,
            "chapters": chapters,
            "provider": provider,
            "model": model,
            "has_api_key": has_key,
            "exports_dir": str(exports.resolve()),
            "sample_chapter_only": 1,
        }

    @app.post("/api/generate")
    def generate(payload: Annotated[GenerateBody, Body()]) -> dict:
        known = {n for n, _ in PHYSICS10_CHAPTERS}
        if payload.chapter not in known:
            raise HTTPException(400, detail=f"فصل نامعتبر: {payload.chapter}")
        if payload.sample and payload.chapter != 1:
            raise HTTPException(400, detail="حالت نمونه فقط برای فصل ۱ است.")
        try:
            job = jobs.start(payload.chapter, payload.count, payload.sample)
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return job.to_dict()

    @app.get("/api/jobs/active")
    def active_job() -> dict:
        job = jobs.active()
        if not job:
            return {"job": None}
        return {"job": job.to_dict()}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, detail="جاب پیدا نشد.")
        return job.to_dict()

    @app.post("/api/cancel")
    def cancel() -> dict:
        job = jobs.cancel()
        if not job:
            return {"ok": True, "job": None}
        return {"ok": True, "job": job.to_dict()}

    @app.get("/api/exports/{prefix}")
    def export_preview(prefix: str) -> dict:
        if not prefix.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(400, detail="پیشوند نامعتبر.")
        settings = get_settings()
        exports = settings.exports_dir
        if not exports.is_absolute():
            exports = root / exports
        json_path = exports / f"{prefix}.json"
        if not json_path.is_file():
            raise HTTPException(404, detail="فایل JSON پیدا نشد.")
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        questions = payload.get("questions") or []
        for q in questions:
            idx = q.get("correct_index", 0)
            q["correct_letter"] = _OPTION_LETTERS[idx] if 0 <= idx < 4 else "?"
        return {
            "prefix": prefix,
            "count": payload.get("count", len(questions)),
            "questions": questions,
            "paths": {
                "json": str(json_path),
                "csv": str(exports / f"{prefix}.csv"),
                "md": str(exports / f"{prefix}.md"),
            },
        }

    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app
