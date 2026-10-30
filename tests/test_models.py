import pytest
from darsamooz.models import Question


def test_question_requires_four_unique_options():
    with pytest.raises(Exception):
        Question(
            id="t1",
            chapter=1,
            stem="سوال؟",
            options=["الف", "ب", "ج"],
            correct_index=0,
            explanation="چون",
        )


def test_question_ok():
    q = Question(
        id="t1",
        chapter=1,
        stem="سوال نمونه؟",
        options=["الف", "ب", "ج", "د"],
        correct_index=2,
        explanation="چون ج درست است",
    )
    assert q.correct_index == 2
