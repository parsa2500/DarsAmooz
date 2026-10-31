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
    status: JobStatus = JobStatus.queued
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    prefix: str | None = None
    paths: dict[str, str] = field(default_factory=dict)
    question_count: int = 0
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
            "status": self.status.value,
            "logs": list(self.logs),
            "error": self.error,
            "prefix": self.prefix,
            "paths": dict(self.paths),
            "question_count": self.question_count,
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

    def start(self, chapter: int, count: int, sample: bool) -> Job:
        with self._lock:
            active = self.active()
            if active and active.status in (JobStatus.queued, JobStatus.running):
                raise RuntimeError("یک تولید در حال اجراست؛ صبر کنید یا لغو کنید.")
            job = Job(
                id=uuid.uuid4().hex[:12],
                chapter=chapter,
                count=count,
                sample=sample,
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
        # Resolve data dirs against project root so generate works even if cwd drifts.
        for attr in ("data_dir", "textbooks_dir", "extracted_dir", "exports_dir", "manifests_dir"):
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

            prefix = f"phys10-ch{job.chapter}"
            job.prefix = prefix

            if job.sample:
                if job.chapter != 1:
                    raise RuntimeError("حالت نمونه فقط برای فصل ۱ در دسترس است.")
                job.log("بارگذاری سوالات نمونه (بدون LLM)…")
                questions = load_sample_questions()[: job.count]
                paths, run = persist_sample_run(
                    questions, chapter=job.chapter, prefix=prefix, settings=settings
                )
                job.log(f"{len(questions)} سوال نمونه — run_id={run.run_id}")
            else:
                if not settings.api_key:
                    raise RuntimeError(
                        f"کلید API برای LLM_PROVIDER={settings.provider_name} تنظیم نشده. "
                        "در .env کلید را بگذارید یا حالت نمونه را روشن کنید."
                    )
                job.log(
                    f"تولید با {settings.provider_name} / {settings.model} — "
                    f"فصل {job.chapter}، تعداد {job.count}…"
                )
                if job._cancel.is_set():
                    job.status = JobStatus.cancelled
                    job.log("لغو شد.")
                    return
                result = generate_from_chapter_with_run(
                    job.chapter, count=job.count, settings=settings
                )
                if job._cancel.is_set():
                    job.status = JobStatus.cancelled
                    job.log("لغو پس از تولید؛ خروجی نوشته نمی‌شود.")
                    return
                questions = result.questions
                paths = persist_generation(result, prefix=prefix, settings=settings)
                m = result.metrics
                job.log(
                    f"{len(questions)} سوال — run_id={result.run.run_id} "
                    f"rounds={m.get('rounds')} rejected={m.get('rejected')} "
                    f"tokens={m.get('prompt_tokens')}+{m.get('completion_tokens')}"
                )

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
