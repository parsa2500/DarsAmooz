from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"


class ReviewStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    needs_review = "needs_review"


class Question(BaseModel):
    """A single multiple-choice test item (تست چهارگزینه‌ای)."""

    id: str = Field(..., description="Stable id, e.g. phys10-ch1-q003")
    subject: str = Field(default="فیزیک ۱ دهم ریاضی")
    chapter: int = Field(..., ge=1)
    chapter_title: str = ""
    section: str | None = None
    stem: str = Field(..., min_length=5, description="Question text in Persian")
    options: list[str] = Field(..., min_length=4, max_length=4)
    correct_index: int = Field(..., ge=0, le=3, description="0-based index of correct option")
    explanation: str = Field(..., min_length=3)
    difficulty: Difficulty = Difficulty.medium
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_sample: bool = False
    review_status: ReviewStatus = ReviewStatus.pending
    verifier_answer_index: int | None = Field(
        default=None, ge=0, le=3, description="Blind verifier's chosen option index"
    )
    verifier_reason: str | None = None
    verifier_agrees: bool | None = None
    verifier_ambiguous: bool | None = None

    @field_validator("options")
    @classmethod
    def options_nonempty(cls, v: list[str]) -> list[str]:
        if len(v) != 4:
            raise ValueError("exactly 4 options required")
        if any(not o or not o.strip() for o in v):
            raise ValueError("options must be non-empty")
        if len({o.strip() for o in v}) < 4:
            raise ValueError("options must be unique")
        return [o.strip() for o in v]

    @model_validator(mode="after")
    def correct_in_range(self) -> Question:
        if not (0 <= self.correct_index < 4):
            raise ValueError("correct_index out of range")
        return self


class Chapter(BaseModel):
    number: int
    title: str
    start_char: int = 0
    end_char: int = 0
    text: str = ""


class Section(BaseModel):
    """A textbook subsection unit used for grounded generation."""

    id: str = Field(..., description="e.g. ch1-s02 or ch1-u003")
    chapter: int = Field(..., ge=1)
    title: str = ""
    text: str = ""
    order: int = 0
    char_start: int = 0
    char_end: int = 0


class TextbookManifest(BaseModel):
    book_code: str
    title: str
    track: str
    grade: str
    source_url: str
    official_page: str
    chapters: list[dict]
    notes: str = ""


class GenerationRequest(BaseModel):
    chapter: int
    count: int = 10
    difficulty: Difficulty | Literal["mixed"] = "mixed"
    language: str = "fa"
