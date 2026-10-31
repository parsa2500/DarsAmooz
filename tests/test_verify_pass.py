from __future__ import annotations

from darsamooz.models import Difficulty, Question, ReviewStatus
from darsamooz.verify import verify_question, verify_questions


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}

    def complete_json(self, system: str, user: str):
        self.calls += 1
        if callable(self.payload):
            return self.payload(system, user)
        return dict(self.payload)


def _q(correct: int = 0) -> Question:
    return Question(
        id="v-q001",
        chapter=1,
        chapter_title="فصل",
        stem="کمیت اصلی کدام است؟",
        options=["طول", "سرعت", "نیرو", "فشار"],
        correct_index=correct,
        explanation="طبق متن کتاب طول کمیت اصلی است.",
        difficulty=Difficulty.easy,
        source_refs=["کمیت اصلی"],
        tags=["section:ch1-u001"],
    )


def test_verify_agrees_keeps_pending():
    q = _q(0)
    provider = FakeProvider({"answer_index": 0, "ambiguous": False, "reason": "طبق متن"})
    out = verify_question(q, "طول یک کمیت اصلی است.", provider=provider)
    assert out.verifier_agrees is True
    assert out.review_status == ReviewStatus.pending
    assert out.verifier_answer_index == 0
    assert provider.calls == 1


def test_verify_mismatch_marks_needs_review_not_dropped():
    q = _q(0)
    provider = FakeProvider({"answer_index": 2, "ambiguous": False, "reason": "اشتباه مدل"})
    out = verify_question(q, "طول یک کمیت اصلی است.", provider=provider)
    assert out.verifier_agrees is False
    assert out.review_status == ReviewStatus.needs_review
    assert out.id == q.id
    assert out.stem == q.stem


def test_verify_ambiguous_marks_needs_review():
    q = _q(1)
    provider = FakeProvider({"answer_index": 1, "ambiguous": True, "reason": "مبهم"})
    out = verify_question(q, "متن ناکافی", provider=provider)
    assert out.review_status == ReviewStatus.needs_review
    assert out.verifier_ambiguous is True


def test_verify_batch_metrics_and_missing_unit():
    qs = [_q(0), _q(0)]
    qs[1] = qs[1].model_copy(update={"id": "v-q002", "tags": []})
    provider = FakeProvider({"answer_index": 0, "ambiguous": False, "reason": "ok"})
    out, metrics = verify_questions(
        qs,
        section_text_by_id={"ch1-u001": "طول کمیت اصلی است."},
        provider=provider,
    )
    assert len(out) == 2
    assert metrics["verify_enabled"] is True
    assert metrics["verify_count"] == 2
    assert metrics["verify_errors"] >= 1
    assert out[1].review_status == ReviewStatus.needs_review
