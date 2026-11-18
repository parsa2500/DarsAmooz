"""Step 1 of the handout flow: detect precisely what an uploaded جزوه is about.

The LLM reads the chapter text (title, section titles, a text sample) and returns
a structured *topic profile*. The profile is saved per source/chapter so the user
can review/edit/confirm it in the web UI before web pattern mining.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.ingest import chapter_title_for, read_book_meta
from darsamooz.llm import LLMProvider
from darsamooz.chapter_scope import clean_section_titles, filter_topic_items, ground_search_queries, load_scope
from darsamooz.sections import load_sections, sections_dir

TOPIC_SYSTEM = """You classify Iranian school study material (جزوه / کتاب درسی) precisely.
Read the given Persian text and return ONLY JSON:
{
  "subject": "e.g. فیزیک",
  "grade": "e.g. دهم",
  "track": "e.g. ریاضی و فیزیک | تجربی | انسانی | عمومی",
  "book": "e.g. فیزیک (۱)",
  "chapter": "chapter/فصل title as in the material",
  "topics": ["مباحث اصلی (3-8 items, specific)"],
  "key_concepts": ["مفاهیم/فرمول‌های کلیدی (5-12 items)"],
  "exam_targets": ["کنکور سراسری", "امتحان نهایی", "آزمون‌های آزمایشی", ...],
  "search_queries": ["3-4 Persian web queries to find real 4-option tests on exactly this chapter"],
  "summary_fa": "one or two Persian sentences",
  "confidence": 0.0
}
Rules:
- Base everything on the text; do not guess a different subject.
- search_queries should look like what an Iranian student types, e.g. "تست فیزیک دهم فصل اول اندازه گیری کنکور".
- search_queries: Persian words only (no English like "10th grade"), 3-7 words each.
- book = the textbook name (e.g. "فیزیک (۱)"), not the chapter title.
- topics and search_queries MUST come from the listed section titles («عنوان بخش‌ها»). A formula or
  law that the text only mentions as an example (e.g. F = ma used to explain units) is NOT a topic.
- Use only words that appear in the text or are ordinary Persian; never invent terms
  (wrong: «فاکتور دوم نیوتون»).
