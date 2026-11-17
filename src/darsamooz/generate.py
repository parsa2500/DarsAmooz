from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from darsamooz.balance import (
    DIFFICULTY_RUBRIC_FA,
    PATTERN_RUBRIC_FA,
    assign_target_difficulties,
    assign_target_patterns,
    balance_answer_positions,
    difficulty_prompt_hint,
    distribution_metrics,
    parse_difficulty_mix,
    parse_pattern_mix,
    pattern_prompt_hint,
)
from darsamooz.config import Settings, get_settings
from darsamooz.ingest import chapter_title_for, export_prefix_for
from darsamooz.llm import LLMProvider, get_provider, provider_call_summary
from darsamooz.models import Difficulty, Question, QuestionPattern, Section
from darsamooz.review import write_reviews_sidecar
from darsamooz.runs import RunWriter
from darsamooz.sections import load_sections
from darsamooz.validate import validate_batch, validate_question_dict
from darsamooz.verify import sections_to_text_map, verify_questions

SYSTEM_PROMPT = """You write Iranian school MCQs (تست چهارگزینه‌ای; default context grade-10 physics) ONLY from the given unit text.
Return ONLY JSON:
{"questions":[{"stem":"...","options":["a","b","c","d"],"correct_index":0,"explanation":"...","difficulty":"easy|medium|hard","pattern":"tarifi|mafhumi|halli|tarkibi","section":"...","source_refs":["verbatim quote from unit"]}]}
Rules:
- exactly 4 unique options; one correct; Persian stem/explanation
- no invented facts; source_refs = short continuous phrase copied from the unit text
- difficulty MUST follow the rubric in the user message (hard ≠ restating a definition)
- pattern MUST match: tarifi=تعریفی, mafhumi=مفهومی, halli=حلی/numeric, tarkibi=ترکیبی
- halli items MUST include numbers/calculation grounded in the unit
- if a web style-pack / pattern block is present, use it ONLY for stem/option structure and trap style — never copy or paraphrase web questions, facts or numbers; all truth stays in the unit text
"""


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


def _chapter_title(
    num: int,
    book_slug: str = "physics10-riazi-110209",
    settings: Settings | None = None,
) -> str:
    return chapter_title_for(book_slug, num, settings)


