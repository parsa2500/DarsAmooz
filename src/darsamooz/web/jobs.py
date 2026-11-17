from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.generate import (
    generate_from_chapter_with_run,
    load_sample_questions,
    persist_generation,
    persist_sample_run,
)
from darsamooz.ingest import PHYSICS10_SLUG, export_prefix_for


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class Job:
    id: str
    chapter: int
    count: int
    sample: bool
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
    status: JobStatus = JobStatus.queued
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    prefix: str | None = None
    paths: dict[str, str] = field(default_factory=dict)
    question_count: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def log(self, message: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {message}")
        if len(self.logs) > 80:
            self.logs = self.logs[-80:]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chapter": self.chapter,
            "count": self.count,
            "sample": self.sample,
            "book_slug": self.book_slug,
            "verify": self.verify,
            "style_mine": self.style_mine,
            "difficulty_mix": self.difficulty_mix,
            "pattern_mix": self.pattern_mix,
            "balance_answers": self.balance_answers,
            "export_profile": self.export_profile,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "use_pattern_pack": self.use_pattern_pack,
            "status": self.status.value,
            "logs": list(self.logs),
            "error": self.error,
            "prefix": self.prefix,
            "paths": dict(self.paths),
            "question_count": self.question_count,
            "metrics": dict(self.metrics),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    """In-process background jobs for generate (one active job at a time)."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._active_id: str | None = None
        self._thread: threading.Thread | None = None

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self) -> Job | None:
        if self._active_id:
            return self._jobs.get(self._active_id)
        return None

    def start(
        self,
        chapter: int,
        count: int,
        sample: bool,
        *,
        book_slug: str = PHYSICS10_SLUG,
        verify: bool = True,
        style_mine: bool = False,
        difficulty_mix: str | None = None,
        pattern_mix: str | None = None,
        balance_answers: bool = True,
        export_profile: str | None = None,
        llm_provider: str | None = None,
        llm_model: str | None = None,
        use_pattern_pack: bool = False,
    ) -> Job:
        with self._lock:
            active = self.active()
            if active and active.status in (JobStatus.queued, JobStatus.running):
                raise RuntimeError("یک تولید در حال اجراست؛ صبر کنید یا لغو کنید.")
            job = Job(
                id=uuid.uuid4().hex[:12],
                chapter=chapter,
                count=count,
                sample=sample,
                book_slug=book_slug or PHYSICS10_SLUG,
                verify=verify,
                style_mine=bool(style_mine),
                difficulty_mix=difficulty_mix,
                pattern_mix=pattern_mix,
                balance_answers=balance_answers,
                export_profile=export_profile,
                llm_provider=llm_provider,
                llm_model=llm_model,
                use_pattern_pack=bool(use_pattern_pack),
            )
            self._jobs[job.id] = job
            self._active_id = job.id
            self._write_pid_file(job)
            thread = threading.Thread(
                target=self._run_job,
                args=(job.id,),
                name=f"darsamooz-job-{job.id}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
            return job

    def cancel(self) -> Job | None:
        with self._lock:
            job = self.active()
            if not job:
                return None
            if job.status in (JobStatus.succeeded, JobStatus.failed, JobStatus.cancelled):
                return job
            job._cancel.set()
            job.log("درخواست لغو دریافت شد…")
            if job.status == JobStatus.queued:
                job.status = JobStatus.cancelled
                job.finished_at = _now()
                job.log("جاب قبل از شروع لغو شد.")
            return job

    def _settings(self) -> Settings:
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
                setattr(settings, attr, (self.project_root / p).resolve())
        return settings

    def _run_job(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.status = JobStatus.running
        job.started_at = _now()
        settings = self._settings()
        try:
            if job._cancel.is_set():
                job.status = JobStatus.cancelled
                job.log("لغو شد.")
                return

            prefix = export_prefix_for(job.book_slug, job.chapter)
            job.prefix = prefix

            if job.sample:
                if job.book_slug != PHYSICS10_SLUG or job.chapter != 1:
                    raise RuntimeError(
                        "حالت نمونه فقط برای کتاب فیزیک ۱۰، فصل ۱ در دسترس است."
                    )
                job.log("بارگذاری سوالات نمونه (بدون LLM)…")
                questions = load_sample_questions()[: job.count]
                paths, run, questions = persist_sample_run(
                    questions,
                    chapter=job.chapter,
                    prefix=prefix,
                    settings=settings,
                    balance_answers=job.balance_answers,
                )
                job.metrics = dict(run.metrics)
                job.log(f"{len(questions)} سوال نمونه — run_id={run.run_id}")
            else:
                from darsamooz.config import resolve_llm_selection

                provider_id, model_id, view = resolve_llm_selection(
                    settings,
                    provider=job.llm_provider,
                    model=job.llm_model,
                )
                if not view.api_key:
                    raise RuntimeError(
                        f"کلید API برای provider={provider_id} تنظیم نشده. "
                        "در .env کلید را بگذارید یا حالت نمونه را روشن کنید."
                    )
                if not model_id:
                    raise RuntimeError(
                        f"مدل برای provider={provider_id} مشخص نیست. "
                        "مدل را در UI انتخاب کنید یا *_MODEL را در .env بگذارید."
                    )
                fb = settings.fallback_provider_name
                if fb and fb == provider_id:
                    fb = None
                fb_bit = f"، fallback={fb}" if fb else ""
                style_bit = "، style_mine=on" if job.style_mine else ""
                if job.use_pattern_pack:
                    style_bit += "، بسته الگوی وب=on"
                job.log(
                    f"تولید با {provider_id} / {model_id}{fb_bit} — "
                    f"{job.book_slug} فصل {job.chapter}، تعداد {job.count}، "
                    f"verify={job.verify}{style_bit}…"
                )
                if job._cancel.is_set():
                    job.status = JobStatus.cancelled
                    job.log("لغو شد.")
                    return
                result = generate_from_chapter_with_run(
                    job.chapter,
                    count=job.count,
                    settings=settings,
                    book_slug=job.book_slug,
                    verify=job.verify,
                    difficulty_mix=job.difficulty_mix,
                    pattern_mix=job.pattern_mix,
                    balance_answers=job.balance_answers,
                    llm_provider=job.llm_provider,
                    llm_model=job.llm_model,
                    style_mine=job.style_mine,
                    on_log=job.log,
                    pattern_pack=True if job.use_pattern_pack else None,
                )
                if job._cancel.is_set():
                    job.status = JobStatus.cancelled
                    job.log("لغو پس از تولید؛ خروجی نوشته نمی‌شود.")
                    return
                questions = result.questions
                paths = persist_generation(result, prefix=prefix, settings=settings)
                job.metrics = dict(result.metrics)
                m = result.metrics
                job.log(
                    f"{len(questions)} سوال — run_id={result.run.run_id} "
                    f"rounds={m.get('rounds')} rejected={m.get('rejected')} "
                    f"tokens={m.get('prompt_tokens')}+{m.get('completion_tokens')} "
                    f"needs_review={m.get('needs_review')} "
                    f"fallback={m.get('fallback_count', 0)} "
                    f"by_provider={m.get('calls_by_provider')}"
                )
                if m.get("fallback_count"):
                    reasons = m.get("fallback_reasons") or []
                    job.log(
                        f"هشدار fallback: {m.get('fallback_count')} فراخوانی از "
                        f"{m.get('primary_provider')} به {m.get('fallback_provider')} — "
                        f"{reasons[:3]}"
                    )

            if job.export_profile:
                from darsamooz.profiles import export_with_profile

                mapped = Path(settings.exports_dir) / f"{prefix}.{job.export_profile}.json"
                export_with_profile(
                    questions,
                    mapped,
                    profile_id=job.export_profile,
                    settings=settings,
                )
                paths = dict(paths)
                paths["profile"] = mapped
                job.log(f"خروجی پروفایل {job.export_profile}: {mapped}")

            job.paths = {k: str(v) for k, v in paths.items()}
            job.question_count = len(questions)
            job.status = JobStatus.succeeded
            job.log("خروجی JSON / CSV / Markdown ذخیره شد.")
        except Exception as exc:
            job.status = JobStatus.failed
            job.error = str(exc)
            job.log(f"خطا: {exc}")
            traceback.print_exc()
        finally:
            job.finished_at = _now()
            self._write_pid_file(job, done=True)

    def _write_pid_file(self, job: Job, done: bool = False) -> None:
        pid_path = self.project_root / ".darsamooz.pid"
        import os

        lines = [
            f"web_pid={os.getpid()}",
            f"job_id={job.id}",
            f"started={_now()}",
            f"project={self.project_root}",
        ]
        if not done and job.status in (JobStatus.queued, JobStatus.running):
            lines.append(f"generate_pid={os.getpid()}")
        text = "\n".join(lines) + "\n"
        pid_path.write_text(text, encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
