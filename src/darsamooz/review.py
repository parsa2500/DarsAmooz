from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from darsamooz.export import export_csv, export_json, export_markdown
from darsamooz.models import Difficulty, Question, ReviewStatus
from darsamooz.validate import validate_question_dict


EDITABLE_FIELDS = frozenset(
    {"stem", "options", "correct_index", "explanation", "difficulty", "review_status"}
)


def filter_by_review_status(
    questions: list[Question],
    statuses: set[ReviewStatus] | None = None,
    *,
    approved_only: bool = False,
) -> list[Question]:
    if approved_only:
        statuses = {ReviewStatus.approved}
    if not statuses:
        return list(questions)
    return [q for q in questions if q.review_status in statuses]


def apply_review_edit(question: Question, patch: dict[str, Any]) -> Question:
    """Apply an allowed edit patch; validates options/correct_index."""
    unknown = set(patch) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"unsupported edit fields: {sorted(unknown)}")
    data = question.model_dump(mode="json")
    for key, value in patch.items():
        if key == "difficulty" and isinstance(value, str):
            data[key] = Difficulty(value.lower()).value
        elif key == "review_status" and isinstance(value, str):
            data[key] = ReviewStatus(value.lower()).value
        else:
            data[key] = value
    # Re-validate via Question model
    return Question.model_validate(data)


def load_questions_payload(path: Path) -> tuple[dict[str, Any], list[Question]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        items = raw
        meta: dict[str, Any] = {"count": len(items)}
    else:
        items = raw.get("questions") or []
        meta = {k: v for k, v in raw.items() if k != "questions"}
        meta.setdefault("count", len(items))
    questions: list[Question] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        # Prefer full model validate (includes review fields); fall back to validate_question_dict
        try:
            questions.append(Question.model_validate(item))
        except Exception:
            qid = item.get("id") or f"q{i:03d}"
            questions.append(
                validate_question_dict(
                    item,
                    chapter=int(item.get("chapter") or 1),
                    chapter_title=str(item.get("chapter_title") or ""),
                    qid=qid,
                    require_grounding=False,
                )
            )
    return meta, questions


def save_questions_payload(
    path: Path,
    questions: list[Question],
    *,
    meta: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(meta or {})
    payload["count"] = len(questions)
    payload["questions"] = [q.model_dump(mode="json") for q in questions]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_reviews_sidecar(run_root: Path, questions: list[Question]) -> Path:
    path = run_root / "reviews.json"
    rows = [
        {
            "id": q.id,
            "review_status": q.review_status.value,
            "verifier_answer_index": q.verifier_answer_index,
            "verifier_agrees": q.verifier_agrees,
            "verifier_ambiguous": q.verifier_ambiguous,
            "verifier_reason": q.verifier_reason,
        }
        for q in questions
    ]
    path.write_text(
        json.dumps({"count": len(rows), "reviews": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def reexport_questions(
    questions: list[Question],
    *,
    json_path: Path,
    csv_path: Path | None = None,
    md_path: Path | None = None,
    approved_only: bool = False,
) -> dict[str, Path]:
    selected = filter_by_review_status(questions, approved_only=approved_only)
    paths: dict[str, Path] = {}
    paths["json"] = export_json(selected, json_path)
    if csv_path is not None:
        paths["csv"] = export_csv(selected, csv_path)
    if md_path is not None:
        paths["md"] = export_markdown(selected, md_path)
    return paths


def review_summary(questions: list[Question]) -> dict[str, int]:
    counts = {s.value: 0 for s in ReviewStatus}
    for q in questions:
        counts[q.review_status.value] += 1
    counts["total"] = len(questions)
    return counts