def _build_unit_prompt(
    chapter_num: int,
    chapter_title: str,
    section: Section,
    count: int,
    *,
    target_difficulty: Difficulty | None = None,
    target_pattern: QuestionPattern | None = None,
    style_block: str = "",
    book_label: str = "فیزیک (۱) دهم ریاضی (۱۱۰۲۰۹)",
) -> str:
    d_hint = difficulty_prompt_hint(target_difficulty)
    p_hint = pattern_prompt_hint(target_pattern)
    rubric_block = "\n".join(
        [
            "روبریک سختی:",
            *[f"- {v}" for v in DIFFICULTY_RUBRIC_FA.values()],
            "روبریک الگو:",
            *[f"- {v}" for v in PATTERN_RUBRIC_FA.values()],
        ]
    )
    style = f"\n{style_block}\n" if (style_block or "").strip() else "\n"
    return (
        f"کتاب/منبع: {book_label}\n"
        f"فصل {chapter_num}: {chapter_title}\n"
        f"یونیت: {section.id} — {section.title}\n"
        f"دقیقاً {count} تست بساز؛ فقط از متن یونیت.\n"
        f"{d_hint}\n{p_hint}\n"
        f"{style}"
        f"{rubric_block}\n\n"
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
    pattern_mix: str | dict | None = None,
    balance_answers: bool = True,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    style_mine: bool = False,
    pattern_pack: Any = None,
) -> list[Question]:
    return generate_from_chapter_with_run(
        chapter_num,
        count=count,
        provider=provider,
        settings=settings,
        book_slug=book_slug,
        verify=verify,
        difficulty_mix=difficulty_mix,
        pattern_mix=pattern_mix,
        balance_answers=balance_answers,
        llm_provider=llm_provider,
        llm_model=llm_model,
        style_mine=style_mine,
        pattern_pack=pattern_pack,
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
    pattern_mix: str | dict | None = None,
    balance_answers: bool = True,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    style_mine: bool = False,
    on_log: Any | None = None,
    pattern_pack: Any = None,
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

    if isinstance(pattern_mix, dict):
        pm = {
            (QuestionPattern(k) if not isinstance(k, QuestionPattern) else k): float(v)
            for k, v in pattern_mix.items()
        }
        ptotal = sum(pm.values())
        pattern_norm = {k: v / ptotal for k, v in pm.items()} if ptotal else None
    else:
        pattern_norm = parse_pattern_mix(pattern_mix)

    title = _chapter_title(chapter_num, book_slug, settings)
    schedule = plan_unit_schedule(sections, count)
    if not schedule:
        raise RuntimeError(f"Chapter {chapter_num} has no usable section text.")

    targets = assign_target_difficulties(count, mix_norm)
    target_iter = iter(targets)
    pattern_targets = assign_target_patterns(count, pattern_norm)
    pattern_iter = iter(pattern_targets)

    provider = provider or get_provider(
        settings,
        provider_override=llm_provider,
        model_override=llm_model,
    )
    primary_name = getattr(provider, "primary_name", None) or getattr(
        provider, "provider_label", None
    ) or (llm_provider or settings.provider_name)
    model_name: str | None = getattr(provider, "primary_model", None) or getattr(
        provider, "model_label", None
    )
    if model_name is None:
        try:
            from darsamooz.config import resolve_llm_selection

            _, model_name, _ = resolve_llm_selection(
                settings, provider=llm_provider, model=llm_model
            )
        except Exception:
            model_name = llm_model

    style_block = ""
    style_pack_path_str: str | None = None
    tavily_hits = 0
    style_pack_cached = False
    if style_mine:
        from darsamooz.style_mine import (
            format_style_pack_for_prompt,
            load_style_pack,
            mine_style_pack,
            topic_from_titles,
        )

        def _log(msg: str) -> None:
            if callable(on_log):
                on_log(msg)

        cached = load_style_pack(book_slug, chapter_num, settings)
        if cached:
            style_pack_cached = True
            _log("بسته سبک وب از کش بارگذاری شد.")
            pack = cached
            from darsamooz.style_mine import style_pack_path as _spp

            style_pack_path_str = str(_spp(book_slug, chapter_num, settings))
            tavily_hits = int(pack.get("search_hits") or pack.get("tavily_hits") or 0)
        else:
            _log("الگوبرداری وب با API مدل…")
            topic = topic_from_titles(title, book_slug)
            pack, pack_path, tavily_hits = mine_style_pack(
                topic,
                book_slug=book_slug,
                chapter=chapter_num,
                chapter_title=title,
                provider=provider,
                settings=settings,
                force_refresh=True,
            )
            style_pack_path_str = str(pack_path)
            backend = pack.get("search_backend") or "llm"
            _log(
                f"بسته سبک آماده شد (backend={backend}"
                + (f"، sources≈{tavily_hits}" if tavily_hits else "")
                + ")."
            )
        style_block = format_style_pack_for_prompt(pack)

    pp_pack, pp_sources, pp_path = _resolve_pattern_pack(
        pattern_pack, book_slug, chapter_num, settings
    )
    pp_patterns: list[dict[str, Any]] = []
    web_texts: list[str] = []
    if pp_pack is not None:
        from darsamooz.patterns import active_patterns, web_question_texts

        pp_patterns = active_patterns(pp_pack)
        if not pp_patterns:
            raise RuntimeError("بسته الگو هیچ الگوی فعالی ندارد.")
        web_texts = web_question_texts(pp_sources)
        if callable(on_log):
            on_log(
                f"بسته الگو: {len(pp_patterns)} الگو، "
                f"{pp_pack.get('sources_count', 0)} منبع وب (status={pp_pack.get('status')})."
            )
        if style_block:
            # Pattern pack supersedes the older style pack; avoid conflicting blocks.
            style_block = ""

    run = RunWriter.create(
        chapter_num,
        settings=settings,
        request={
            "book_slug": book_slug,
            "count": count,
            "sample": False,
            "provider": primary_name,
            "model": model_name,
            "provider_override": llm_provider,
            "model_override": llm_model,
            "fallback_provider": settings.fallback_provider_name,
            "units_scheduled": len(schedule),
            "verify": verify,
            "difficulty_mix": (
                {k.value: v for k, v in mix_norm.items()} if mix_norm else None
            ),
            "pattern_mix": (
                {k.value: v for k, v in pattern_norm.items()} if pattern_norm else None
            ),
            "balance_answers": balance_answers,
            "style_mine": bool(style_mine),
            "style_pack_path": style_pack_path_str,
            "tavily_hits": tavily_hits if style_mine else None,
            "style_pack_cached": style_pack_cached if style_mine else None,
            "pattern_pack_path": pp_path,
            "pattern_pack_status": (pp_pack or {}).get("status"),
        },
    )
    if pp_pack is not None:
        _copy_pattern_artifacts(run.root, pp_pack, pp_sources, book_slug, chapter_num, settings)

    prefix = export_prefix_for(book_slug, chapter_num)
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
    web_dup_rejected = 0
    pattern_use: dict[str, int] = {}

    max_rounds = max(len(schedule) * 2, count * 2)

    for i in range(max_rounds):
        if len(questions) >= count:
            break
        sec, batch = schedule[i % len(schedule)]
        batch = min(batch, count - len(questions))
        rounds += 1
        run.record_unit(sec.id, sec.title, len(sec.text))
        # Use next target difficulty/pattern for this batch (first slot)
        target = next(target_iter, None) if batch else None
        if target is None and mix_norm and len(questions) < len(targets):
            target = targets[len(questions)]
        ptarget = next(pattern_iter, None) if batch else None
        if ptarget is None and pattern_norm and len(questions) < len(pattern_targets):
            ptarget = pattern_targets[len(questions)]
        round_block = style_block
        web_pat: dict[str, Any] | None = None
        if pp_patterns:
            from darsamooz.patterns import format_pattern_for_prompt

            web_pat = pp_patterns[(rounds - 1) % len(pp_patterns)]
            round_block = format_pattern_for_prompt(pp_pack, web_pat)
            if pattern_norm is None:
                try:
                    ptarget = QuestionPattern(web_pat.get("question_type") or "mafhumi")
                except ValueError:
                    pass
        user = _build_unit_prompt(
            chapter_num,
            title,
            sec,
            batch,
            target_difficulty=target,
            target_pattern=ptarget,
            style_block=round_block,
            book_label=book_slug,
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
            if ptarget is not None and not raw.get("pattern"):
                raw["pattern"] = ptarget.value
            claimed_hard = str(raw.get("difficulty") or "").lower() == "hard"
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
            if web_texts:
                from darsamooz.patterns import is_web_near_duplicate

                dup, dup_score, dup_match = is_web_near_duplicate(stem, web_texts)
                if dup:
                    rejected += 1
                    web_dup_rejected += 1
                    run.append_item(
                        {
                            "status": "rejected",
                            "reason": "web_near_duplicate",
                            "section_id": sec.id,
                            "stem": stem[:120],
                            "web_similarity": round(dup_score, 3),
                            "web_match": (dup_match or "")[:160],
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
            if web_pat is not None:
                q.tags = [*(q.tags or []), f"web_pattern:{web_pat.get('id')}"]
                pattern_use[str(web_pat.get("id"))] = pattern_use.get(str(web_pat.get("id")), 0) + 1
            questions.append(q)
            accepted_stems.append(q.stem)
            section_for_qid[q.id] = sec.id
            accepted_this_round += 1
            acc_item = {
                "status": "accepted",
                "id": q.id,
                "section_id": sec.id,
                "stem": q.stem[:160],
                "difficulty": q.difficulty.value,
                "pattern": q.pattern.value,
            }
            if claimed_hard and q.difficulty != Difficulty.hard:
                acc_item["hard_downgraded"] = True
            if web_pat is not None:
                acc_item["web_pattern_id"] = web_pat.get("id")
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
            provider_override=llm_provider,
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
        "pattern_mix_target": (
            {k.value: v for k, v in pattern_norm.items()} if pattern_norm else None
        ),
        "style_mine": bool(style_mine),
        "style_pack_path": style_pack_path_str,
        "tavily_hits": tavily_hits if style_mine else None,
        "style_pack_cached": style_pack_cached if style_mine else None,
        "pattern_pack_used": pp_pack is not None,
        "pattern_pack_status": (pp_pack or {}).get("status"),
        "pattern_pack_path": pp_path,
        "patterns_count": len(pp_patterns) if pp_pack is not None else None,
        "pattern_usage": pattern_use if pp_pack is not None else None,
        "web_sources_count": (pp_pack or {}).get("sources_count") if pp_pack is not None else None,
        "search_backend": (pp_pack or {}).get("search_backend") if pp_pack is not None else None,
        "search_error": (
            "; ".join(
                f"{a.get('backend')}: {a.get('error')}"
                for a in (pp_pack or {}).get("search_attempts") or []
                if not a.get("ok")
            )
            or None
        )
        if pp_pack is not None
        else None,
        "web_dup_rejected": web_dup_rejected if pp_pack is not None else None,
        "hard_downgraded_count": sum(
            1 for q in questions if "hard_downgraded" in (q.tags or [])
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
            f"(early_stop={early_stop}, provider={primary_name}, "
            f"model={model_name}, run_id={run.run_id})."
            f"{detail}"
        )

    return GenerationResult(
        questions=questions,
        run=run,
        metrics=metrics,
    )



def _resolve_pattern_pack(
    pattern_pack: Any,
    book_slug: str,
    chapter_num: int,
    settings: Settings,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Accept None/False, True/"auto" (stored pack for slug+chapter), a path, or a dict."""
    if pattern_pack is None or pattern_pack is False or pattern_pack == "":
        return None, None, None
    from darsamooz.patterns import (
        load_pattern_pack,
        load_web_sources,
        pattern_pack_path,
    )

    if pattern_pack is True or (isinstance(pattern_pack, str) and pattern_pack.lower() == "auto"):
        pack = load_pattern_pack(book_slug, chapter_num, settings)
        if not pack:
            raise FileNotFoundError(
                f"بسته الگو برای {book_slug} فصل {chapter_num} وجود ندارد. "
                "اول mine-web-patterns (یا دکمه جستجوی وب در UI) را اجرا کنید."
            )
        return (
            pack,
            load_web_sources(book_slug, chapter_num, settings),
            str(pattern_pack_path(book_slug, chapter_num, settings)),
        )
    if isinstance(pattern_pack, dict):
        return pattern_pack, load_web_sources(book_slug, chapter_num, settings), None
    path = Path(str(pattern_pack))
    if not path.is_file():
        raise FileNotFoundError(f"فایل بسته الگو پیدا نشد: {path}")
    pack = json.loads(path.read_text(encoding="utf-8"))
    sources_file = path.with_name("web_sources.json")
    sources = (
        json.loads(sources_file.read_text(encoding="utf-8"))
        if sources_file.is_file()
        else load_web_sources(
            pack.get("book_slug") or book_slug,
            int(pack.get("chapter_num") or chapter_num),
            settings,
        )
    )
    return pack, sources, str(path)


def _copy_pattern_artifacts(
    run_root: Path,
    pack: dict[str, Any],
    web_sources: dict[str, Any] | None,
    book_slug: str,
    chapter_num: int,
    settings: Settings,
) -> None:
    """Traceability: keep the exact pack, web sources and topic profile in the run folder."""
    from darsamooz.topic import load_topic_profile

    def _w(name: str, obj: Any) -> None:
        if obj is None:
            return
        (run_root / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    _w("pattern_pack.json", pack)
    _w("web_sources.json", web_sources)
    _w("topic_profile.json", load_topic_profile(book_slug, chapter_num, settings))


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
