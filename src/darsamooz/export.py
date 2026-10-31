from __future__ import annotations

import csv
import json
from pathlib import Path

from darsamooz.models import Question
from darsamooz.validate import validate_batch

_OPTION_LETTERS_FA = ("الف", "ب", "ج", "د")
_DIFFICULTY_FA = {
    "easy": "آسان",
    "medium": "متوسط",
    "hard": "سخت",
}


def export_json(questions: list[Question], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(questions),
        "questions": [q.model_dump(mode="json") for q in questions],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_csv(questions: list[Question], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "id",
        "subject",
        "chapter",
        "chapter_title",
        "section",
        "stem",
        "option_a",
        "option_b",
        "option_c",
        "option_d",
        "correct_index",
        "correct_letter",
        "explanation",
        "difficulty",
        "tags",
        "is_sample",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for q in questions:
            letters = "ABCD"
            w.writerow(
                {
                    "id": q.id,
                    "subject": q.subject,
                    "chapter": q.chapter,
                    "chapter_title": q.chapter_title,
                    "section": q.section or "",
                    "stem": q.stem,
                    "option_a": q.options[0],
                    "option_b": q.options[1],
                    "option_c": q.options[2],
                    "option_d": q.options[3],
                    "correct_index": q.correct_index,
                    "correct_letter": letters[q.correct_index],
                    "explanation": q.explanation,
                    "difficulty": q.difficulty.value,
                    "tags": "|".join(q.tags),
                    "is_sample": q.is_sample,
                }
            )
    return path


def _difficulty_fa(q: Question) -> str:
    key = q.difficulty.value if hasattr(q.difficulty, "value") else str(q.difficulty)
    return _DIFFICULTY_FA.get(key, key)


def render_markdown(
    questions: list[Question],
    *,
    title: str | None = None,
    book_title: str | None = None,
) -> str:
    """Render RTL-friendly Persian Markdown for a question set."""
    if not questions:
        raise ValueError("no questions to export as Markdown")

    first = questions[0]
    book = book_title or first.subject or "فیزیک ۱ دهم ریاضی"
    chapter_title = first.chapter_title or f"فصل {first.chapter}"
    heading = title or f"تست‌های {book} — فصل {first.chapter}: {chapter_title}"

    lines: list[str] = [
        '<div dir="rtl" lang="fa">',
        "",
        f"# {heading}",
        "",
        f"**تعداد سؤال:** {len(questions)}",
        "",
        "---",
        "",
    ]

    for i, q in enumerate(questions, start=1):
        section = f" — {q.section}" if q.section else ""
        lines.append(f"## سؤال {i}{section}")
        lines.append("")
        lines.append(q.stem.strip())
        lines.append("")
        for letter, opt in zip(_OPTION_LETTERS_FA, q.options, strict=True):
            lines.append(f"- **{letter})** {opt}")
        lines.append("")

        correct_letter = _OPTION_LETTERS_FA[q.correct_index]
        correct_text = q.options[q.correct_index]
        refs = q.source_refs or []
        quote = " / ".join(r.strip() for r in refs if str(r).strip()) or "—"

        lines.append("<details>")
        lines.append("<summary>پاسخ و توضیح</summary>")
        lines.append("")
        lines.append(f"**پاسخ صحیح:** {correct_letter}) {correct_text}")
        lines.append("")
        lines.append(f"**توضیح:** {q.explanation.strip()}")
        lines.append("")
        lines.append(f"**سطح دشواری:** {_difficulty_fa(q)}")
        lines.append("")
        lines.append(f"**نقل از متن کتاب:** «{quote}»")
        lines.append("")
        lines.append("</details>")
        lines.append("")
        lines.append("---")
        lines.append("")

    lines.append("</div>")
    lines.append("")
    return "\n".join(lines)


def export_markdown(
    questions: list[Question],
    path: Path,
    *,
    title: str | None = None,
    book_title: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_markdown(questions, title=title, book_title=book_title),
        encoding="utf-8",
    )
    return path


def load_questions_from_json(path: Path) -> list[Question]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["questions"] if isinstance(data, dict) else data
    if not items:
        raise ValueError(f"no questions in {path}")
    first = items[0]
    chapter = int(first.get("chapter") or 1)
    chapter_title = str(first.get("chapter_title") or "")
    prefix = str(first.get("id") or "q").rsplit("-q", 1)[0]
    return validate_batch(
        items, chapter=chapter, chapter_title=chapter_title, id_prefix=prefix
    )
