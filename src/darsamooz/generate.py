from __future__ import annotations

import json
import re
from pathlib import Path

from darsamooz.chunking import chunk_chapter
from darsamooz.config import Settings, get_settings
from darsamooz.ingest import PHYSICS10_CHAPTERS, load_extracted_chapters
from darsamooz.llm import LLMProvider, get_provider
from darsamooz.models import Question
from darsamooz.validate import validate_batch, validate_question_dict

SYSTEM_PROMPT = """You are an exam-item writer for Iranian grade-10 physics (math track).
Create multiple-choice questions (تست چهارگزینه‌ای) ONLY from the provided textbook source text.
Return ONLY valid JSON:
{
  "questions": [
    {
      "stem": "Persian question text",
      "options": ["opt1", "opt2", "opt3", "opt4"],
      "correct_index": 0,
      "explanation": "short correct Persian explanation grounded in the source",
      "difficulty": "easy|medium|hard",
      "section": "subsection name if known",
      "tags": ["tag"],
      "source_refs": ["verbatim short quote from the source text that supports the answer"]
    }
  ]
}
Hard rules:
- Use ONLY facts, definitions, formulas, and examples present in the source text
- Exactly 4 unique options; exactly one fully correct answer
- correct_index must be 0..3 and match the correct option
- Distractors must be plausible but wrong
- Avoid "all of the above" / "none of the above"
- Keep formulas, units, and numbers faithful to the source; do not invent outside facts
- Write stem and explanation in clear Persian (SI unit symbols like m, kg, s are OK)
- Each item must be understandable without seeing the textbook
- Do NOT repeat the same scenario/numbers across items
- Aim for a mix of difficulties when asked for several items
- source_refs MUST quote a short continuous phrase that actually appears in the source text
"""


_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_NON_WORD = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)


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


def _build_chunk_prompt(chapter_num: int, title: str, chunk: str, count: int) -> str:
    return (
        f"کتاب: فیزیک (۱) پایه دهم رشته ریاضی و فیزیک (کد ۱۱۰۲۰۹)\n"
        f"فصل {chapter_num}: {title}\n"
        f"از متن منبع زیر دقیقاً {count} تست چهارگزینه‌ای بساز. "
        f"فقط از همین متن استفاده کن؛ چیزی از بیرون اضافه نکن.\n\n"
        f"--- متن منبع ---\n{chunk}\n--- پایان متن منبع ---\n"
    )


def generate_from_chapter(
    chapter_num: int,
    count: int = 10,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    book_slug: str = "physics10-riazi-110209",
) -> list[Question]:
    settings = settings or get_settings()
    chapters = {c.number: c for c in load_extracted_chapters(book_slug, settings)}
    if chapter_num not in chapters:
        raise FileNotFoundError(
            f"Chapter {chapter_num} not found. Run `darsamooz ingest` first."
        )
    chapter = chapters[chapter_num]
    chunks = chunk_chapter(chapter)
    if not chunks:
        raise RuntimeError(
            f"Chapter {chapter_num} has no extracted text. Re-run ingest on the PDF."
        )

    provider = provider or get_provider(settings)
    title = chapter.title or _chapter_title(chapter_num)
    prefix = f"phys10-ch{chapter_num}"
    collected: list[dict] = []
    accepted_stems: list[str] = []
    max_rounds = max(len(chunks) * 2, (count + 3) // 4 * 3)
    last_error: Exception | None = None

    for i in range(max_rounds):
        if len(collected) >= count:
            break
        chunk = chunks[i % len(chunks)]
        batch = min(4, count - len(collected))
        user = _build_chunk_prompt(chapter_num, title, chunk, batch)
        try:
            data = provider.complete_json(SYSTEM_PROMPT, user)
        except Exception as exc:
            last_error = exc
            # Do not silently switch models/providers; surface after retries exhausted.
            continue
        items = data.get("questions", data if isinstance(data, list) else [])
        if not isinstance(items, list):
            items = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            stem = str(raw.get("stem") or raw.get("question") or "")
            if any(_stems_too_similar(stem, prev) for prev in accepted_stems):
                continue
            collected.append(raw)
            accepted_stems.append(stem)
            if len(collected) >= count:
                break

    questions: list[Question] = []
    for raw in collected:
        if len(questions) >= count:
            break
        try:
            qid = raw.get("id") or f"{prefix}-q{len(questions)+1:03d}"
            q = validate_question_dict(
                raw, chapter=chapter_num, chapter_title=title, qid=qid
            )
            if any(_stems_too_similar(q.stem, prev.stem) for prev in questions):
                continue
            questions.append(q)
        except Exception:
            continue

    if len(questions) < count:
        detail = f" last_error={last_error}" if last_error else ""
        raise RuntimeError(
            f"Only generated {len(questions)}/{count} valid book-grounded questions "
            f"for chapter {chapter_num} after {max_rounds} rounds "
            f"(provider={settings.provider_name}, model={settings.model})."
            f"{detail}"
        )
    return questions[:count]


def load_sample_questions(path: Path | None = None) -> list[Question]:
    path = path or Path("samples/physics10_ch1_sample.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["questions"] if isinstance(data, dict) else data
    return validate_batch(
        items,
        chapter=1,
        chapter_title=_chapter_title(1),
        id_prefix="phys10-ch1-sample",
    )
