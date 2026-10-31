from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from darsamooz import __version__
from darsamooz.config import get_settings
from darsamooz.ingest import PHYSICS10_CHAPTERS
from darsamooz.models import ReviewStatus
from darsamooz.profiles import DEFAULT_PROFILE_ID, list_profiles
from darsamooz.review import (
    apply_review_edit,
    filter_by_review_status,
    load_questions_payload,
    reexport_questions,
    review_summary,
    save_questions_payload,
)
from darsamooz.web.jobs import JobManager

_OPTION_LETTERS = ("الف", "ب", "ج", "د")


class GenerateBody(BaseModel):
    chapter: int = Field(..., ge=1, le=20)
    count: int = Field(10, ge=1, le=100)
    sample: bool = False
    verify: bool = True
    difficulty_mix: str | None = None
    balance_answers: bool = True
    export_profile: str | None = None


class ReviewBody(BaseModel):
    review_status: str | None = None
    stem: str | None = None
    explanation: str | None = None
    difficulty: str | None = None
    correct_index: int | None = Field(default=None, ge=0, le=3)
    options: list[str] | None = None


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def static_dir() -> Path:
    return project_root() / "web" / "static"


def _exports_dir(root: Path) -> Path:
    settings = get_settings()
    exports = settings.exports_dir
    if not exports.is_absolute():
        exports = root / exports
    return exports


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
        exports = _exports_dir(root)
        profiles = list_profiles(settings)
        return {
            "version": __version__,
            "book_title": book_title,
            "chapters": chapters,
            "provider": provider,
            "model": model,
            "has_api_key": has_key,
            "exports_dir": str(exports.resolve()),
            "sample_chapter_only": 1,
            "export_profiles": [{**p, "label": p.get("name") or p.get("id")} for p in profiles],
            "default_export_profile": DEFAULT_PROFILE_ID,
            "verify_default": True,
        }

    @app.post("/api/generate")
    def generate(payload: Annotated[GenerateBody, Body()]) -> dict:
        known = {n for n, _ in PHYSICS10_CHAPTERS}
        if payload.chapter not in known:
            raise HTTPException(400, detail=f"فصل نامعتبر: {payload.chapter}")
        if payload.sample and payload.chapter != 1:
            raise HTTPException(400, detail="حالت نمونه فقط برای فصل ۱ است.")
        try:
            job = jobs.start(
                payload.chapter,
                payload.count,
                payload.sample,
                verify=payload.verify,
                difficulty_mix=payload.difficulty_mix,
                balance_answers=payload.balance_answers,
                export_profile=payload.export_profile,
            )
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

    def _load_export(prefix: str) -> tuple[Path, dict[str, Any], list]:
        if not prefix.replace("-", "").replace("_", "").replace(".", "").isalnum():
            raise HTTPException(400, detail="پیشوند نامعتبر.")
        exports = _exports_dir(root)
        json_path = exports / f"{prefix}.json"
        if not json_path.is_file():
            raise HTTPException(404, detail="فایل JSON پیدا نشد.")
        meta, questions = load_questions_payload(json_path)
        return json_path, meta, questions

    @app.get("/api/exports/{prefix}")
    def export_preview(prefix: str, approved_only: bool = False) -> dict:
        json_path, meta, questions = _load_export(prefix)
        if approved_only:
            questions = filter_by_review_status(questions, approved_only=True)
        payload_questions = []
        for q in questions:
            d = q.model_dump(mode="json")
            idx = d.get("correct_index", 0)
            d["correct_letter"] = _OPTION_LETTERS[idx] if 0 <= idx < 4 else "?"
            payload_questions.append(d)
        exports = json_path.parent
        _, all_questions = load_questions_payload(json_path)
        for q in payload_questions:
            refs = q.get("source_refs") or []
            q["source_quote"] = " / ".join(str(r) for r in refs if str(r).strip())
            q["needs_review"] = q.get("review_status") == "needs_review"
        return {
            "prefix": prefix,
            "count": len(payload_questions),
            "questions": payload_questions,
            "review_summary": review_summary(all_questions),
            "metrics_note": meta,
            "paths": {
                "json": str(json_path),
                "csv": str(exports / f"{prefix}.csv"),
                "md": str(exports / f"{prefix}.md"),
            },
        }

    @app.patch("/api/exports/{prefix}/questions/{question_id}")
    def review_question(
        prefix: str,
        question_id: str,
        payload: Annotated[ReviewBody, Body()],
    ) -> dict:
        json_path, meta, questions = _load_export(prefix)
        idx = next((i for i, q in enumerate(questions) if q.id == question_id), None)
        if idx is None:
            raise HTTPException(404, detail="سؤال پیدا نشد.")
        patch: dict[str, Any] = {}
        raw = payload.model_dump(exclude_none=True)
        for key, value in raw.items():
            patch[key] = value
        if "review_status" in patch:
            try:
                ReviewStatus(str(patch["review_status"]).lower())
            except ValueError as exc:
                raise HTTPException(400, detail="وضعیت بازبینی نامعتبر") from exc
        try:
            questions[idx] = apply_review_edit(questions[idx], patch)
        except Exception as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        save_questions_payload(json_path, questions, meta=meta)
        reexport_questions(
            questions,
            json_path=json_path,
            csv_path=json_path.with_suffix(".csv"),
            md_path=json_path.with_suffix(".md"),
        )
        q = questions[idx].model_dump(mode="json")
        q["correct_letter"] = _OPTION_LETTERS[questions[idx].correct_index]
        return {
            "ok": True,
            "question": q,
            "review_summary": review_summary(questions),
        }

    @app.post("/api/exports/{prefix}/export-approved")
    def export_approved(prefix: str) -> dict:
        json_path, _meta, questions = _load_export(prefix)
        approved = filter_by_review_status(questions, approved_only=True)
        out_prefix = json_path.parent / f"{prefix}.approved"
        if not approved:
            raise HTTPException(400, detail="هیچ سؤال تأییدشده‌ای نیست.")
        paths = reexport_questions(
            approved,
            json_path=Path(str(out_prefix) + ".json"),
            csv_path=Path(str(out_prefix) + ".csv"),
            md_path=Path(str(out_prefix) + ".md"),
        )
        return {"ok": True, "count": len(approved), "paths": {k: str(v) for k, v in paths.items()}}

    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    return app
