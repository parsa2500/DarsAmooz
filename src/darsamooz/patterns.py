"""Steps 3–4 of the handout flow: turn real web test excerpts into a *pattern pack*.

A pattern pack is structure only (stem skeletons, option format, distractor
strategies, numeric vs conceptual, difficulty, traps). Generated questions take
their facts only from the handout text, and every generated stem is checked
against the fetched web questions so nothing is copied near-verbatim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.llm import LLMProvider
from darsamooz.sections import sections_dir
from darsamooz.topic import load_topic_profile, topic_line

PATTERN_TYPES = ("tarifi", "mafhumi", "halli", "tarkibi")
DIFFICULTIES = ("easy", "medium", "hard")

PATTERN_SYSTEM = """You analyse REAL Iranian exam MCQs (تست چهارگزینه‌ای) found on the web and extract reusable *patterns*.
Return ONLY JSON:
{
  "exam_styles": ["which exams the evidence looks like: کنکور سراسری / امتحان نهایی / قلم‌چی / گاج / ..."],
  "patterns": [
    {
      "name_fa": "short Persian name",
      "question_type": "tarifi|mafhumi|halli|tarkibi",
      "stem_template": "ABSTRACT skeleton with placeholders like [کمیت] [عدد+یکا] [پدیده]; never a real stem",
      "option_format": "how the 4 options look (numbers with units, short statements, 'هر دو'، ...)",
      "distractor_strategies": ["how wrong options are built"],
      "numeric": true,
      "difficulty": "easy|medium|hard",
      "traps": ["common traps"],
      "section": "the chapter section (copy one title from the given section list) this pattern tests",
      "evidence": [1, 3]
    }
  ],
  "general_traps": ["..."],
  "option_format_notes": "...",
  "difficulty_notes": "..."
}
Rules:
- 4 to 8 patterns, diverse (mix numeric and conceptual when the evidence has both).
- NEVER copy a real question stem, number set, or option list. Abstract skeletons only.
- evidence = numbers of the [n] sources that show the pattern (empty if none).
- Persian text for names/templates/notes.
- ONLY patterns whose core skill is taught in the given chapter sections. If a web question
  tests another chapter/grade (e.g. Newton's laws / F=ma computations in a measurement
  chapter), do NOT turn it into a pattern.
- section MUST be copied from the given section list.
"""

SCOPE_AUDIT_SYSTEM = """You audit whether exam-question patterns belong to ONE Iranian textbook chapter.
You get the chapter title, its section titles, a short outline of each section from the book,
and candidate patterns. Return ONLY JSON:
{"patterns":[{"id":"p1","on_chapter":true,"section":"exact title from the section list","reason":"short Persian"}]}
Rules:
- Each pattern comes with book sentences that share its words. Judge from them and the outline.
- on_chapter=true when the pattern's core skill is taught in these sections (book sentences
  explain or practise it).
- on_chapter=false when the skill belongs to another chapter or grade, even if a word or formula
  is mentioned in passing (e.g. computing acceleration/weight with F=ma in a chapter where F=ma
  only appears as an example of units), or when no book sentence supports it.
