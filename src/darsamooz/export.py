from __future__ import annotations

import csv
import json
from pathlib import Path

from darsamooz.models import Question


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
