from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from darsamooz.config import Settings, get_settings
from darsamooz.models import Chapter

PHYSICS10_SLUG = "physics10-riazi-110209"

# Canonical chapter titles for Physics 1 (math track), book code 110209
PHYSICS10_CHAPTERS: list[tuple[int, str]] = [
    (1, "فیزیک و اندازه‌گیری"),
    (2, "ویژگی‌های فیزیکی مواد"),
    (3, "کار، انرژی و توان"),
    (4, "دما و گرما"),
    (5, "ترمودینامیک"),
]

_DIGIT_MAP = {
    "۰": "0",
    "۱": "1",
    "۲": "2",
    "۳": "3",
    "۴": "4",
    "۵": "5",
    "۶": "6",
    "۷": "7",
    "۸": "8",
    "۹": "9",
}


def clean_bidi(text: str) -> str:
    """Remove bidirectional / format controls that break Persian chapter detection."""
    out = []
    for c in text:
        if unicodedata.category(c) == "Cf" and c not in ("\n", "\t"):
            continue
        if c in "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\u200a":
            continue
        out.append(c)
    return "".join(out)


def extract_pdf_text(pdf_path: Path, out_txt: Path | None = None) -> str:
    """Extract text with poppler `pdftotext`. Requires poppler-utils."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)
    if shutil.which("pdftotext") is None:
        raise RuntimeError(
            "pdftotext not found. Install poppler-utils (apt install poppler-utils)."
        )
    result = subprocess.run(
        ["pdftotext", "-enc", "UTF-8", str(pdf_path), "-"],
        check=True,
        capture_output=True,
    )
    raw = result.stdout.decode("utf-8", errors="replace")
    cleaned = clean_bidi(raw)
    if out_txt:
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(cleaned, encoding="utf-8")
    return cleaned


def _normalize_chapter_num(raw: str) -> int | None:
    chars = "".join(_DIGIT_MAP.get(c, c) for c in raw.strip())
    if not chars.isdigit():
        return None
    n = int(chars)
    if 1 <= n <= 20:
        return n
    return None


def _find_chapter_body_starts(text: str) -> dict[int, int]:
    """
    Locate approximate start offsets for each chapter body.

    Prefer form-feed + 'فصل N' markers that appear as running headers
    after the table of contents.
    """
    starts: dict[int, int] = {}
    for m in re.finditer(r"(?:^|\f)\s*فصل\s*([1-5۱-۵])\b", text, flags=re.MULTILINE):
        n = _normalize_chapter_num(m.group(1))
        if n is None or n in starts:
            continue
        if m.start() < 8000:
            continue
        starts[n] = m.start()
    return starts


def split_chapters(text: str, titles: list[tuple[int, str]] | None = None) -> list[Chapter]:
    titles = titles or PHYSICS10_CHAPTERS
    starts = _find_chapter_body_starts(text)

    if len(starts) < 2:
        starts = {}
        for m in re.finditer(r"فصل\s*:\s*([1-5۱-۵])", text):
            n = _normalize_chapter_num(m.group(1))
            if n is not None and n not in starts:
                starts[n] = m.start()

    chapters: list[Chapter] = []
    for i, (num, title) in enumerate(titles):
        start = starts.get(num, 0)
        if i + 1 < len(titles):
            next_num = titles[i + 1][0]
            end = starts.get(next_num, len(text))
        else:
            end = len(text)
        if end <= start:
            end = len(text)
        body = text[start:end].strip()
        chapters.append(
            Chapter(number=num, title=title, start_char=start, end_char=end, text=body)
        )
    return chapters


def split_chapters_generic(text: str, fallback_title: str = "سند") -> list[Chapter]:
    """
    Split arbitrary textbook-like PDF text into chapters.

    Prefer 'فصل N' markers (1–20). If fewer than two chapters are found,
    treat the whole document as chapter 1.
    """
    starts: dict[int, tuple[int, str]] = {}
    pattern = r"(?:^|\f)\s*فصل\s*([0-9۰-۹]{1,2})\s*[:：\-]?\s*([^\n\f]{0,80})"
    for m in re.finditer(pattern, text, flags=re.MULTILINE):
        n = _normalize_chapter_num(m.group(1))
        if n is None or n in starts:
            continue
        title = (m.group(2) or "").strip()
        title = re.sub(r"\s+\d{1,4}\s*$", "", title).strip()
        if not title or len(title) < 2:
            title = f"فصل {n}"
        starts[n] = (m.start(), title)

    if len(starts) < 2:
        body = text.strip()
        if not body:
            raise ValueError("متن قابل استخراج از PDF خالی است.")
        return [
            Chapter(
                number=1,
                title=fallback_title or "سند",
                start_char=0,
                end_char=len(text),
                text=body,
            )
        ]

    nums = sorted(starts.keys())
    chapters: list[Chapter] = []
    for i, num in enumerate(nums):
        start, title = starts[num]
        end = starts[nums[i + 1]][0] if i + 1 < len(nums) else len(text)
        if end <= start:
            end = len(text)
        body = text[start:end].strip()
        chapters.append(
            Chapter(number=num, title=title, start_char=start, end_char=end, text=body)
        )
    return chapters


def make_document_slug(filename: str) -> str:
    """Stable filesystem-safe slug for an uploaded PDF name."""
    stem = Path(filename).stem
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-_.").lower()[:40] or "doc"
    digest = hashlib.sha1(filename.encode("utf-8")).hexdigest()[:8]
    return f"doc-{safe}-{digest}"


def book_meta_path(book_slug: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.extracted_dir) / book_slug / "meta.json"


def write_book_meta(
    book_slug: str,
    *,
    title: str,
    chapters: list[Chapter],
    source_filename: str | None = None,
    kind: str = "uploaded",
    settings: Settings | None = None,
) -> Path:
    settings = settings or get_settings()
    path = book_meta_path(book_slug, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "book_slug": book_slug,
        "title": title,
        "kind": kind,
        "source_filename": source_filename,
        "chapters": [{"number": c.number, "title": c.title} for c in chapters],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_book_meta(
    book_slug: str, settings: Settings | None = None
) -> dict | None:
    path = book_meta_path(book_slug, settings)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def chapter_title_for(
    book_slug: str,
    chapter_num: int,
    settings: Settings | None = None,
) -> str:
    if book_slug == PHYSICS10_SLUG:
        for n, t in PHYSICS10_CHAPTERS:
            if n == chapter_num:
                return t
    meta = read_book_meta(book_slug, settings)
    if meta:
        for c in meta.get("chapters") or []:
            if int(c.get("number", -1)) == chapter_num:
                return str(c.get("title") or f"فصل {chapter_num}")
    return f"فصل {chapter_num}"


def list_book_chapters(
    book_slug: str, settings: Settings | None = None
) -> list[dict]:
    """Return [{number, title}, ...] for a slug."""
    settings = settings or get_settings()
    if book_slug == PHYSICS10_SLUG:
        meta = read_book_meta(book_slug, settings)
        if meta and meta.get("chapters"):
            return [
                {"number": int(c["number"]), "title": c["title"]}
                for c in meta["chapters"]
            ]
        return [{"number": n, "title": t} for n, t in PHYSICS10_CHAPTERS]
    meta = read_book_meta(book_slug, settings)
    if meta and meta.get("chapters"):
        return [
            {"number": int(c["number"]), "title": c["title"]} for c in meta["chapters"]
        ]
    chapters = load_extracted_chapters(book_slug, settings)
    return [{"number": c.number, "title": c.title} for c in chapters]


def list_document_sources(settings: Settings | None = None) -> list[dict]:
    """Built-in Physics 10 plus any uploaded docs with meta.json."""
    settings = settings or get_settings()
    sources: list[dict] = [
        {
            "book_slug": PHYSICS10_SLUG,
            "title": "فیزیک (۱) — پایه دهم",
            "kind": "builtin",
            "chapters": [{"number": n, "title": t} for n, t in PHYSICS10_CHAPTERS],
        }
    ]
    root = Path(settings.extracted_dir)
    if not root.is_dir():
        return sources
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == PHYSICS10_SLUG:
            continue
        meta = read_book_meta(child.name, settings)
        if not meta:
            continue
        sources.append(
            {
                "book_slug": child.name,
                "title": meta.get("title") or child.name,
                "kind": meta.get("kind") or "uploaded",
                "source_filename": meta.get("source_filename"),
                "chapters": [
                    {"number": int(c["number"]), "title": c["title"]}
                    for c in (meta.get("chapters") or [])
                ],
            }
        )
    return sources


def export_prefix_for(book_slug: str, chapter_num: int) -> str:
    if book_slug == PHYSICS10_SLUG:
        return f"phys10-ch{chapter_num}"
    return f"{book_slug}-ch{chapter_num}"


def _write_chapter_artifacts(
    chapters: list[Chapter],
    book_slug: str,
    settings: Settings,
) -> dict[int, int]:
    from darsamooz.sections import split_sections, write_sections

    section_counts: dict[int, int] = {}
    for ch in chapters:
        p = settings.extracted_dir / f"{book_slug}-ch{ch.number}.txt"
        p.write_text(ch.text, encoding="utf-8")
        sections = split_sections(ch)
        write_sections(sections, book_slug, ch.number, settings)
        section_counts[ch.number] = len(sections)
    return section_counts


def ingest_pdf(
    pdf_path: Path,
    book_slug: str = PHYSICS10_SLUG,
    settings: Settings | None = None,
) -> list[Chapter]:
    settings = settings or get_settings()
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    out_txt = settings.extracted_dir / f"{book_slug}.txt"
    text = extract_pdf_text(pdf_path, out_txt)
    chapters = split_chapters(text)
    _write_chapter_artifacts(chapters, book_slug, settings)
    write_book_meta(
        book_slug,
        title="فیزیک (۱) — پایه دهم",
        chapters=chapters,
        source_filename=Path(pdf_path).name,
        kind="builtin",
        settings=settings,
    )
    return chapters


def ingest_document_pdf(
    pdf_path: Path,
    book_slug: str,
    *,
    title: str | None = None,
    settings: Settings | None = None,
) -> tuple[list[Chapter], dict[int, int]]:
    """
    Ingest an arbitrary PDF as a generation source.

    Returns (chapters, section_counts_by_chapter).
    """
    settings = settings or get_settings()
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = Path(pdf_path)
    display_title = title or Path(pdf_path).stem or "سند"
    out_txt = settings.extracted_dir / f"{book_slug}.txt"
    text = extract_pdf_text(pdf_path, out_txt)
    chapters = split_chapters_generic(text, fallback_title=display_title)
    section_counts = _write_chapter_artifacts(chapters, book_slug, settings)
    write_book_meta(
        book_slug,
        title=display_title,
        chapters=chapters,
        source_filename=pdf_path.name,
        kind="uploaded",
        settings=settings,
    )
    return chapters, section_counts


def load_extracted_chapters(
    book_slug: str = PHYSICS10_SLUG,
    settings: Settings | None = None,
) -> list[Chapter]:
    settings = settings or get_settings()
    title_by_num: dict[int, str] = {}
    meta = read_book_meta(book_slug, settings)
    if meta and meta.get("chapters"):
        for c in meta["chapters"]:
            title_by_num[int(c["number"])] = str(c.get("title") or f"فصل {c['number']}")
    elif book_slug == PHYSICS10_SLUG:
        title_by_num = {n: t for n, t in PHYSICS10_CHAPTERS}

    chapters: list[Chapter] = []
    # Prefer numbered chapter files discovered on disk
    extracted = Path(settings.extracted_dir)
    pattern = re.compile(rf"^{re.escape(book_slug)}-ch(\d+)\.txt$")
    found_nums: list[int] = []
    if extracted.is_dir():
        for p in extracted.iterdir():
            m = pattern.match(p.name)
            if m:
                found_nums.append(int(m.group(1)))
    for num in sorted(set(found_nums) | set(title_by_num.keys())):
        p = extracted / f"{book_slug}-ch{num}.txt"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        title = title_by_num.get(num, f"فصل {num}")
        chapters.append(
            Chapter(
                number=num,
                title=title,
                text=text,
                start_char=0,
                end_char=len(text),
            )
        )
    if not chapters:
        full = extracted / f"{book_slug}.txt"
        if full.exists():
            raw = full.read_text(encoding="utf-8")
            if book_slug == PHYSICS10_SLUG:
                return split_chapters(raw)
            return split_chapters_generic(raw, fallback_title=book_slug)
    return chapters
