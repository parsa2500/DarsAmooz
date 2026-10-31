from __future__ import annotations

import hashlib
import random
import re
from collections import Counter
from typing import Iterable

from darsamooz.models import Difficulty, Question

_MIX_ITEM = re.compile(
    r"^\s*(easy|medium|hard)\s*=\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)


def parse_difficulty_mix(spec: str | None) -> dict[Difficulty, float] | None:
    """Parse ``easy=30,medium=50,hard=20`` into normalized weights (sum=1).

    Returns None when spec is empty/None (no steering).
    """
    if spec is None:
        return None
    text = str(spec).strip()
    if not text:
        return None
    weights: dict[Difficulty, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = _MIX_ITEM.match(part)
        if not m:
            raise ValueError(
                f"Invalid difficulty mix item {part!r}. "
                "Use e.g. easy=30,medium=50,hard=20"
            )
        key = Difficulty(m.group(1).lower())
        weights[key] = float(m.group(2))
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("difficulty mix weights must sum to a positive number")
    return {k: v / total for k, v in weights.items()}


def assign_target_difficulties(
    count: int, mix: dict[Difficulty, float] | None
) -> list[Difficulty | None]:
    """Return a list of length ``count`` with target difficulties (or None)."""
    if not mix or count <= 0:
        return [None] * count
    # Largest remainder method for integer quotas
    raw = {d: mix.get(d, 0.0) * count for d in Difficulty}
    floors = {d: int(raw[d]) for d in Difficulty}
    remainders = sorted(
        ((raw[d] - floors[d], d) for d in Difficulty), reverse=True
    )
    assigned = floors.copy()
    left = count - sum(floors.values())
    for i in range(left):
        assigned[remainders[i % len(remainders)][1]] += 1
    targets: list[Difficulty | None] = []
    for d in (Difficulty.easy, Difficulty.medium, Difficulty.hard):
        targets.extend([d] * assigned.get(d, 0))
    # Stable shuffle by count seed so order is spread, not clumped only at end
    rng = random.Random(count * 17 + 31)
    rng.shuffle(targets)
    while len(targets) < count:
        targets.append(None)
    return targets[:count]


def _seed_for(question_id: str) -> int:
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def balance_answer_positions(questions: list[Question]) -> list[Question]:
    """Deterministically shuffle options so correct answers spread over A–D.

    Uses each question's id as the RNG seed, then applies a global rotation
    bias so the batch's correct_index histogram is as even as possible.
    """
    if not questions:
        return questions

    # First pass: independent shuffle per question
    shuffled: list[Question] = []
    for q in questions:
        rng = random.Random(_seed_for(q.id))
        order = list(range(4))
        rng.shuffle(order)
        new_options = [q.options[i] for i in order]
        # Where did the old correct land?
        new_correct = order.index(q.correct_index)
        shuffled.append(
            q.model_copy(update={"options": new_options, "correct_index": new_correct})
        )

    # Second pass: rotate option lists so correct lands in the current least-filled bucket.
    # opts = options[rot:] + options[:rot] moves old index i → (i - rot) % 4.
    adjusted: list[Question] = []
    running = Counter()
    for q in shuffled:
        best_rot = 0
        best_score = None
        for rot in range(4):
            new_idx = (q.correct_index - rot) % 4
            projected = running[new_idx] + 1
            # Prefer least-filled; tiny tie-break favors smaller rot (stability)
            score = (projected, rot)
            if best_score is None or score < best_score:
                best_score = score
                best_rot = rot
        if best_rot == 0:
            adjusted.append(q)
            running[q.correct_index] += 1
            continue
        opts = q.options[best_rot:] + q.options[:best_rot]
        new_idx = (q.correct_index - best_rot) % 4
        adjusted.append(
            q.model_copy(update={"options": opts, "correct_index": new_idx})
        )
        running[new_idx] += 1
    return adjusted


def distribution_metrics(questions: Iterable[Question]) -> dict:
    qs = list(questions)
    answer_counts = {letter: 0 for letter in "ABCD"}
    letters = "ABCD"
    for q in qs:
        answer_counts[letters[q.correct_index]] += 1
    diff_counts = {d.value: 0 for d in Difficulty}
    for q in qs:
        diff_counts[q.difficulty.value] += 1
    return {
        "answer_positions": answer_counts,
        "difficulties": diff_counts,
        "total": len(qs),
    }


def difficulty_prompt_hint(target: Difficulty | None) -> str:
    if target is None:
        return "سختی را بین easy/medium/hard متنوع انتخاب کن."
    fa = {
        Difficulty.easy: "آسان (easy)",
        Difficulty.medium: "متوسط (medium)",
        Difficulty.hard: "سخت (hard)",
    }
    return f"سختی این تست باید {fa[target]} باشد (فیلد difficulty برابر {target.value})."
