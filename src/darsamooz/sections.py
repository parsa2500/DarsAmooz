from __future__ import annotations

import json
import re
from pathlib import Path

from darsamooz.chunking import chunk_chapter
from darsamooz.config import Settings, get_settings
from darsamooz.models import Chapter, Section

# Iranian textbook subsection headers: "1ـ 2مدلسازی در فیزیک" at line start.
# Avoids "شکل 1ـ 1", "فعالیت 1ـ 1", "پرسش …", "جدول …" which do not start with the digit.
_SECTION_HEADER = re.compile(
    r"(?m)^(?:\f)?\s*([1-5۱-۵])\s*[ـ\-–—]+\s*([0-9۰-۹]{1,2})\s*([^\n\f]{2,100})"
)

_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")

_SKIP_TITLE_PREFIXES = (
    "تمامی",
    "مطالب",
    "شکل",
    "جدول",
    "فعالیت",
    "پرسش",
    "آزمایش",
)


def _to_int(s: str) -> int:
    return int(s.translate(_DIGIT_MAP))


def _clean_title(raw: str) -> str:
    t = raw.strip()
    t = re.sub(r"\s+", " ", t)
    return t[:120]


def split_sections(chapter: Chapter, *, max_unit_chars: int = 3500) -> list[Section]:
    """
    Split a chapter into subsection units.

    Prefer numbered headers (Nـ M …). Fall back to paragraph chunks when
    fewer than two headers are found.
    """
    text = chapter.text or ""
    if not text.strip():
        return []

    matches: list[tuple[int, int, str]] = []
    for m in _SECTION_HEADER.finditer(text):
        try:
            ch_n = _to_int(m.group(1))
            sec_n = _to_int(m.group(2))
        except ValueError:
            continue
        if ch_n != chapter.number:
            continue
        title = _clean_title(m.group(3))
        if not title or any(title.startswith(p) for p in _SKIP_TITLE_PREFIXES):
            continue
        if not any("\u0600" <= c <= "\u06FF" for c in title):
            continue
        matches.append((m.start(), sec_n, title))

    # Dedupe identical starts; keep first occurrence of each sec_n order by position
    if len(matches) >= 2:
        sections: list[Section] = []
        for i, (start, sec_n, title) in enumerate(matches):
            end = matches[i + 1][0] if i + 1 < len(matches) else len(text)
            body = text[start:end].strip()
            if len(body) < 40:
                continue
            sid = f"ch{chapter.number}-s{sec_n:02d}"
            # Disambiguate duplicate sec numbers
            if any(s.id == sid for s in sections):
                sid = f"ch{chapter.number}-s{sec_n:02d}-{i+1:02d}"
            if len(body) <= max_unit_chars:
                sections.append(
                    Section(
                        id=sid,
                        chapter=chapter.number,
                        title=title,
                        text=body,
                        order=len(sections),
                        char_start=start,
                        char_end=end,
                    )
                )
            else:
                # Oversized unit: hard-split preserving section id prefix
                fake = Chapter(
                    number=chapter.number,
                    title=title,
                    text=body,
                    start_char=start,
                    end_char=end,
                )
                for j, piece in enumerate(chunk_chapter(fake, max_chars=max_unit_chars, overlap=80)):
                    sections.append(
                        Section(
                            id=f"{sid}-p{j+1:02d}",
                            chapter=chapter.number,
                            title=f"{title} (بخش {j+1})",
                            text=piece,
                            order=len(sections),
                            char_start=start,
                            char_end=end,
                        )
                    )
        if len(sections) >= 2:
            return sections

    # Fallback: paragraph-aware chunks as synthetic units
    fake = Chapter(
        number=chapter.number,
        title=chapter.title,
        text=text,
        start_char=0,
        end_char=len(text),
    )
    chunks = chunk_chapter(fake, max_chars=max_unit_chars, overlap=80)
    out: list[Section] = []
    offset = 0
    for i, piece in enumerate(chunks):
        idx = text.find(piece[:80], offset) if piece else -1
        start = idx if idx >= 0 else offset
        end = start + len(piece)
        offset = max(offset, end - 40)
        out.append(
            Section(
                id=f"ch{chapter.number}-u{i+1:03d}",
                chapter=chapter.number,
                title=f"{chapter.title or f'فصل {chapter.number}'} — واحد {i+1}",
                text=piece,
                order=i,
                char_start=start,
                char_end=end,
            )
        )
    return out


def sections_dir(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.extracted_dir) / book_slug / f"ch{chapter}"


def sections_path(book_slug: str, chapter: int, settings: Settings | None = None) -> Path:
    return sections_dir(book_slug, chapter, settings) / "sections.json"


def write_sections(
    sections: list[Section],
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> Path:
    path = sections_path(book_slug, chapter, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "book_slug": book_slug,
        "chapter": chapter,
        "count": len(sections),
        "sections": [s.model_dump(mode="json") for s in sections],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_sections(
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
    *,
    rebuild_if_missing: bool = True,
) -> list[Section]:
    """Load sections.json, or build from extracted chapter text if missing."""
    settings = settings or get_settings()
    path = sections_path(book_slug, chapter, settings)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("sections") or []
        return [Section.model_validate(x) for x in items]

    if not rebuild_if_missing:
        return []

    from darsamooz.ingest import load_extracted_chapters

    chapters = {c.number: c for c in load_extracted_chapters(book_slug, settings)}
    ch = chapters.get(chapter)
    if not ch:
        return []
    sections = split_sections(ch)
    if sections:
        write_sections(sections, book_slug, chapter, settings)
    return sections
