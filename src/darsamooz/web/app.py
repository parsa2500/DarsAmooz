from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from darsamooz import __version__
from darsamooz.config import (
    get_settings,
    list_provider_catalog,
    normalize_provider_id,
)
from darsamooz.ingest import (
    PHYSICS10_CHAPTERS,
    PHYSICS10_SLUG,
    ingest_document_pdf,
    list_book_chapters,
    list_document_sources,
    make_document_slug,
)
from darsamooz.balance import PATTERN_LABEL_FA
from darsamooz.models import QuestionPattern, ReviewStatus
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
_MAX_UPLOAD_BYTES = 40 * 1024 * 1024
_SAFE_UPLOAD_NAME = re.compile(r"[^a-zA-Z0-9._\u0600-\u06FF-]+")


class GenerateBody(BaseModel):
    chapter: int = Field(..., ge=1, le=20)
    count: int = Field(10, ge=1, le=100)
    sample: bool = False
    book_slug: str = PHYSICS10_SLUG
    verify: bool = True
    style_mine: bool = False
    difficulty_mix: str | None = None
    pattern_mix: str | None = None
    balance_answers: bool = True
    export_profile: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    use_pattern_pack: bool = False


class TopicPatch(BaseModel):
    subject: str | None = None
    grade: str | None = None
    track: str | None = None
    book: str | None = None
    chapter: str | None = None
    summary_fa: str | None = None
    topics: list[str] | None = None
    key_concepts: list[str] | None = None
    exam_targets: list[str] | None = None
    search_queries: list[str] | None = None
    confirm: bool = True


