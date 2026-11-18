"""Question-quality gate used during generation (before a question is accepted).

Reject reasons (stable codes, written to items.jsonl and metrics):

* ``trivial_recall``   — one-line definition / heading recall («فیزیک به معنای چه است؟»)
* ``garbled_text``     — broken / transliterated Persian not found in the book («اسکاپی‌التور»)
* ``pattern_mismatch`` — the question does not follow its web pattern and fits no other one

Deterministic checks run first; with a web pattern pack an LLM reviewer also checks
fluency, triviality and pattern fit. A reviewer failure is never treated as a pass:
the question is kept but tagged ``quality_unchecked`` and marked needs_review.
"""

from __future__ import annotations

import re
from typing import Any

from darsamooz.chapter_scope import ChapterScope, garbled_tokens, trivial_recall_reason, unknown_terms
from darsamooz.llm import LLMProvider
from darsamooz.models import Question

REVIEW_SYSTEM = """You review ONE generated Iranian MCQ (تست چهارگزینه‌ای, Persian) before it is accepted.
Return ONLY JSON:
{"fluent_persian": true, "garbled_terms": [], "trivial_recall": false, "pattern_fits": true, "best_pattern_id": null, "reason": "short Persian"}
Rules:
- fluent_persian=false when the stem or options contain broken Persian, misused verbs, nonsense or
  transliterated words (e.g. «اسکاپی‌التور», «هر دانشمند چقدر از طول ... می‌خورد»). Put those exact words in garbled_terms.
- trivial_recall=true when the question only asks to repeat a heading or a one-line definition
  (e.g. «فیزیک به معنای چه است؟») and needs no understanding.
- pattern_fits: does the question really follow the TARGET pattern (its skeleton and numeric/conceptual type)?
  If not, set best_pattern_id to the id of another listed pattern that it does fit, else null.
"""

_DIGIT = re.compile(r"[0-9۰-۹]")


def deterministic_reject(
    q: Question, scope: ChapterScope, web_pattern: dict[str, Any] | None = None
) -> tuple[str, str] | None:
    """(code, detail) for an obvious reject, else None. No LLM call."""
    tr = trivial_recall_reason(q.stem, q.options, q.correct_index, scope.section_titles)
    if tr:
        return "trivial_recall", tr
    bad = garbled_tokens([q.stem, *q.options], scope)
    if bad:
        return "garbled_text", "not in book: " + "، ".join(bad[:5])
    if web_pattern is not None and web_pattern.get("numeric"):
        if not _DIGIT.search(" ".join([q.stem, *q.options])):
            return "pattern_mismatch", f"pattern {web_pattern.get('id')} is numeric but the question has no numbers"
    return None


def _review_prompt(q: Question, unit_text: str, target: dict[str, Any], patterns: list[dict[str, Any]]) -> str:
    letters = ("الف", "ب", "ج", "د")
    opts = "\n".join(f"{letters[i]}) {o}" for i, o in enumerate(q.options))
    plist = "\n".join(
        f"{p.get('id')}: {p.get('name_fa')} | اسکلت: {p.get('stem_template')} | "
        f"عددی: {'بله' if p.get('numeric') else 'خیر'}"
        for p in patterns
    )
    return (
        f"سؤال:\n{q.stem}\n\nگزینه‌ها:\n{opts}\nپاسخ درست: {letters[q.correct_index]}\n\n"
        f"الگوی هدف: {target.get('id')}\nالگوهای موجود:\n{plist}\n\n"
        f"--- متن یونیت کتاب (برای تشخیص واژه‌های کتاب) ---\n{unit_text[:3500]}\n--- پایان ---\n"
        "فقط JSON برگردان."
    )


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "1", "yes", "بله"}:
            return True
        if s in {"false", "0", "no", "خیر"}:
            return False
    return None


def llm_review(
    q: Question,
    unit_text: str,
    *,
    target: dict[str, Any],
    patterns: list[dict[str, Any]],
    scope: ChapterScope,
    provider: LLMProvider,
) -> dict[str, Any]:
    """Return a decision dict:

    ``{"action": "accept"|"reject"|"retag"|"unchecked", "code", "detail", "pattern_id"}``
    """
    try:
        raw = provider.complete_json(REVIEW_SYSTEM, _review_prompt(q, unit_text, target, patterns))
    except Exception as exc:  # noqa: BLE001 — surfaced as quality_unchecked
        return {"action": "unchecked", "code": "quality_review_error", "detail": str(exc)[:200]}
    if not isinstance(raw, dict) or not {"fluent_persian", "trivial_recall", "pattern_fits"} <= set(raw):
        return {"action": "unchecked", "code": "quality_review_malformed", "detail": "reviewer JSON missing fields"}
    reason = str(raw.get("reason") or "").strip()[:200]
    fluent = _as_bool(raw.get("fluent_persian"))
    terms = [str(t) for t in (raw.get("garbled_terms") or []) if str(t).strip()]
    novel = unknown_terms(terms, scope)
    if novel:
        return {"action": "reject", "code": "garbled_text", "detail": "reviewer: " + "، ".join(novel[:5])}
    if fluent is False and not terms:
        return {"action": "reject", "code": "garbled_text", "detail": f"reviewer: not fluent Persian — {reason}"}
    if _as_bool(raw.get("trivial_recall")) is True:
        return {"action": "reject", "code": "trivial_recall", "detail": f"reviewer: {reason}"}
    if _as_bool(raw.get("pattern_fits")) is False:
        best = str(raw.get("best_pattern_id") or "").strip()
        ids = {str(p.get("id")) for p in patterns}
        if best and best in ids and best != str(target.get("id")):
            return {"action": "retag", "code": "pattern_retagged", "pattern_id": best,
                    "detail": f"{target.get('id')} → {best}: {reason}"}
        return {"action": "reject", "code": "pattern_mismatch", "detail": f"reviewer: {reason}"}
    return {"action": "accept", "code": None, "detail": reason}
