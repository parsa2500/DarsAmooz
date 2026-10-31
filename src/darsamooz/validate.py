from __future__ import annotations

import re
import unicodedata

from darsamooz.models import Difficulty, Question

_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_WHITESPACE = re.compile(r"\s+")


def normalize_fa(text: str) -> str:
    """Light Persian normalization for quote matching."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    s = (
        s.replace("ي", "ی")
        .replace("ك", "ک")
        .replace("ة", "ه")
        .replace("‌", " ")  # ZWNJ → space
        .replace("ـ", "")  # tatweel / section dash noise
        .replace("–", "")
        .replace("—", "")
    )
    s = _ARABIC_DIACRITICS.sub("", s)
    s = _WHITESPACE.sub(" ", s).strip().lower()
    return s


def quote_in_source(quote: str, unit_text: str, *, min_len: int = 12) -> bool:
    """True if normalized quote is a substring of normalized unit text."""
    q = normalize_fa(quote)
    if len(q) < min_len:
        return False
    src = normalize_fa(unit_text)
    if not src:
        return False
    if q in src:
        return True
    # Allow minor truncation: require a long contiguous window
    if len(q) >= 24:
        window = q[: max(min_len, len(q) // 2)]
        if window in src:
            return True
    return False


def any_quote_grounded(refs: list[str], unit_text: str) -> bool:
    return any(quote_in_source(str(r), unit_text) for r in refs if str(r).strip())


def validate_question_dict(
    raw: dict,
    *,
    chapter: int,
    chapter_title: str,
    qid: str,
    unit_text: str | None = None,
    require_grounding: bool = False,
) -> Question:
    options = raw.get("options") or raw.get("choices")
    if isinstance(options, dict):
        # {"A": "...", "B": "...", ...}
        keys = sorted(options.keys())
        options = [options[k] for k in keys[:4]]

    correct = raw.get("correct_index", raw.get("answer_index"))
    if correct is None and "correct" in raw:
        c = raw["correct"]
        if isinstance(c, str) and c.upper() in "ABCD":
            correct = ord(c.upper()) - ord("A")
        elif isinstance(c, int):
            correct = c if c < 4 else c - 1
    if correct is None and "correct_option" in raw:
        c = str(raw["correct_option"]).upper()
        if c in "ABCD":
            correct = ord(c) - ord("A")

    diff = raw.get("difficulty", "medium")
    if isinstance(diff, str):
        diff = diff.lower()
    try:
        difficulty = Difficulty(diff)
    except ValueError:
        difficulty = Difficulty.medium

    stem = str(raw.get("stem") or raw.get("question") or "").strip()
    explanation = str(raw.get("explanation") or raw.get("reason") or "").strip()
    opts = [str(o).strip() for o in (options or [])]
    if options is None or len(opts) != 4:
        raise ValueError("exactly 4 options required")
    if correct is None:
        raise ValueError("correct_index missing")
    # Require Persian in stem/explanation for Iranian تست items
    if not any("\u0600" <= c <= "\u06FF" for c in stem):
        raise ValueError("stem must contain Persian text")
    if not any("\u0600" <= c <= "\u06FF" for c in explanation):
        raise ValueError("explanation must contain Persian text")

    refs = [str(r).strip() for r in (raw.get("source_refs") or []) if str(r).strip()]
    if require_grounding:
        if not refs:
            raise ValueError("source_refs required for grounded generation")
        if unit_text is None or not any_quote_grounded(refs, unit_text):
            raise ValueError("source_refs quote not found in unit text")

    tags = list(raw.get("tags") or [])
    section_id = raw.get("section_id")
    if section_id and f"section:{section_id}" not in tags:
        tags = [*tags, f"section:{section_id}"]

    return Question(
        id=qid,
        chapter=chapter,
        chapter_title=chapter_title,
        section=raw.get("section"),
        stem=stem,
        options=opts,
        correct_index=int(correct),
        explanation=explanation,
        difficulty=difficulty,
        source_refs=refs,
        tags=tags,
        is_sample=bool(raw.get("is_sample", False)),
        subject=str(raw.get("subject") or "فیزیک ۱ دهم ریاضی"),
    )


def validate_batch(
    items: list[dict],
    *,
    chapter: int,
    chapter_title: str,
    id_prefix: str,
    unit_text: str | None = None,
    require_grounding: bool = False,
) -> list[Question]:
    out: list[Question] = []
    for i, raw in enumerate(items, start=1):
        qid = raw.get("id") or f"{id_prefix}-q{i:03d}"
        out.append(
            validate_question_dict(
                raw,
                chapter=chapter,
                chapter_title=chapter_title,
                qid=qid,
                unit_text=unit_text,
                require_grounding=require_grounding,
            )
        )
    return out