class AnalyzeBody(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None


class MineBody(BaseModel):
    backend: str = "auto"
    max_sources: int = Field(8, ge=1, le=15)
    max_queries: int | None = Field(default=None, ge=1, le=20)
    llm_only: bool = False
    fetch_pages: bool = True
    llm_provider: str | None = None
    llm_model: str | None = None


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


def _resolved_settings(root: Path):
    settings = get_settings()
    for attr in (
        "data_dir",
        "textbooks_dir",
        "uploads_dir",
        "extracted_dir",
        "exports_dir",
        "manifests_dir",
    ):
        p = getattr(settings, attr)
        if not Path(p).is_absolute():
            setattr(settings, attr, (root / p).resolve())
    return settings


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
        settings = _resolved_settings(root)
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
        fb = None
        try:
            fb = settings.fallback_provider_name
        except RuntimeError:
            fb = None
        providers = list_provider_catalog(settings)
        sources = list_document_sources(settings)
        # Prefer manifest titles for the builtin physics source when present
        for src in sources:
            if src["book_slug"] == PHYSICS10_SLUG:
                src["title"] = book_title
                src["chapters"] = chapters
        return {
            "version": __version__,
            "book_title": book_title,
            "chapters": chapters,
            "default_book_slug": PHYSICS10_SLUG,
            "sources": sources,
            "provider": provider,
            "model": model,
            "fallback_provider": fb,
            "has_api_key": has_key,
            "providers": providers,
            "exports_dir": str(exports.resolve()),
            "sample_chapter_only": 1,
            "export_profiles": [{**p, "label": p.get("name") or p.get("id")} for p in profiles],
            "default_export_profile": DEFAULT_PROFILE_ID,
            "verify_default": True,
            "max_upload_bytes": _MAX_UPLOAD_BYTES,
            "has_tavily_key": settings.has_tavily_key,
            "style_mine_ready": bool(settings.api_key),
            "question_patterns": [
                {"id": p.value, "label_fa": PATTERN_LABEL_FA[p]} for p in QuestionPattern
            ],
        }

    @app.get("/api/documents")
    def documents() -> dict:
        settings = _resolved_settings(root)
        return {"sources": list_document_sources(settings)}

    @app.post("/api/documents")
    async def upload_document(file: UploadFile = File(...)) -> dict:
        settings = _resolved_settings(root)
        filename = Path(file.filename or "document.pdf").name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(400, detail="فقط فایل PDF پذیرفته می‌شود.")
        raw = await file.read()
        if not raw:
            raise HTTPException(400, detail="فایل خالی است.")
        if len(raw) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                400,
                detail=f"حجم فایل بیش از {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB است.",
            )
        safe_name = _SAFE_UPLOAD_NAME.sub("-", filename).strip("-_.") or "document.pdf"
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        book_slug = make_document_slug(filename)
        uploads = Path(settings.uploads_dir)
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / f"{book_slug}.pdf"
        dest.write_bytes(raw)
        try:
            chapters, section_counts = ingest_document_pdf(
                dest,
                book_slug,
                title=Path(filename).stem or safe_name,
                settings=settings,
            )
        except RuntimeError as exc:
            raise HTTPException(500, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, detail=f"خواندن PDF ناموفق: {exc}") from exc
        return {
            "book_slug": book_slug,
            "title": Path(filename).stem or book_slug,
            "source_filename": filename,
            "chapters": [{"number": c.number, "title": c.title} for c in chapters],
            "section_counts": {str(k): v for k, v in section_counts.items()},
        }

    @app.post("/api/generate")
    def generate(payload: Annotated[GenerateBody, Body()]) -> dict:
        settings = _resolved_settings(root)
        book_slug = (payload.book_slug or PHYSICS10_SLUG).strip() or PHYSICS10_SLUG
        chapters = list_book_chapters(book_slug, settings)
        known = {int(c["number"]) for c in chapters}
        if not known and book_slug == PHYSICS10_SLUG:
            known = {n for n, _ in PHYSICS10_CHAPTERS}
        if payload.chapter not in known:
            raise HTTPException(400, detail=f"فصل نامعتبر: {payload.chapter}")
        if payload.sample and (book_slug != PHYSICS10_SLUG or payload.chapter != 1):
            raise HTTPException(
                400,
                detail="حالت نمونه فقط برای کتاب فیزیک ۱۰، فصل ۱ است.",
            )
        if payload.style_mine and payload.sample:
            raise HTTPException(
                400,
                detail="الگوبرداری وب با حالت نمونه سازگار نیست.",
            )
        if payload.style_mine and not settings.api_key:
            raise HTTPException(
                400,
                detail="برای الگوبرداری وب همان کلید LLM (OpenRouter/Gemini/OpenAI) لازم است.",
            )
        if payload.use_pattern_pack:
            if payload.sample:
                raise HTTPException(400, detail="بسته الگو با حالت نمونه سازگار نیست.")
            from darsamooz.patterns import load_pattern_pack

            if not load_pattern_pack(book_slug, payload.chapter, settings):
                raise HTTPException(
                    400,
                    detail="برای این منبع/فصل هنوز بسته الگو ساخته نشده (مرحله ۲ و ۳).",
                )
        llm_provider = None
        llm_model = (payload.llm_model or "").strip() or None
        if payload.llm_provider:
            try:
                llm_provider = normalize_provider_id(payload.llm_provider)
            except ValueError as exc:
                raise HTTPException(400, detail=str(exc)) from exc
        try:
            job = jobs.start(
                payload.chapter,
                payload.count,
                payload.sample,
                book_slug=book_slug,
                verify=payload.verify,
                style_mine=payload.style_mine,
                difficulty_mix=payload.difficulty_mix,
                pattern_mix=payload.pattern_mix,
                balance_answers=payload.balance_answers,
                export_profile=payload.export_profile,
                llm_provider=llm_provider,
                llm_model=llm_model,
                use_pattern_pack=payload.use_pattern_pack,
            )
        except RuntimeError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        return job.to_dict()

    # ------------------------------------------------------------ handout flow
    def _check_source(slug: str, chapter: int, settings) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", slug or ""):
            raise HTTPException(400, detail="شناسه منبع نامعتبر.")
        known = {int(c["number"]) for c in list_book_chapters(slug, settings)}
        if chapter not in known:
            raise HTTPException(404, detail=f"فصل {chapter} برای این منبع نیست.")

    def _llm(provider: str | None, model: str | None, settings):
        from darsamooz.llm import get_provider

        try:
            pid = normalize_provider_id(provider) if provider else None
            return get_provider(
                settings, provider_override=pid, model_override=(model or "").strip() or None
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, detail=str(exc)) from exc

    @app.get("/api/handout/{slug}/{chapter}")
    def handout_state(slug: str, chapter: int) -> dict:
        from darsamooz.patterns import load_pattern_pack, load_web_sources
        from darsamooz.topic import load_topic_profile
        from darsamooz.web_search import google_cse_configured, max_queries_per_run, tavily_configured

        settings = _resolved_settings(root)
        _check_source(slug, chapter, settings)
        return {
            "search": {
                "tavily_configured": tavily_configured(),
                "google_cse_configured": google_cse_configured(),
                "max_queries": max_queries_per_run(),
            },
            "topic_profile": load_topic_profile(slug, chapter, settings),
            "web_sources": load_web_sources(slug, chapter, settings),
            "pattern_pack": load_pattern_pack(slug, chapter, settings),
        }

    @app.post("/api/handout/{slug}/{chapter}/analyze")
    def handout_analyze(slug: str, chapter: int, payload: Annotated[AnalyzeBody, Body()] = AnalyzeBody()) -> dict:
        from darsamooz.topic import analyze_topic

        settings = _resolved_settings(root)
        _check_source(slug, chapter, settings)
        provider = _llm(payload.llm_provider, payload.llm_model, settings)
        try:
            profile = analyze_topic(slug, chapter, provider=provider, settings=settings)
        except FileNotFoundError as exc:
            raise HTTPException(404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, detail=f"تشخیص موضوع ناموفق: {exc}") from exc
        return {"topic_profile": profile}

    @app.put("/api/handout/{slug}/{chapter}/topic")
    def handout_topic_save(slug: str, chapter: int, payload: Annotated[TopicPatch, Body()]) -> dict:
        from darsamooz.topic import update_topic_profile

        settings = _resolved_settings(root)
        _check_source(slug, chapter, settings)
        patch = payload.model_dump(exclude_none=True)
        confirm = bool(patch.pop("confirm", True))
        profile = update_topic_profile(slug, chapter, patch, confirm=confirm, settings=settings)
        return {"topic_profile": profile}

    @app.post("/api/handout/{slug}/{chapter}/mine")
    def handout_mine(slug: str, chapter: int, payload: Annotated[MineBody, Body()] = MineBody()) -> dict:
        from darsamooz.patterns import load_web_sources, mine_web_patterns
        from darsamooz.topic import load_topic_profile
        from darsamooz.web_search import VALID_BACKENDS, WebSearchError

        settings = _resolved_settings(root)
        _check_source(slug, chapter, settings)
        if payload.backend not in VALID_BACKENDS:
            raise HTTPException(400, detail=f"backend نامعتبر: {payload.backend}")
        profile = load_topic_profile(slug, chapter, settings)
        if not profile:
            raise HTTPException(400, detail="اول موضوع جزوه را تشخیص دهید (مرحله ۱).")
        provider = _llm(payload.llm_provider, payload.llm_model, settings)
        logs: list[str] = []
        try:
            pack = mine_web_patterns(
                slug,
                chapter,
                provider=provider,
                settings=settings,
                profile=profile,
                backend=payload.backend,
                max_sources=payload.max_sources,
                max_queries=payload.max_queries,
                llm_only=payload.llm_only,
                fetch_pages=payload.fetch_pages,
                on_log=logs.append,
            )
        except WebSearchError as exc:
            return {
                "ok": False,
                "search_failed": True,
                "error": str(exc),
                "attempts": exc.attempts,
                "logs": logs,
                "can_continue_llm_only": True,
            }
        except (RuntimeError, FileNotFoundError) as exc:
            raise HTTPException(502, detail=f"استخراج الگو ناموفق: {exc}") from exc
        return {
            "ok": True,
            "pattern_pack": pack,
            "web_sources": load_web_sources(slug, chapter, settings),
            "logs": logs,
        }

    @app.put("/api/handout/{slug}/{chapter}/patterns")
    def handout_patterns_save(slug: str, chapter: int, payload: Annotated[dict, Body()]) -> dict:
        from darsamooz.patterns import update_pattern_pack

        settings = _resolved_settings(root)
        _check_source(slug, chapter, settings)
        try:
            pack = update_pattern_pack(slug, chapter, payload, settings=settings)
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return {"pattern_pack": pack}


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
