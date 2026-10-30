from __future__ import annotations

from darsamooz.models import Difficulty, Question


def validate_question_dict(raw: dict, *, chapter: int, chapter_title: str, qid: str) -> Question:
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
        source_refs=list(raw.get("source_refs") or []),
        tags=list(raw.get("tags") or []),
        is_sample=bool(raw.get("is_sample", False)),
        subject=str(raw.get("subject") or "فیزیک ۱ دهم ریاضی"),
    )


def validate_batch(items: list[dict], *, chapter: int, chapter_title: str, id_prefix: str) -> list[Question]:
    out: list[Question] = []
    for i, raw in enumerate(items, start=1):
        qid = raw.get("id") or f"{id_prefix}-q{i:03d}"
        out.append(validate_question_dict(raw, chapter=chapter, chapter_title=chapter_title, qid=qid))
    return out
