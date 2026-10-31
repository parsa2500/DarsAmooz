from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from darsamooz.balance import (
    assign_target_difficulties,
    balance_answer_positions,
    difficulty_prompt_hint,
    distribution_metrics,
    parse_difficulty_mix,
)
from darsamooz.config import Settings, get_settings
from darsamooz.ingest import PHYSICS10_CHAPTERS
from darsamooz.llm import LLMProvider, get_provider, provider_call_summary
from darsamooz.models import Difficulty, Question, Section
from darsamooz.review import write_reviews_sidecar
from darsamooz.runs import RunWriter
from darsamooz.sections import load_sections
from darsamooz.validate import validate_batch, validate_question_dict
from darsamooz.verify import sections_to_text_map, verify_questions

SYSTEM_PROMPT = """You write Iranian grade-10 physics MCQs (تست چهارگزینه‌ای) ONLY from the given unit text.
Return ONLY JSON: {"questions":[{"stem":"...","options":["a","b","c","d"],"correct_index":0,"explanation":"...","difficulty":"easy|medium|hard","section":"...","source_refs":["verbatim quote from unit"]}]}
Rules: exactly 4 unique options; one correct; Persian stem/explanation; no invented facts; source_refs must be a short continuous phrase copied from the unit text."""


_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_NON_WORD = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)

EARLY_STOP_EMPTY_ROUNDS = 3
BATCH_PER_UNIT = 2


@dataclass
class GenerationResult:
    questions: list[Question]
    run: RunWriter
    metrics: dict[str, Any] = field(default_factory=dict)


def _normalize_stem(stem: str) -> str:
    s = stem.strip().replace("ي", "ی").replace("ك", "ک").replace("ة", "ه")
    s = _ARABIC_DIACRITICS.sub("", s)
    s = _NON_WORD.sub(" ", s)
    return " ".join(s.split()).lower()


def _stems_too_similar(a: str, b: str) -> bool:
    na, nb = _normalize_stem(a), _normalize_stem(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    motif_re = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|km|μm|mm|cm|m|s|n|j|w|pa|k)\b", re.I)
    motifs_a = {re.sub(r"\s+", "", m.group(0).lower()) for m in motif_re.finditer(na)}
    motifs_b = {re.sub(r"\s+", "", m.group(0).lower()) for m in motif_re.finditer(nb)}
    if motifs_a & motifs_b:
        return True
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return overlap >= 0.85


def _chapter_title(num: int) -> str:
    for n, t in PHYSICS10_CHAPTERS:
        if n == num:
            return t
    return f"فصل {num}"


def _build_unit_prompt(
    chapter_num: int,
    chapter_title: str,
    section: Section,
    count: int,
    *,
    target_difficulty: Difficulty | None = None,
) -> str:
    hint = difficulty_prompt_hint(target_difficulty)
    return (
        f"کتاب: فیزیک (۱) دهم ریاضی (۱۱۰۲۰۹)\n"
        f"فصل {chapter_num}: {chapter_title}\n"
        f"یونیت: {section.id} — {section.title}\n"
        f"دقیقاً {count} تست بساز؛ فقط از متن یونیت.\n"
        f"{hint}\n\n"
        f"--- یونیت ---\n{section.text}\n--- پایان ---\n"
    )


def plan_unit_schedule(sections: list[Section], count: int) -> list[tuple[Section, int]]:
    """Round-robin units; each visit asks for up to BATCH_PER_UNIT questions."""
    usable = [s for s in sections if len((s.text or "").strip()) >= 80]
    if not usable:
        return []
    schedule: list[tuple[Section, int]] = []
    remaining = count
    idx = 0
    max_slots = max(count * 2, len(usable) * 2)
    while remaining > 0 and len(schedule) < max_slots:
        sec = usable[idx % len(usable)]
        batch = min(BATCH_PER_UNIT, remaining)
        schedule.append((sec, batch))
        remaining -= batch
        idx += 1
    return schedule


