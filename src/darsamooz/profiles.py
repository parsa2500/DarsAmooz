from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from darsamooz.config import Settings, get_settings
from darsamooz.models import Question
from darsamooz.review import filter_by_review_status
from darsamooz.models import ReviewStatus

DEFAULT_PROFILE_ID = "generic"


def profiles_dir(settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.data_dir) / "export_profiles"


def list_profiles(settings: Settings | None = None) -> list[dict[str, Any]]:
    root = profiles_dir(settings)
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append(
            {
                "id": data.get("id") or path.stem,
                "name": data.get("name") or path.stem,
                "description": data.get("description") or "",
                "format": data.get("format") or "json",
                "path": str(path),
            }
        )
    return out


def load_profile(profile_id: str, settings: Settings | None = None) -> dict[str, Any]:
    root = profiles_dir(settings)
    path = root / f"{profile_id}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"Export profile {profile_id!r} not found under {root}. "
            "Add a mapping JSON when the client import format is known "
            f"(default profile id is {DEFAULT_PROFILE_ID!r})."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Profile {profile_id!r} must be a JSON object")
    data.setdefault("id", profile_id)
    data.setdefault("format", "json")
    data.setdefault("option_letters", ["A", "B", "C", "D"])
    data.setdefault("answer_encoding", "letter")
    data.setdefault("fields", {})
    return data


def _get_source_value(q: Question, key: str, letters: list[str]) -> Any:
    if key == "correct_letter":
        return letters[q.correct_index]
    if key == "difficulty":
        return q.difficulty.value
    if key == "review_status":
        return q.review_status.value
    if key == "options":
        return list(q.options)
    if key == "source_refs":
        return list(q.source_refs)
    if key == "tags":
        return list(q.tags)
    return getattr(q, key, None)


def map_question(q: Question, profile: dict[str, Any]) -> dict[str, Any]:
    letters = list(profile.get("option_letters") or ["A", "B", "C", "D"])
    fields: dict[str, str] = dict(profile.get("fields") or {})
    row: dict[str, Any] = {}
    for src, dest in fields.items():
        if src == "options":
            # Expand or keep as list depending on format handled later
            row[dest] = list(q.options)
            continue
        row[dest] = _get_source_value(q, src, letters)

    encoding = str(profile.get("answer_encoding") or "letter").lower()
    answer_field = fields.get("correct_letter") or fields.get("correct_index")
    if answer_field and encoding == "letter" and "correct_letter" in fields:
        row[fields["correct_letter"]] = letters[q.correct_index]
    if answer_field and encoding == "index" and "correct_index" in fields:
        row[fields["correct_index"]] = q.correct_index
    if encoding == "letter+index":
        if "correct_letter" in fields:
            row[fields["correct_letter"]] = letters[q.correct_index]
        if "correct_index" in fields:
            row[fields["correct_index"]] = q.correct_index
    return row


def _questions_for_profile(
    questions: list[Question], profile: dict[str, Any], *, approved_only: bool
) -> list[Question]:
    status_filter = profile.get("include_only_review_status")
    qs = list(questions)
    if approved_only:
        qs = filter_by_review_status(qs, approved_only=True)
    elif status_filter:
        if isinstance(status_filter, str):
            statuses = {ReviewStatus(status_filter)}
        else:
            statuses = {ReviewStatus(s) for s in status_filter}
        qs = filter_by_review_status(qs, statuses)
    return qs


def export_with_profile(
    questions: list[Question],
    path: Path,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    settings: Settings | None = None,
    approved_only: bool = False,
) -> Path:
    """Write questions using a mapping profile (JSON or CSV).

    The real client exam-site mapping should be added as a new profile file
    under data/export_profiles/ when the import format is provided. This
    function does not invent a client schema.
    """
    profile = load_profile(profile_id, settings)
    qs = _questions_for_profile(questions, profile, approved_only=approved_only)
    fmt = str(profile.get("format") or "json").lower()
    path.parent.mkdir(parents=True, exist_ok=True)
    mapped = [map_question(q, profile) for q in qs]

    if fmt == "csv":
        letters = list(profile.get("option_letters") or ["A", "B", "C", "D"])
        columns = list(profile.get("csv_columns") or [])
        if not columns:
            # Derive from fields + expanded choices
            columns = []
            for src, dest in (profile.get("fields") or {}).items():
                if src == "options":
                    for L in letters:
                        columns.append(f"choice_{L}")
                else:
                    columns.append(dest)
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            for q, row in zip(qs, mapped, strict=True):
                csv_row = dict(row)
                # Expand choices into choice_A.. columns when present
                opts = q.options
                for i, L in enumerate(letters):
                    key = f"choice_{L}"
                    if key in columns and i < len(opts):
                        csv_row[key] = opts[i]
                # Flatten list fields
                for k, v in list(csv_row.items()):
                    if isinstance(v, list):
                        csv_row[k] = "|".join(str(x) for x in v)
                w.writerow({c: csv_row.get(c, "") for c in columns})
        return path

    # JSON default
    payload = {
        "profile": profile.get("id"),
        "profile_name": profile.get("name"),
        "count": len(mapped),
        "note": (
            "Mapped via data/export_profiles. "
            "Replace mapping when the client import format arrives."
        ),
        "items": mapped,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
