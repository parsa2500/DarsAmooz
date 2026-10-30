from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from darsamooz.config import Settings, get_settings
from darsamooz.models import Chapter

# Canonical chapter titles for Physics 1 (math track), book code 110209
PHYSICS10_CHAPTERS: list[tuple[int, str]] = [
    (1, "فیزیک و اندازه‌گیری"),
    (2, "ویژگی‌های فیزیکی مواد"),
    (3, "کار، انرژی و توان"),
    (4, "دما و گرما"),
    (5, "ترمودینامیک"),
]


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


def _find_chapter_body_starts(text: str) -> dict[int, int]:
    """
    Locate approximate start offsets for each chapter body.

    Prefer form-feed + 'فصل N' markers that appear as running headers
    after the table of contents.
    """
    starts: dict[int, int] = {}
    # After TOC, chapters appear as '\fفصل N' or 'فصل N' near page starts
    for m in re.finditer(r"(?:^|\f)\s*فصل\s*([1-5۱-۵])\b", text, flags=re.MULTILINE):
        num_s = m.group(1)
        # normalize Persian digits
        trans = str.maketrans("۰۱۲۳۴۵۶۷۸۹۱۲۳۴۵", "012345678912345")
        # Only map ۰-۹ and ۱-۵ variants; keep simple:
        digit_map = {"۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5", "1": "1", "2": "2", "3": "3", "4": "4", "5": "5"}
        n = int(digit_map.get(num_s, num_s.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))))
        if n not in starts:
            # Skip early TOC hits: require some content length already
            if m.start() < 8000:
                continue
            starts[n] = m.start()
    return starts


def split_chapters(text: str, titles: list[tuple[int, str]] | None = None) -> list[Chapter]:
    titles = titles or PHYSICS10_CHAPTERS
    starts = _find_chapter_body_starts(text)

    # Fallback: TOC lines like 'فصل  : 1فیزیک و اندازه گیری'
    if len(starts) < 2:
        starts = {}
        for m in re.finditer(r"فصل\s*:\s*([1-5۱-۵])", text):
            digit_map = {"۱": "1", "۲": "2", "۳": "3", "۴": "4", "۵": "5"}
            n = int(digit_map.get(m.group(1), m.group(1)))
            if n not in starts:
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


def ingest_pdf(
    pdf_path: Path,
    book_slug: str = "physics10-riazi-110209",
    settings: Settings | None = None,
) -> list[Chapter]:
    settings = settings or get_settings()
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    out_txt = settings.extracted_dir / f"{book_slug}.txt"
    text = extract_pdf_text(pdf_path, out_txt)
    chapters = split_chapters(text)
    # Write per-chapter files for inspection
    for ch in chapters:
        p = settings.extracted_dir / f"{book_slug}-ch{ch.number}.txt"
        p.write_text(ch.text, encoding="utf-8")
    return chapters


def load_extracted_chapters(
    book_slug: str = "physics10-riazi-110209",
    settings: Settings | None = None,
) -> list[Chapter]:
    settings = settings or get_settings()
    chapters: list[Chapter] = []
    for num, title in PHYSICS10_CHAPTERS:
        p = settings.extracted_dir / f"{book_slug}-ch{num}.txt"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        chapters.append(Chapter(number=num, title=title, text=text, start_char=0, end_char=len(text)))
    if not chapters:
        full = settings.extracted_dir / f"{book_slug}.txt"
        if full.exists():
            return split_chapters(full.read_text(encoding="utf-8"))
    return chapters