def generate_from_chapter(
    chapter_num: int,
    count: int = 10,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    book_slug: str = "physics10-riazi-110209",
    verify: bool = True,
    difficulty_mix: str | dict | None = None,
    balance_answers: bool = True,
) -> list[Question]:
    return generate_from_chapter_with_run(
        chapter_num,
        count=count,
        provider=provider,
        settings=settings,
        book_slug=book_slug,
        verify=verify,
        difficulty_mix=difficulty_mix,
        balance_answers=balance_answers,
    ).questions



def _llm_item_fields(provider: LLMProvider) -> dict[str, Any]:
    """Per-call provider/model/fallback visibility for run items.jsonl."""
    call = getattr(provider, "last_call", None) or {}
    if not call:
        return {}
    out: dict[str, Any] = {
        "llm_provider": call.get("provider"),
        "llm_model": call.get("model"),
        "fallback_used": bool(call.get("fallback_used")),
    }
    if call.get("fallback_used"):
        out["fallback_from"] = call.get("fallback_from")
        out["fallback_reason"] = call.get("fallback_reason")
    return out


def generate_from_chapter_with_run(
    chapter_num: int,
    count: int = 10,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    book_slug: str = "physics10-riazi-110209",
    verify: bool = True,
    difficulty_mix: str | dict | None = None,
    balance_answers: bool = True,
) -> GenerationResult:
    settings = settings or get_settings()
    sections = load_sections(book_slug, chapter_num, settings)
    if not sections:
        raise FileNotFoundError(
            f"No sections for chapter {chapter_num}. Run `darsamooz ingest` first."
        )

    if isinstance(difficulty_mix, dict):
        mix = {
            (Difficulty(k) if not isinstance(k, Difficulty) else k): float(v)
            for k, v in difficulty_mix.items()
        }
        total = sum(mix.values())
        mix_norm = {k: v / total for k, v in mix.items()} if total else None
    else:
        mix_norm = parse_difficulty_mix(difficulty_mix)

    title = _chapter_title(chapter_num)
    schedule = plan_unit_schedule(sections, count)
    if not schedule:
        raise RuntimeError(f"Chapter {chapter_num} has no usable section text.")

    targets = assign_target_difficulties(count, mix_norm)
    target_iter = iter(targets)

    provider = provider or get_provider(settings)
    model_name: str | None = None
    try:
        model_name = settings.model
    except RuntimeError:
        model_name = None
    run = RunWriter.create(
        chapter_num,
        settings=settings,
        request={
            "book_slug": book_slug,
            "count": count,
            "sample": False,
            "provider": settings.provider_name,
            "model": model_name,
            "fallback_provider": settings.fallback_provider_name,
            "units_scheduled": len(schedule),
            "verify": verify,
            "difficulty_mix": (
                {k.value: v for k, v in mix_norm.items()} if mix_norm else None
            ),
            "balance_answers": balance_answers,
        },
    )

    prefix = f"phys10-ch{chapter_num}"
    questions: list[Question] = []
    accepted_stems: list[str] = []
    last_error: Exception | None = None
    empty_streak = 0
    rounds = 0
    rejected = 0
    prompt_tokens = 0
    completion_tokens = 0
    tokens_known = False
    early_stop = False
    section_for_qid: dict[str, str] = {}

    max_rounds = max(len(schedule) * 2, count * 2)

    for i in range(max_rounds):
        if len(questions) >= count:
            break
        sec, batch = schedule[i % len(schedule)]
        batch = min(batch, count - len(questions))
        rounds += 1
        run.record_unit(sec.id, sec.title, len(sec.text))
        # Use next target difficulty for this batch (first slot)
        target = next(target_iter, None) if batch else None
        # If we already consumed targets for accepted qs, peek remaining plan
        if target is None and mix_norm and len(questions) < len(targets):
            target = targets[len(questions)]
        user = _build_unit_prompt(
            chapter_num, title, sec, batch, target_difficulty=target
        )
        try:
            data = provider.complete_json(SYSTEM_PROMPT, user)
            usage = getattr(provider, "last_usage", None) or {}
            if usage.get("prompt_tokens") is not None:
                tokens_known = True
                prompt_tokens += int(usage["prompt_tokens"] or 0)
            if usage.get("completion_tokens") is not None:
                tokens_known = True
                completion_tokens += int(usage["completion_tokens"] or 0)
            llm_fields = _llm_item_fields(provider)
        except Exception as exc:
            last_error = exc
            empty_streak += 1
            err_item = {
                "status": "rejected",
                "reason": f"llm_error: {exc}",
                "section_id": sec.id,
            }
            err_item.update(_llm_item_fields(provider))
            run.append_item(err_item)
            rejected += 1
            if empty_streak >= EARLY_STOP_EMPTY_ROUNDS:
                early_stop = True
                break
            continue

        items = data.get("questions", data if isinstance(data, list) else [])
        if not isinstance(items, list):
            items = []

        accepted_this_round = 0
        for raw in items:
            if not isinstance(raw, dict):
                rejected += 1
                run.append_item(
                    {
                        "status": "rejected",
                        "reason": "not_a_dict",
                        "section_id": sec.id,
                    }
                )
                continue
            raw = {**raw, "section_id": sec.id, "section": raw.get("section") or sec.title}
            if target is not None and not raw.get("difficulty"):
                raw["difficulty"] = target.value
            stem = str(raw.get("stem") or raw.get("question") or "")
            if any(_stems_too_similar(stem, prev) for prev in accepted_stems):
                rejected += 1
                run.append_item(
                    {
                        "status": "rejected",
                        "reason": "similar_stem",
                        "section_id": sec.id,
                        "stem": stem[:120],
                    }
                )
                continue
            try:
                qid = raw.get("id") or f"{prefix}-q{len(questions)+1:03d}"
                q = validate_question_dict(
                    raw,
                    chapter=chapter_num,
                    chapter_title=title,
                    qid=qid,
                    unit_text=sec.text,
                    require_grounding=True,
                )
            except Exception as exc:
                rejected += 1
                run.append_item(
                    {
                        "status": "rejected",
                        "reason": str(exc),
                        "section_id": sec.id,
                        "stem": stem[:120],
                    }
                )
                continue
            if any(_stems_too_similar(q.stem, prev.stem) for prev in questions):
                rejected += 1
                run.append_item(
                    {
                        "status": "rejected",
                        "reason": "similar_stem_validated",
                        "section_id": sec.id,
                        "id": q.id,
                    }
                )
                continue
            questions.append(q)
            accepted_stems.append(q.stem)
            section_for_qid[q.id] = sec.id
            accepted_this_round += 1
            acc_item = {
                "status": "accepted",
                "id": q.id,
                "section_id": sec.id,
                "stem": q.stem[:160],
            }
            acc_item.update(llm_fields)
            run.append_item(acc_item)
            if len(questions) >= count:
                break

        if accepted_this_round == 0:
            empty_streak += 1
            if empty_streak >= EARLY_STOP_EMPTY_ROUNDS:
                early_stop = True
                break
        else:
            empty_streak = 0

    questions = questions[:count]

    if balance_answers and questions:
        questions = balance_answer_positions(questions)

    verify_metrics: dict[str, Any] = {"verify_enabled": False}
    if verify and questions:
        text_map = sections_to_text_map(sections)
        questions, verify_metrics = verify_questions(
            questions,
            section_text_by_id=text_map,
            provider=provider,
            settings=settings,
        )
        for q in questions:
            run.append_item(
                {
                    "status": "verified",
                    "id": q.id,
                    "review_status": q.review_status.value,
                    "verifier_agrees": q.verifier_agrees,
                    "verifier_answer_index": q.verifier_answer_index,
                    "verifier_reason": (q.verifier_reason or "")[:200],
                }
            )

    dist = distribution_metrics(questions)
    llm_meta = provider_call_summary(provider, settings=settings)
    metrics: dict[str, Any] = {
        "rounds": rounds,
        "accepted": len(questions),
        "rejected": rejected,
        "requested": count,
        "early_stop": early_stop,
        "prompt_tokens": prompt_tokens if tokens_known else None,
        "completion_tokens": completion_tokens if tokens_known else None,
        "units_touched": len({u["section_id"] for u in run.units_used}),
        "balance_answers": balance_answers,
        "difficulty_mix_target": (
            {k.value: v for k, v in mix_norm.items()} if mix_norm else None
        ),
        **dist,
        **verify_metrics,
        # Flat visibility fields for UI/job status (also nested under llm)
        "fallback_enabled": llm_meta.get("fallback_enabled"),
        "fallback_provider": llm_meta.get("fallback_provider"),
        "fallback_count": llm_meta.get("fallback_count", 0),
        "calls_by_provider": llm_meta.get("calls_by_provider", {}),
        "fallback_reasons": llm_meta.get("fallback_reasons", []),
        "llm_calls": llm_meta.get("llm_calls", 0),
        "primary_provider": llm_meta.get("primary_provider"),
        "primary_model": llm_meta.get("primary_model"),
        "llm": llm_meta,
    }
    if last_error:
        metrics["last_error"] = str(last_error)
    run.write_metrics(metrics)
    write_reviews_sidecar(run.root, questions)

    if len(questions) < count:
        detail = f" last_error={last_error}" if last_error else ""
        raise RuntimeError(
            f"Only generated {len(questions)}/{count} valid book-grounded questions "
            f"for chapter {chapter_num} after {rounds} rounds "
            f"(early_stop={early_stop}, provider={settings.provider_name}, "
            f"model={model_name}, run_id={run.run_id})."
            f"{detail}"
        )

    return GenerationResult(
        questions=questions,
        run=run,
        metrics=metrics,
    )


