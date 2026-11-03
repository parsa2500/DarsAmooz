from __future__ import annotations

from collections import Counter

import pytest

from darsamooz.balance import (
    assign_target_difficulties,
    assign_target_patterns,
    balance_answer_positions,
    difficulty_prompt_hint,
    distribution_metrics,
    halli_missing_calc_signal,
    looks_like_soft_hard,
    parse_difficulty_mix,
    parse_pattern_mix,
)
from darsamooz.models import Difficulty, Question, QuestionPattern


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


def test_parse_pattern_mix_and_aliases():
    mix = parse_pattern_mix("tarifi=20,mafhumi=40,halli=30,tarkibi=10")
    assert mix is not None
    assert abs(sum(mix.values()) - 1.0) < 1e-9
    assert abs(mix[QuestionPattern.tarifi] - 0.2) < 1e-9
    assert abs(mix[QuestionPattern.mafhumi] - 0.4) < 1e-9
    # taalifi typo alias → tarifi
    alias = parse_pattern_mix("taalifi=50,halli=50")
    assert alias is not None
    assert QuestionPattern.tarifi in alias
    assert parse_pattern_mix(None) is None
    with pytest.raises(ValueError):
        parse_pattern_mix("tarifi=abc")


def test_assign_target_patterns_counts():
    mix = parse_pattern_mix("tarifi=50,mafhumi=50,halli=0,tarkibi=0")
    targets = assign_target_patterns(10, mix)
    assert len(targets) == 10
    counts = Counter(targets)
    assert counts[QuestionPattern.tarifi] == 5
    assert counts[QuestionPattern.mafhumi] == 5
    assert counts.get(QuestionPattern.halli, 0) == 0


def test_difficulty_prompt_hint_includes_hard_rubric():
    hint = difficulty_prompt_hint(Difficulty.hard)
    assert "hard" in hint
    assert "صرفاً بازنویسی تعریف" in hint or "hard نیست" in hint


def test_soft_hard_and_halli_heuristics():
    soft = looks_like_soft_hard(
        "کدام تعریف درست است؟",
        ["گزینه آ", "گزینه ب", "گزینه ج", "گزینه د"],
        Difficulty.hard,
    )
    assert soft is True
    hardish = looks_like_soft_hard(
        "جسمی به جرم ۲ kg با سرعت ۳ m/s حرکت می‌کند؛ تکانه‌اش چند است؟",
        ["۶", "۵", "۳", "۱"],
        Difficulty.hard,
    )
    assert hardish is False
    assert halli_missing_calc_signal(
        "کدام گزینه دربارهٔ تکانه درست است؟",
        ["گزینه آ", "گزینه ب", "گزینه ج", "گزینه د"],
        QuestionPattern.halli,
    )
    assert not halli_missing_calc_signal(
        "جسم ۲ kg با سرعت ۳ m/s",
        ["۶", "۵", "۳", "۱"],
        QuestionPattern.halli,
    )


def test_distribution_metrics_includes_patterns():
    qs = [
        _q(1, diff=Difficulty.easy),
        _q(2, diff=Difficulty.hard),
    ]
    qs[0] = qs[0].model_copy(update={"pattern": QuestionPattern.tarifi})
    qs[1] = qs[1].model_copy(update={"pattern": QuestionPattern.halli})
    dist = distribution_metrics(qs)
    assert dist["patterns"]["tarifi"] == 1
    assert dist["patterns"]["halli"] == 1
    assert dist["difficulties"]["easy"] == 1
    assert dist["difficulties"]["hard"] == 1