- Persian values. confidence in [0,1].
"""

_LIST_FIELDS = ("topics", "key_concepts", "exam_targets", "search_queries")
_STR_FIELDS = ("subject", "grade", "track", "book", "chapter", "summary_fa")


def topic_profile_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "topic_profile.json"


def load_topic_profile(
    book_slug: str, chapter: int, settings: Settings | None = None
) -> dict[str, Any] | None:
    path = topic_profile_path(book_slug, chapter, settings)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_topic_profile(
    profile: dict[str, Any],
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> Path:
    path = topic_profile_path(book_slug, chapter, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def normalize_topic_profile(raw: dict[str, Any] | None, *, fallback_chapter: str = "") -> dict[str, Any]:
    raw = raw or {}
    out: dict[str, Any] = {}
    for k in _STR_FIELDS:
        out[k] = str(raw.get(k) or "").strip()[:300]
    if not out["chapter"]:
        out["chapter"] = fallback_chapter
    for k in _LIST_FIELDS:
        vals = raw.get(k) or []
        if isinstance(vals, str):
            vals = [v for v in vals.replace("،", ",").split(",")]
        cleaned: list[str] = []
        for v in vals:
            s = str(v).strip()
            if s and s not in cleaned:
                cleaned.append(s[:200])
        out[k] = cleaned[:12]
    try:
        conf = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    out["confidence"] = max(0.0, min(1.0, conf))
    if not out["search_queries"]:
        out["search_queries"] = default_search_queries(out)
    else:
        out["search_queries"] = out["search_queries"][:8]
    return out


def default_search_queries(profile: dict[str, Any]) -> list[str]:
    subj = profile.get("subject") or ""
    grade = profile.get("grade") or ""
    chap = profile.get("chapter") or ""
    base = " ".join(x for x in (subj, grade) if x).strip()
    qs = [f"تست {base} {chap}".strip(), f"تست کنکور {subj} {chap}".strip()]
    if profile.get("topics"):
        qs.append(f"تست چهارگزینه ای {profile['topics'][0]} {base}".strip())
    return [" ".join(q.split()) for q in qs if q.strip()]


def topic_line(profile: dict[str, Any]) -> str:
    bits = [
        profile.get("subject"),
        profile.get("grade"),
        profile.get("track"),
        profile.get("chapter"),
    ]
    line = " — ".join(b for b in bits if b)
    topics = profile.get("topics") or []
    if topics:
        line += " | مباحث: " + "، ".join(topics[:6])
    return line


def build_topic_sample(book_slug: str, chapter: int, settings: Settings | None = None, *, max_chars: int = 7000) -> str:
    settings = settings or get_settings()
    sections = load_sections(book_slug, chapter, settings)
    if not sections:
        raise FileNotFoundError(
            f"برای {book_slug} فصل {chapter} متنی پیدا نشد. اول PDF را بخوانید (ingest)."
        )
    meta = read_book_meta(book_slug, settings) or {}
    title = chapter_title_for(book_slug, chapter, settings)
    head = [
        f"عنوان سند: {meta.get('title') or book_slug}",
        f"نام فایل: {meta.get('source_filename') or '—'}",
        f"عنوان فصل: {title}",
        "عنوان بخش‌ها: " + " | ".join(clean_section_titles(sections, title)[:25]),
        "--- نمونه متن ---",
    ]
    # Take text from the beginning, middle and end so long chapters are represented.
    full = "\n".join(s.text for s in sections)
    if len(full) <= max_chars:
        sample = full
    else:
        third = max_chars // 3
        mid = len(full) // 2
        sample = (
            full[:third]
            + "\n...\n"
            + full[mid - third // 2 : mid + third // 2]
            + "\n...\n"
            + full[-third:]
        )
    return "\n".join(head) + "\n" + sample


def analyze_topic(
    book_slug: str,
    chapter: int,
    *,
    provider: LLMProvider,
    settings: Settings | None = None,
    save: bool = True,
) -> dict[str, Any]:
    settings = settings or get_settings()
    sample = build_topic_sample(book_slug, chapter, settings)
    raw = provider.complete_json(TOPIC_SYSTEM, sample)
    profile = normalize_topic_profile(
        raw, fallback_chapter=chapter_title_for(book_slug, chapter, settings)
    )
    apply_chapter_scope(profile, book_slug, chapter, settings)
    call = getattr(provider, "last_call", None) or {}
    profile.update(
        {
            "book_slug": book_slug,
            "chapter_num": chapter,
            "confirmed": False,
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "detected_by": {"provider": call.get("provider"), "model": call.get("model")},
        }
    )
    if save:
        save_topic_profile(profile, book_slug, chapter, settings)
    return profile


def apply_chapter_scope(
    profile: dict[str, Any], book_slug: str, chapter: int, settings: Settings | None = None
) -> dict[str, Any]:
    """Ground queries/topics in the chapter's real sections; record what was dropped."""
    scope, _ = load_scope(book_slug, chapter, settings)
    if scope is None:
        return profile
    from darsamooz.web_search import max_queries_per_run

    dropped_items: list[dict[str, str]] = []
    for key in ("topics", "key_concepts"):
        kept, dropped = filter_topic_items(profile.get(key) or [], scope)
        profile[key] = kept
        dropped_items += [{**d, "field": key} for d in dropped]
    g = ground_search_queries(profile, scope, max_queries=max_queries_per_run())
    profile["search_queries"] = g["queries"]
    profile["dropped_search_queries"] = g["dropped"]
    profile["dropped_topic_items"] = dropped_items
    profile["chapter_sections"] = g["section_titles"]
    return profile


def update_topic_profile(
    book_slug: str,
    chapter: int,
    patch: dict[str, Any],
    *,
    confirm: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Apply a user edit from UI/CLI and mark the profile confirmed."""
    current = load_topic_profile(book_slug, chapter, settings) or {}
    merged = {**current, **{k: v for k, v in patch.items() if k in (*_STR_FIELDS, *_LIST_FIELDS, "confidence")}}
    profile = normalize_topic_profile(merged, fallback_chapter=current.get("chapter") or "")
    for k in ("book_slug", "chapter_num", "detected_at", "detected_by"):
        if k in current:
            profile[k] = current[k]
    profile.setdefault("book_slug", book_slug)
    profile.setdefault("chapter_num", chapter)
    profile["confirmed"] = bool(confirm)
    profile["edited_at"] = datetime.now(timezone.utc).isoformat()
    save_topic_profile(profile, book_slug, chapter, settings)
    return profile
