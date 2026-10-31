from __future__ import annotations

from pathlib import Path
from typing import Literal

from darsamooz.config import Settings, get_settings
from darsamooz.models import Section
from darsamooz.retrieval.bm25 import (
    BM25Index,
    build_bm25,
    load_bm25,
    save_bm25,
    score_bm25,
)
from darsamooz.retrieval.tfidf import (
    TfidfIndex,
    build_tfidf,
    load_tfidf,
    save_tfidf,
    score_tfidf,
)
from darsamooz.sections import load_sections, sections_dir

RetrievalMethod = Literal["bm25", "tfidf"]


def bm25_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "index_bm25.json"


def tfidf_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "index_tfidf.json"


def build_chapter_index(
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> dict[str, object]:
    """Build and persist BM25 + TF-IDF indexes next to sections.json."""
    settings = settings or get_settings()
    sections = load_sections(book_slug, chapter, settings)
    if not sections:
        raise FileNotFoundError(
            f"No sections for {book_slug} chapter {chapter}. Run ingest first."
        )
    bm = build_bm25(sections)
    tf = build_tfidf(sections)
    bp = save_bm25(bm, bm25_path(book_slug, chapter, settings))
    tp = save_tfidf(tf, tfidf_path(book_slug, chapter, settings))
    return {"bm25": bp, "tfidf": tp, "section_count": len(sections)}


def build_all_chapter_indexes(
    book_slug: str = "physics10-riazi-110209",
    chapters: list[int] | None = None,
    settings: Settings | None = None,
) -> dict[int, dict[str, object]]:
    settings = settings or get_settings()
    from darsamooz.ingest import PHYSICS10_CHAPTERS

    nums = chapters or [n for n, _ in PHYSICS10_CHAPTERS]
    out: dict[int, dict[str, object]] = {}
    for n in nums:
        try:
            out[n] = build_chapter_index(book_slug, n, settings)
        except FileNotFoundError:
            continue
    return out


def _load_or_build(
    book_slug: str,
    chapter: int,
    settings: Settings,
) -> tuple[list[Section], BM25Index, TfidfIndex]:
    sections = load_sections(book_slug, chapter, settings)
    if not sections:
        raise FileNotFoundError(
            f"No sections for {book_slug} chapter {chapter}. Run ingest first."
        )
    bp = bm25_path(book_slug, chapter, settings)
    tp = tfidf_path(book_slug, chapter, settings)
    if bp.is_file() and tp.is_file():
        return sections, load_bm25(bp), load_tfidf(tp)
    # Persist so repeated retrieves are cheap (per plan).
    bm = build_bm25(sections)
    tf = build_tfidf(sections)
    save_bm25(bm, bp)
    save_tfidf(tf, tp)
    return sections, bm, tf


def retrieve_sections(
    book_slug: str,
    chapter: int,
    query: str,
    *,
    k: int = 5,
    method: RetrievalMethod = "bm25",
    settings: Settings | None = None,
) -> list[tuple[Section, float]]:
    """
    Rank chapter sections for a query.

    Not wired into generate/planner — offline Phase 2 API for future use.
    """
    settings = settings or get_settings()
    sections, bm, tf = _load_or_build(book_slug, chapter, settings)
    by_id = {s.id: s for s in sections}

    if method == "tfidf":
        ranked = score_tfidf(tf, query)
    else:
        ranked = score_bm25(bm, query)

    out: list[tuple[Section, float]] = []
    for sid, score in ranked[: max(1, k)]:
        sec = by_id.get(sid)
        if sec is not None:
            out.append((sec, float(score)))
    return out