- section must be copied exactly from the list (empty if on_chapter=false).
"""

LLM_ONLY_NOTE = (
    "بدون شواهد وب: الگوها فقط از دانش عمومی مدل درباره آزمون‌های ایران ساخته شده‌اند."
)


def pattern_pack_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "pattern_pack.json"


def web_sources_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "web_sources.json"


def _write_json(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_pattern_pack(book_slug: str, chapter: int, settings: Settings | None = None) -> dict[str, Any] | None:
    return _read_json(pattern_pack_path(book_slug, chapter, settings))


def load_web_sources(book_slug: str, chapter: int, settings: Settings | None = None) -> dict[str, Any] | None:
    return _read_json(web_sources_path(book_slug, chapter, settings))


def save_pattern_pack(pack: dict[str, Any], book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return _write_json(pattern_pack_path(book_slug, chapter, settings), pack)


def save_web_sources(data: dict[str, Any], book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return _write_json(web_sources_path(book_slug, chapter, settings), data)


# ---------------------------------------------------------------- normalize


def _str_list(vals: Any, limit: int, maxlen: int = 240) -> list[str]:
    if isinstance(vals, str):
        vals = [vals]
    out: list[str] = []
    for v in vals or []:
        s = str(v).strip()
        if s and s not in out:
            out.append(s[:maxlen])
    return out[:limit]


def normalize_pattern_pack(
    raw: dict[str, Any] | None,
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = raw or {}
    sources = sources or []
    patterns: list[dict[str, Any]] = []
    for item in raw.get("patterns") or []:
        if not isinstance(item, dict):
            continue
        template = str(item.get("stem_template") or "").strip()
        name = str(item.get("name_fa") or "").strip()
        if not template and not name:
            continue
        qtype = str(item.get("question_type") or "mafhumi").strip().lower()
        if qtype not in PATTERN_TYPES:
            qtype = "halli" if item.get("numeric") else "mafhumi"
        diff = str(item.get("difficulty") or "medium").strip().lower()
        if diff not in DIFFICULTIES:
            diff = "medium"
        evidence_urls: list[str] = []
        for ev in item.get("evidence") or []:
            try:
                idx = int(ev) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(sources):
                url = str(sources[idx].get("url") or "")
                if url and url not in evidence_urls:
                    evidence_urls.append(url)
        numeric = item.get("numeric")
        if isinstance(numeric, str):
            numeric = numeric.strip().lower() in {"true", "1", "yes", "بله"}
        patterns.append(
            {
                "id": f"p{len(patterns) + 1}",
                "name_fa": (name or template[:40])[:120],
                "question_type": qtype,
                "stem_template": template[:400],
                "option_format": str(item.get("option_format") or "").strip()[:300],
                "distractor_strategies": _str_list(item.get("distractor_strategies"), 5),
                "numeric": bool(numeric) if numeric is not None else qtype == "halli",
                "difficulty": diff,
                "traps": _str_list(item.get("traps"), 5),
                "evidence_urls": evidence_urls[:5],
                "section": str(item.get("section") or "").strip()[:160],
                "enabled": item.get("enabled", True) is not False,
            }
        )
        if len(patterns) >= 8:
            break
    return {
        "exam_styles": _str_list(raw.get("exam_styles"), 6, 120),
        "patterns": patterns,
        "general_traps": _str_list(raw.get("general_traps"), 6),
        "option_format_notes": str(raw.get("option_format_notes") or "").strip()[:500],
        "difficulty_notes": str(raw.get("difficulty_notes") or "").strip()[:500],
    }


# ---------------------------------------------------------------- mining


def _evidence_blob(research: dict[str, Any], *, max_chars: int = 9000) -> str:
    parts: list[str] = []
    for i, s in enumerate(research.get("sources") or [], 1):
        lines = [f"[{i}] {s.get('title') or '—'}", f"URL: {s.get('url') or '—'}"]
        if s.get("snippet"):
            lines.append(f"خلاصه جستجو: {s['snippet']}")
        for ex in (s.get("excerpts") or [])[:6]:
            lines.append(f"- نمونه تست: {ex}")
        parts.append("\n".join(lines))
    summary = ((research.get("gemini") or {}).get("summary") or "").strip()
    if summary:
        parts.append(f"[خلاصه جستجوی Gemini]\n{summary[:1500]}")
    blob = "\n\n".join(parts)
    return blob[:max_chars]


def _scope_block(scope: Any) -> str:
    if scope is None:
        return ""
    lines = [f"فصل {scope.chapter_num}: {scope.chapter_title}", "بخش‌های این فصل در کتاب:"]
    lines += [f"- {t}" for t in scope.section_titles]
    return "\n".join(lines) + "\n"


def _context_lines(profile: dict[str, Any], scope: Any) -> str:
    if scope is None:
        return (
            f"مبحث جزوه: {topic_line(profile)}\n"
            f"مفاهیم کلیدی: {'، '.join(profile.get('key_concepts') or [])}\n"
        )
    # Book-grounded context only: LLM-detected topics/concepts can drift off-chapter.
    head = " — ".join(b for b in (profile.get("subject"), profile.get("grade"), profile.get("track")) if b)
    return f"منبع: {head}\n{_scope_block(scope)}"


def extract_patterns(
    profile: dict[str, Any],
    research: dict[str, Any],
    provider: LLMProvider,
    scope: Any = None,
) -> dict[str, Any]:
    user = (
        f"{_context_lines(profile, scope)}\n"
        "--- شواهد وب (تست‌های واقعی؛ فقط برای الگو، کپی ممنوع) ---\n"
        f"{_evidence_blob(research)}\n--- پایان ---\n"
        "فقط JSON بسته الگو را برگردان."
    )
    raw = provider.complete_json(PATTERN_SYSTEM, user)
    return normalize_pattern_pack(raw, sources=research.get("sources") or [])


def _section_outline(sections: list[Any], titles: list[str], *, per_section: int = 400) -> str:
    from darsamooz.chapter_scope import norm_fa

    lines: list[str] = []
    for title in titles:
        key = norm_fa(title)
        body = ""
        for sec in sections:
            if key and key in norm_fa(sec.title or ""):
                body = " ".join((sec.text or "").split())[:per_section]
                break
        lines.append(f"- {title}: {body or '—'}")
    return "\n".join(lines)


def audit_patterns_scope(
    patterns: list[dict[str, Any]],
    scope: Any,
    provider: LLMProvider,
    sections: list[Any] | None = None,
) -> dict[str, Any]:
    """Ask the LLM which patterns are really taught in this chapter.

    Returns ``{"ok": bool, "verdicts": {id: {...}}, "error": str|None}``. A failed or
    malformed audit is reported (ok=False) — never treated as "all on-chapter" silently.
    """
    if not patterns:
        return {"ok": True, "verdicts": {}, "error": None}
    from darsamooz.chapter_scope import book_snippets

    blocks: list[str] = []
    for p in patterns:
        snips = book_snippets(sections or [], f"{p.get('name_fa') or ''} {p.get('stem_template') or ''}")
        blocks.append(
            f"{p['id']}: {p.get('name_fa')} | اسکلت: {p.get('stem_template')} | "
            f"عددی: {'بله' if p.get('numeric') else 'خیر'} | بخش اعلام‌شده: {p.get('section') or '—'}\n"
            + ("  شواهد از متن کتاب:\n" + "\n".join(f"  «{x}»" for x in snips) if snips else "  شواهد از متن کتاب: —")
        )
    plist = "\n".join(blocks)
    user = (
        f"{_scope_block(scope)}\nخلاصه هر بخش از متن کتاب:\n"
        f"{_section_outline(sections or [], scope.section_titles)}\n\n"
        f"الگوهای پیشنهادی:\n{plist}\n\nفقط JSON برگردان."
    )
    try:
        raw = provider.complete_json(SCOPE_AUDIT_SYSTEM, user)
    except Exception as exc:  # noqa: BLE001 — reported to caller
        return {"ok": False, "verdicts": {}, "error": f"audit_error: {exc}"}
    items = raw.get("patterns") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "verdicts": {}, "error": "audit_malformed: no patterns list"}
    verdicts: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict) or "on_chapter" not in it:
            continue
        pid = str(it.get("id") or "").strip()
        oc = it.get("on_chapter")
        if isinstance(oc, str):
            oc = oc.strip().lower() in {"true", "1", "yes", "بله"}
        verdicts[pid] = {
            "on_chapter": bool(oc),
            "section": str(it.get("section") or "").strip(),
            "reason": str(it.get("reason") or "").strip()[:200],
        }
    known = {p["id"] for p in patterns}
    if not (known & set(verdicts)):
        return {"ok": False, "verdicts": {}, "error": "audit_malformed: no verdict for any pattern"}
    return {"ok": True, "verdicts": verdicts, "error": None}


def scope_filter_pack(
    pack_body: dict[str, Any],
    scope: Any,
    provider: LLMProvider,
    *,
    sections: list[Any] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """Drop off-chapter patterns (LLM audit + deterministic section/term checks)."""
    from darsamooz.chapter_scope import filter_patterns_to_chapter

    patterns = list(pack_body.get("patterns") or [])
    audit = audit_patterns_scope(patterns, scope, provider, sections)
    dropped: list[dict[str, Any]] = []
    if audit["ok"]:
        kept_after_audit = []
        for p in patterns:
            v = audit["verdicts"].get(p["id"])
            if v is None:
                kept_after_audit.append({**p})
                continue
            if not v["on_chapter"]:
                dropped.append(
                    {"name_fa": p.get("name_fa"), "section": p.get("section"),
                     "reason": f"off_chapter (audit): {v['reason'] or '—'}"}
                )
                continue
            kept_after_audit.append({**p, "section": v["section"] or p.get("section")})
        patterns = kept_after_audit
    else:
        log(f"هشدار: بررسی هم‌فصل بودن الگوها انجام نشد ({audit['error']}); فقط بررسی قطعی اعمال شد.")
    kept, det_dropped = filter_patterns_to_chapter(patterns, scope)
    dropped += det_dropped
    for d in dropped:
        log(f"الگو حذف شد (خارج از فصل): «{d.get('name_fa')}» — {d.get('reason')}")
    return {
        **pack_body,
        "patterns": kept,
        "dropped_patterns": dropped,
        "scope_audit": {"ok": audit["ok"], "error": audit["error"], "verdicts": audit["verdicts"]},
    }


def llm_only_patterns(profile: dict[str, Any], provider: LLMProvider, scope: Any = None) -> dict[str, Any]:
    user = (
        f"{_context_lines(profile, scope)}\n"
        "شواهد وب در دسترس نیست. بر اساس شناخت عمومی از آزمون‌های ایران (کنکور، نهایی، آزمایشی) "
        "برای همین مبحث الگو بساز؛ evidence را خالی بگذار. فقط JSON بسته الگو را برگردان."
    )
    raw = provider.complete_json(PATTERN_SYSTEM, user)
    return normalize_pattern_pack(raw, sources=[])


def _call_meta(provider: LLMProvider) -> dict[str, Any]:
    call = getattr(provider, "last_call", None) or {}
    return {"provider": call.get("provider"), "model": call.get("model")}


def mine_web_patterns(
    book_slug: str,
    chapter: int,
    *,
    provider: LLMProvider,
    settings: Settings | None = None,
    profile: dict[str, Any] | None = None,
    backend: str = "auto",
    max_sources: int = 8,
    max_queries: int | None = None,
    llm_only: bool = False,
    fetch_pages: bool = True,
    on_log: Callable[[str], None] | None = None,
    research_fn: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search the web, save sources, extract patterns, save the pack.

    Raises :class:`darsamooz.web_search.WebSearchError` when search finds nothing and
    ``llm_only`` is False. The failure is also written to web_sources.json so the UI
    can show it. With ``llm_only=True`` the web step is skipped and the pack is
    clearly marked ``status=llm_only`` with zero sources.
    """
    from darsamooz.web_search import WebSearchError, web_research

    settings = settings or get_settings()
    log = on_log or (lambda _m: None)
    profile = profile or load_topic_profile(book_slug, chapter, settings)
    if not profile:
        raise FileNotFoundError(
            "پروفایل موضوع وجود ندارد. اول «تشخیص موضوع» (analyze-topic) را اجرا کنید."
        )
    now = datetime.now(timezone.utc).isoformat()

    from darsamooz.chapter_scope import ground_search_queries, load_scope

    scope, scope_sections = load_scope(book_slug, chapter, settings)
    grounding: dict[str, Any] = {"queries": profile.get("search_queries") or [], "dropped": []}
    if scope is not None:
        from darsamooz.web_search import max_queries_per_run

        grounding = ground_search_queries(
            profile, scope, max_queries=max(1, int(max_queries)) if max_queries else max_queries_per_run()
        )
        for d in grounding["dropped"]:
            log(f"کوئری حذف شد (خارج از فصل): «{d['query']}» — {d['reason']}")
        log("کوئری‌های جستجو (بر اساس بخش‌های فصل): " + " | ".join(grounding["queries"]))
    queries = grounding["queries"]

    research: dict[str, Any]
    if llm_only:
        log("الگوبرداری بدون وب (فقط مدل)…")
        research = {
            "ok": False,
            "skipped": True,
            "backend": None,
            "attempts": [],
            "queries": queries,
            "sources": [],
            "excerpt_count": 0,
            "note": "کاربر ادامه بدون جستجوی وب را انتخاب کرد.",
        }
        prev = load_web_sources(book_slug, chapter, settings) or {}
        if prev.get("error"):
            research["previous_error"] = prev.get("error")
            research["attempts"] = prev.get("attempts") or []
        pack_body = llm_only_patterns(profile, provider, scope)
        status = "llm_only"
    else:
        try:
            research = (research_fn or web_research)(
                queries,
                topic_line=topic_line(profile),
                backend=backend,
                max_sources=max_sources,
                max_queries=max_queries,
                fetch_pages=fetch_pages,
                on_log=log,
            )
        except WebSearchError as exc:
            save_web_sources(
                {
                    "ok": False,
                    "error": str(exc),
                    "attempts": exc.attempts,
                    "queries": queries,
                    "dropped_queries": grounding["dropped"],
                    "sources": [],
                    "searched_at": now,
                },
                book_slug,
                chapter,
                settings,
            )
            raise
        research["searched_at"] = now
        research["dropped_queries"] = grounding["dropped"]
        save_web_sources(research, book_slug, chapter, settings)
        log(
            f"{len(research.get('sources') or [])} منبع، "
            f"{research.get('excerpt_count', 0)} نمونه تست استخراج شد. استخراج الگو…"
        )
        pack_body = extract_patterns(profile, research, provider, scope)
        status = "web"

    if scope is not None:
        pack_body = scope_filter_pack(pack_body, scope, provider, sections=scope_sections, log=log)

    pack = {
        "status": status,
        "book_slug": book_slug,
        "chapter_num": chapter,
        "topic": topic_line(profile),
        "search_backend": research.get("backend"),
        "search_attempts": research.get("attempts") or [],
        "search_queries": research.get("queries") or [],
        "dropped_queries": grounding["dropped"],
        "chapter_sections": scope.section_titles if scope is not None else [],
        "search_requests": research.get("search_requests"),
        "sources": [
            {"title": s.get("title"), "url": s.get("url")}
            for s in research.get("sources") or []
        ],
        "sources_count": len(research.get("sources") or []),
        "web_excerpt_count": research.get("excerpt_count", 0),
        **pack_body,
        "note": LLM_ONLY_NOTE if status == "llm_only" else "",
        "created_at": now,
        "extracted_by": _call_meta(provider),
    }
    if not pack["patterns"]:
        save_pattern_pack(pack, book_slug, chapter, settings)
        raise RuntimeError(
            "هیچ الگوی معتبر و هم‌فصلی باقی نماند"
            + (f" ({len(pack.get('dropped_patterns') or [])} الگو خارج از فصل حذف شد)" if pack.get("dropped_patterns") else "")
            + "؛ دوباره تلاش کنید."
        )
    save_pattern_pack(pack, book_slug, chapter, settings)
    return pack


