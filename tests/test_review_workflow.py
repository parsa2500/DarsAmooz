from __future__ import annotations

import json
from pathlib import Path

from darsamooz.models import Difficulty, Question, ReviewStatus
from darsamooz.review import (
    apply_review_edit,
    filter_by_review_status,
    load_questions_payload,
    reexport_questions,
    review_summary,
    save_questions_payload,
)


def _q(i: int) -> Question:
    return Question(
        id=f"rev-q{i:03d}",
        chapter=1,
        chapter_title="فصل",
        stem=f"متن سؤال فارسی {i} برای بازبینی؟",
        options=["الف‌گزینه", "ب‌گزینه", "ج‌گزینه", "د‌گزینه"],
        correct_index=1,
        explanation="توضیح فارسی.",
        difficulty=Difficulty.easy,
        source_refs=["نقل"],
    )


def test_approve_reject_edit_and_approved_export(tmp_path: Path):
    qs = [_q(1), _q(2), _q(3)]
    path = tmp_path / "bank.json"
    save_questions_payload(path, qs, meta={"book": "test"})
    meta, loaded = load_questions_payload(path)
    assert meta["count"] == 3
    assert all(q.review_status == ReviewStatus.pending for q in loaded)

    loaded[0] = apply_review_edit(loaded[0], {"review_status": "approved"})
    loaded[1] = apply_review_edit(
        loaded[1],
        {
            "review_status": "rejected",
            "stem": "صورت ویرایش‌شده سؤال دو چیست؟",
            "correct_index": 2,
            "options": ["یک", "دو", "سه", "چهار"],
            "explanation": "توضیح جدید فارسی.",
            "difficulty": "hard",
        },
    )
    save_questions_payload(path, loaded, meta=meta)

    _, again = load_questions_payload(path)
    assert again[0].review_status == ReviewStatus.approved
    assert again[1].review_status == ReviewStatus.rejected
    assert again[1].correct_index == 2
    assert again[1].difficulty == Difficulty.hard

    approved = filter_by_review_status(again, approved_only=True)
    assert len(approved) == 1
    summary = review_summary(again)
    assert summary["approved"] == 1
    assert summary["rejected"] == 1
    assert summary["pending"] == 1

    out = tmp_path / "approved.json"
    paths = reexport_questions(
        again,
        json_path=out,
        csv_path=tmp_path / "approved.csv",
        md_path=tmp_path / "approved.md",
        approved_only=True,
    )
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert data["questions"][0]["id"] == "rev-q001"