def persist_generation(
    result: GenerationResult,
    *,
    prefix: str,
    settings: Settings | None = None,
    approved_only: bool = False,
) -> dict[str, Path]:
    settings = settings or get_settings()
    return result.run.finalize_exports(
        result.questions,
        prefix=prefix,
        settings=settings,
        approved_only=approved_only,
    )


def persist_sample_run(
    questions: list[Question],
    *,
    chapter: int,
    prefix: str,
    settings: Settings | None = None,
    balance_answers: bool = True,
) -> tuple[dict[str, Path], RunWriter, list[Question]]:
    settings = settings or get_settings()
    qs = list(questions)
    if balance_answers:
        qs = balance_answer_positions(qs)
    run = RunWriter.create(
        chapter,
        settings=settings,
        request={
            "count": len(qs),
            "sample": True,
            "provider": None,
            "model": None,
            "verify": False,
            "balance_answers": balance_answers,
        },
    )
    for q in qs:
        run.append_item(
            {"status": "accepted", "id": q.id, "sample": True, "stem": q.stem[:160]}
        )
    dist = distribution_metrics(qs)
    run.write_metrics(
        {
            "rounds": 0,
            "accepted": len(qs),
            "rejected": 0,
            "requested": len(qs),
            "early_stop": False,
            "prompt_tokens": None,
            "completion_tokens": None,
            "sample": True,
            "verify_enabled": False,
            "balance_answers": balance_answers,
            **dist,
        }
    )
    write_reviews_sidecar(run.root, qs)
    paths = run.finalize_exports(qs, prefix=prefix, settings=settings)
    return paths, run, qs


def load_sample_questions(path: Path | None = None) -> list[Question]:
    path = path or Path("samples/physics10_ch1_sample.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["questions"] if isinstance(data, dict) else data
    return validate_batch(
        items,
        chapter=1,
        chapter_title=_chapter_title(1),
        id_prefix="phys10-ch1-sample",
        require_grounding=False,
    )