def update_pattern_pack(
    book_slug: str,
    chapter: int,
    edited: dict[str, Any],
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Save a user-edited pack (UI/CLI). Keeps provenance fields, re-normalizes patterns."""
    current = load_pattern_pack(book_slug, chapter, settings) or {}
    raw_patterns = edited.get("patterns", current.get("patterns") or [])
    body = normalize_pattern_pack(
        {
            **current,
            **{k: v for k, v in edited.items() if k != "patterns"},
            # evidence urls are already resolved — keep them as-is below
            "patterns": [
                {**p, "evidence": []} for p in raw_patterns if isinstance(p, dict)
            ],
        }
    )
    for new, old in zip(body["patterns"], [p for p in raw_patterns if isinstance(p, dict)]):
        new["evidence_urls"] = _str_list(old.get("evidence_urls"), 5, 500)
    pack = {**current, **body}
    pack["edited_at"] = datetime.now(timezone.utc).isoformat()
    if not pack.get("patterns"):
        raise ValueError("بسته الگو باید حداقل یک الگو داشته باشد.")
    save_pattern_pack(pack, book_slug, chapter, settings)
    return pack


# ---------------------------------------------------------------- prompt + dedupe


def active_patterns(pack: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not pack:
        return []
    return [p for p in pack.get("patterns") or [] if p.get("enabled", True)]


def format_pattern_for_prompt(pack: dict[str, Any], pattern: dict[str, Any] | None) -> str:
    lines = [
        "--- الگوی تست (از تحلیل تست‌های واقعی؛ فقط ساختار — محتوا فقط از یونیت) ---",
        f"مبحث: {pack.get('topic') or '—'}",
    ]
    if pack.get("exam_styles"):
        lines.append("سبک آزمون: " + "، ".join(pack["exam_styles"]))
    if pattern:
        lines += [
            f"الگوی هدف ({pattern.get('id')}): {pattern.get('name_fa')}"
            + (f" — بخش: {pattern['section']}" if pattern.get("section") else ""),
            f"اسکلت صورت سؤال: {pattern.get('stem_template') or '—'}",
            f"شکل گزینه‌ها: {pattern.get('option_format') or pack.get('option_format_notes') or '—'}",
            f"عددی/محاسباتی: {'بله' if pattern.get('numeric') else 'خیر'}",
        ]
        if pattern.get("distractor_strategies"):
            lines.append("ساخت گزینه‌های غلط:")
            lines += [f"- {d}" for d in pattern["distractor_strategies"]]
        if pattern.get("traps"):
            lines.append("دام‌ها: " + "؛ ".join(pattern["traps"]))
    if pack.get("general_traps"):
        lines.append("دام‌های عمومی: " + "؛ ".join(pack["general_traps"]))
    lines.append(
        "صورت سؤال، اعداد و گزینه‌ها را از نو و فقط بر اساس متن یونیت بنویس؛ "
        "هیچ تست وبی را بازنویسی یا کپی نکن."
    )
    lines.append("--- پایان الگو ---")
    return "\n".join(lines)


_AR = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "\u200c": " "})
_NONWORD = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)
_DIAC = re.compile(r"[\u064B-\u065F\u0670]")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _norm(text: str) -> str:
    s = (text or "").translate(_AR).translate(_FA_DIGITS)
    s = _DIAC.sub("", s)
    s = _NONWORD.sub(" ", s)
    return " ".join(s.split()).lower()


def _shingles(text: str, n: int = 5) -> set[str]:
    s = _norm(text).replace(" ", "")
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def web_question_texts(web_sources: dict[str, Any] | None) -> list[str]:
    texts: list[str] = []
    for s in (web_sources or {}).get("sources") or []:
        for ex in s.get("excerpts") or []:
            if ex:
                texts.append(str(ex))
                stem = str(ex).split(" / ")[0]
                if stem and stem != ex:
                    texts.append(stem)
        if s.get("snippet"):
            texts.append(str(s["snippet"]))
    return texts


def web_similarity(stem: str, web_texts: list[str]) -> tuple[float, str | None]:
    """Max containment of the stem's 5-char shingles inside any web text (0..1)."""
    sa = _shingles(stem)
    if len(sa) < 8:
        return 0.0, None
    best, best_txt = 0.0, None
    for t in web_texts:
        sb = _shingles(t)
        if not sb:
            continue
        score = len(sa & sb) / len(sa)
        if score > best:
            best, best_txt = score, t
    return best, best_txt


WEB_DUP_THRESHOLD = 0.6


def is_web_near_duplicate(stem: str, web_texts: list[str], threshold: float = WEB_DUP_THRESHOLD) -> tuple[bool, float, str | None]:
    score, match = web_similarity(stem, web_texts)
    return score >= threshold, score, match
