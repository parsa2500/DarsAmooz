from __future__ import annotations

import os
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.llm import LLMProvider, OpenAICompatibleProvider, get_provider
from darsamooz.models import Question, ReviewStatus, Section

VERIFY_SYSTEM = """You are a careful Iranian grade-10 physics tutor.
Solve the MCQ using ONLY the provided unit text from the textbook.
Do NOT invent facts. Return ONLY JSON:
{"answer_index":0,"ambiguous":false,"reason":"short Persian justification"}
answer_index is 0-based (0=الف/A … 3=د/D).
Set ambiguous=true if the unit text is insufficient or more than one option could be right.
"""


class VerifyModelSettings:
    """Thin adapter so OpenAICompatibleProvider can use VERIFY_MODEL when set."""

    def __init__(self, base: Settings, model_override: str | None) -> None:
        self._base = base
        self._model_override = model_override

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    @property
    def model(self) -> str:
        if self._model_override:
            return self._model_override
        return self._base.model


def resolve_verify_provider(
    settings: Settings | None = None,
    provider: LLMProvider | None = None,
) -> LLMProvider:
    """Same configured provider/model unless VERIFY_MODEL is set."""
    settings = settings or get_settings()
    override = (os.environ.get("VERIFY_MODEL") or "").strip() or None
    if provider is not None and not override:
        return provider
    wrapped = VerifyModelSettings(settings, override)
    return OpenAICompatibleProvider(wrapped)  # type: ignore[arg-type]


def _build_verify_prompt(question: Question, unit_text: str) -> str:
    letters = ("الف", "ب", "ج", "د")
    opts = "\n".join(f"{letters[i]}) {opt}" for i, opt in enumerate(question.options))
    return (
        f"سؤال:\n{question.stem}\n\nگزینه‌ها:\n{opts}\n\n"
        f"--- متن یونیت کتاب ---\n{unit_text}\n--- پایان ---\n"
        "فقط از روی متن یونیت جواب بده. پاسخ را JSON برگردان."
    )


def verify_question(
    question: Question,
    unit_text: str,
    *,
    provider: LLMProvider,
) -> Question:
    """Blind-solve one question; mark needs_review on mismatch/ambiguity.

    Never drops the question — always returns an updated Question.
    """
    data = provider.complete_json(VERIFY_SYSTEM, _build_verify_prompt(question, unit_text))
    if not isinstance(data, dict):
        return question.model_copy(
            update={
                "review_status": ReviewStatus.needs_review,
                "verifier_reason": "verifier returned non-object JSON",
                "verifier_agrees": False,
                "verifier_ambiguous": True,
            }
        )

    raw_idx = data.get("answer_index", data.get("correct_index"))
    try:
        idx = int(raw_idx) if raw_idx is not None else None
    except (TypeError, ValueError):
        idx = None
    ambiguous = bool(data.get("ambiguous", False))
    reason = str(data.get("reason") or data.get("explanation") or "").strip() or None

    if idx is None or not (0 <= idx <= 3):
        return question.model_copy(
            update={
                "review_status": ReviewStatus.needs_review,
                "verifier_answer_index": None,
                "verifier_reason": reason or "verifier missing/invalid answer_index",
                "verifier_agrees": False,
                "verifier_ambiguous": True,
            }
        )

    agrees = (idx == question.correct_index) and not ambiguous
    updates: dict[str, Any] = {
        "verifier_answer_index": idx,
        "verifier_reason": reason,
        "verifier_agrees": agrees,
        "verifier_ambiguous": ambiguous,
    }
    if not agrees:
        updates["review_status"] = ReviewStatus.needs_review
        if not reason:
            updates["verifier_reason"] = (
                f"verifier chose {idx}, expected {question.correct_index}"
                + (" (ambiguous)" if ambiguous else "")
            )
    return question.model_copy(update=updates)


def verify_questions(
    questions: list[Question],
    *,
    section_text_by_id: dict[str, str],
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> tuple[list[Question], dict[str, Any]]:
    """Verify a batch. section_text_by_id maps section id → unit text."""
    settings = settings or get_settings()
    provider = resolve_verify_provider(settings, provider)
    out: list[Question] = []
    mismatches = 0
    ambiguous = 0
    errors = 0
    for q in questions:
        section_id = None
        for tag in q.tags:
            if tag.startswith("section:"):
                section_id = tag.split(":", 1)[1]
                break
        unit = ""
        if section_id and section_id in section_text_by_id:
            unit = section_text_by_id[section_id]
        if not unit:
            # Fall back: empty unit → mark needs_review, do not call LLM blindly
            out.append(
                q.model_copy(
                    update={
                        "review_status": ReviewStatus.needs_review,
                        "verifier_reason": "no unit text available for verification",
                        "verifier_agrees": False,
                        "verifier_ambiguous": True,
                    }
                )
            )
            errors += 1
            continue
        try:
            verified = verify_question(q, unit, provider=provider)
        except Exception as exc:
            errors += 1
            verified = q.model_copy(
                update={
                    "review_status": ReviewStatus.needs_review,
                    "verifier_reason": f"verifier_error: {exc}",
                    "verifier_agrees": False,
                    "verifier_ambiguous": True,
                }
            )
        if verified.verifier_ambiguous:
            ambiguous += 1
        if verified.verifier_agrees is False:
            mismatches += 1
        out.append(verified)

    metrics = {
        "verify_enabled": True,
        "verify_count": len(questions),
        "verify_mismatches": mismatches,
        "verify_ambiguous": ambiguous,
        "verify_errors": errors,
        "needs_review": sum(
            1 for q in out if q.review_status == ReviewStatus.needs_review
        ),
    }
    return out, metrics


def sections_to_text_map(sections: list[Section]) -> dict[str, str]:
    return {s.id: s.text for s in sections}
