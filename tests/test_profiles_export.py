from __future__ import annotations

import json
from pathlib import Path

import pytest

from darsamooz.models import Difficulty, Question, ReviewStatus
from darsamooz.profiles import (
    DEFAULT_PROFILE_ID,
    export_with_profile,
    list_profiles,
    load_profile,
    map_question,
)


def _q(i: int, correct: int = 0, status: ReviewStatus = ReviewStatus.pending) -> Question:
    return Question(
        id=f"t-q{i:03d}",
        chapter=1,
        chapter_title="فیزیک و اندازه‌گیری",
        stem=f"سؤال آزمایشی شماره {i} چیست؟",
        options=["گزینه یک", "گزینه دو", "گزینه سه", "گزینه چهار"],
        correct_index=correct,
        explanation="توضیح کوتاه فارسی برای تست.",
        difficulty=Difficulty.medium,
        source_refs=["اندازه‌گیری"],
        review_status=status,
    )


def test_generic_profile_loads_and_maps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Point data_dir at repo data (profile ships with project)
    from darsamooz.config import get_settings

    settings = get_settings()
    profile = load_profile(DEFAULT_PROFILE_ID, settings)
    assert profile["id"] == "generic"
    assert "fields" in profile
    row = map_question(_q(1, correct=2), profile)
    assert row["answer"] == "C"
    assert row["answer_index"] == 2
    assert row["question_text"].startswith("سؤال")
    assert len(row["choices"]) == 4


def test_export_with_profile_json_and_approved_only(tmp_path: Path):
    from darsamooz.config import get_settings

    settings = get_settings()
    qs = [
        _q(1, status=ReviewStatus.approved),
        _q(2, status=ReviewStatus.pending),
        _q(3, status=ReviewStatus.approved),
    ]
    out = tmp_path / "mapped.json"
    export_with_profile(qs, out, profile_id="generic", settings=settings, approved_only=True)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["profile"] == "generic"
    assert data["count"] == 2
    assert "Replace mapping when the client" in data["note"] or "client" in data["note"].lower()


def test_list_profiles_includes_generic():
    ids = {p["id"] for p in list_profiles()}
    assert "generic" in ids
