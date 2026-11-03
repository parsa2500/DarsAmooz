from __future__ import annotations

import pytest

from darsamooz.models import Difficulty, QuestionPattern
from darsamooz.validate import validate_question_dict


def _base(**overrides):
    raw = {
        "stem": "کدام گزینه دربارهٔ جرم درست است؟",
        "options": ["گزینهٔ الف", "گزینهٔ ب", "گزینهٔ ج", "گزینهٔ د"],
        "correct_index": 0,
        "explanation": "چون طبق متن کتاب این درست است.",
        "difficulty": "medium",
        "pattern": "mafhumi",
        "source_refs": ["جرم کمیتی نرده‌ای است"],
    }
    raw.update(overrides)
    return raw


UNIT = "جرم کمیتی نرده‌ای است و یکای آن کیلوگرم می‌باشد."


def test_validate_accepts_pattern_and_tag():
    q = validate_question_dict(
        _base(pattern="tarifi"),
        chapter=1,
        chapter_title="فصل ۱",
        qid="t-q1",
        unit_text=UNIT,
        require_grounding=True,
    )
    assert q.pattern == QuestionPattern.tarifi
    assert "pattern:tarifi" in q.tags


def test_validate_persian_pattern_alias():
    q = validate_question_dict(
        _base(pattern="تعریفی"),
        chapter=1,
        chapter_title="فصل ۱",
        qid="t-q2",
        unit_text=UNIT,
        require_grounding=False,
    )
    assert q.pattern == QuestionPattern.tarifi


def test_validate_rejects_halli_without_numbers():
    with pytest.raises(ValueError, match="halli"):
        validate_question_dict(
            _base(pattern="halli"),
            chapter=1,
            chapter_title="فصل ۱",
            qid="t-q3",
            require_grounding=False,
        )


def test_validate_accepts_halli_with_numbers():
    q = validate_question_dict(
        _base(
            stem="جسمی به جرم ۲ kg با سرعت ۳ m/s حرکت می‌کند؛ تکانه‌اش چند است؟",
            options=["۶", "۵", "۳", "۱"],
            pattern="halli",
            difficulty="medium",
        ),
        chapter=1,
        chapter_title="فصل ۱",
        qid="t-q4",
        require_grounding=False,
    )
    assert q.pattern == QuestionPattern.halli


def test_soft_hard_downgrades_with_tag():
    q = validate_question_dict(
        _base(
            stem="کدام تعریف درست است؟",
            difficulty="hard",
            pattern="tarifi",
        ),
        chapter=1,
        chapter_title="فصل ۱",
        qid="t-q5",
        require_grounding=False,
    )
    assert q.difficulty == Difficulty.medium
    assert "hard_downgraded" in q.tags


def test_invalid_pattern_rejected():
    with pytest.raises(ValueError, match="pattern"):
        validate_question_dict(
            _base(pattern="nonsense"),
            chapter=1,
            chapter_title="فصل ۱",
            qid="t-q6",
            require_grounding=False,
        )
