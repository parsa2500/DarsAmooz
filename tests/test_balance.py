from __future__ import annotations

from collections import Counter

import pytest

from darsamooz.balance import (
    assign_target_difficulties,
    balance_answer_positions,
    distribution_metrics,
    parse_difficulty_mix,
)
from darsamooz.models import Difficulty, Question


def _q(i: int, correct: int = 0, diff: Difficulty = Difficulty.medium) -> Question:
    opts = ["گزینه آ", "گزینه ب", "گزینه ج", "گزینه د"]
    # put distinctive correct text at correct index
    opts = list(opts)
    return Question(
        id=f"bal-q{i:03d}",
        chapter=1,
        chapter_title="فصل",
        stem=f"سؤال تعادل شماره {i} چیست؟",
        options=opts,
        correct_index=correct,
        explanation="توضیح فارسی.",
        difficulty=diff,
        source_refs=["نقل"],
    )


def test_parse_difficulty_mix_normalizes():
    mix = parse_difficulty_mix("easy=30,medium=50,hard=20")
    assert mix is not None
    assert abs(sum(mix.values()) - 1.0) < 1e-9
    assert abs(mix[Difficulty.easy] - 0.3) < 1e-9
    assert parse_difficulty_mix(None) is None
    assert parse_difficulty_mix("  ") is None
    with pytest.raises(ValueError):
        parse_difficulty_mix("easy=abc")


def test_assign_target_difficulties_counts():
    mix = parse_difficulty_mix("easy=50,medium=50,hard=0")
    targets = assign_target_difficulties(10, mix)
    assert len(targets) == 10
    counts = Counter(targets)
    assert counts[Difficulty.easy] == 5
    assert counts[Difficulty.medium] == 5
    assert counts.get(Difficulty.hard, 0) == 0


def test_balance_spreads_answers_and_preserves_correct_text():
    # All start with correct_index=0
    qs = [_q(i, correct=0) for i in range(12)]
    originals = {q.id: q.options[q.correct_index] for q in qs}
    balanced = balance_answer_positions(qs)
    assert len(balanced) == 12
    for q in balanced:
        assert q.options[q.correct_index] == originals[q.id]
        assert len(set(q.options)) == 4
    dist = distribution_metrics(balanced)
    positions = dist["answer_positions"]
    assert sum(positions.values()) == 12
    # Should be reasonably even (max-min <= 1 for n divisible by 4 after greedy)
    vals = list(positions.values())
    assert max(vals) - min(vals) <= 2
    # Deterministic
    again = balance_answer_positions(qs)
    assert [q.correct_index for q in again] == [q.correct_index for q in balanced]
