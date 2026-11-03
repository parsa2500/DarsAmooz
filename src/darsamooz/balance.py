from __future__ import annotations

import hashlib
import random
import re
from collections import Counter
from typing import Iterable

from darsamooz.models import Difficulty, Question, QuestionPattern

_MIX_ITEM = re.compile(
    r"^\s*(easy|medium|hard)\s*=\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)
_PATTERN_MIX_ITEM = re.compile(
    r"^\s*(tarifi|mafhumi|halli|tarkibi|taalifi|taerifi)\s*=\s*(\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)

# Alias user-facing typos / variants → canonical enum
_PATTERN_ALIASES = {
    "taalifi": QuestionPattern.tarifi,
    "تعلیفی": QuestionPattern.tarifi,
    "taerifi": QuestionPattern.tarifi,
    "تعریفی": QuestionPattern.tarifi,
    "مفهومی": QuestionPattern.mafhumi,
    "حلی": QuestionPattern.halli,
    "محاسباتی": QuestionPattern.halli,
    "ترکیبی": QuestionPattern.tarkibi,
}

_NUMBER_OR_FORMULA = re.compile(
    r"\d|[=≠≈<>≤≥/\\^²³√×·⋅ρμη]|kg|m/s|N·|J\b|W\b|Pa\b|°C",
    re.IGNORECASE,
)
_DEF_STEM = re.compile(
    r"(کدام\s*تعریف|به\s*معنای|یعنی\s*چه|چه\s*معنایی|تعریف\s+)",
    re.IGNORECASE,
)

DIFFICULTY_RUBRIC_FA = {
    Difficulty.easy: (
        "آسان: تعریف یا یکای مستقیم از متن؛ بدون محاسبه چندمرحله‌ای؛ "
        "گزینه‌های غلط باید واضح اشتباه باشند."
    ),
    Difficulty.medium: (
        "متوسط: کاربرد یک مفهوم یا محاسبهٔ یک‌مرحله‌ای با اعداد/فرمول متن؛ "
        "distractorها نزدیک ولی اشتباه."
    ),
    Difficulty.hard: (
        "سخت: حداقل یکی از این‌ها الزامی است — پیوند دو مفهوم، تبدیل واحد چندمرحله‌ای، "
        "تلهٔ مفهومی رایج، یا ترکیب دو فرمول. distractorها باید به جواب درست نزدیک باشند. "
        "صرفاً بازنویسی تعریف از متن به هیچ‌وجه hard نیست."
    ),
}

PATTERN_RUBRIC_FA = {
    QuestionPattern.tarifi: "تعریفی (tarifi): معنای اصطلاح/قانون/یکا مستقیماً از متن.",
    QuestionPattern.mafhumi: (
        "مفهومی (mafhumi): تشخیص مفهوم درست/نادرست بدون محاسبه سنگین عددی."
    ),
    QuestionPattern.halli: (
        "حلی (halli): حتماً عدد و محاسبه/فرمول از متن یونیت؛ جواب عددی یا نتیجه‌ی محاسبه."
    ),
    QuestionPattern.tarkibi: (
        "ترکیبی (tarkibi): پیوند دو رابطه یا دو زیربخش از متن در یک سوال."
    ),
}

PATTERN_LABEL_FA = {
    QuestionPattern.tarifi: "تعریفی",
    QuestionPattern.mafhumi: "مفهومی",
    QuestionPattern.halli: "حلی",
    QuestionPattern.tarkibi: "ترکیبی",
}


def parse_difficulty_mix(spec: str | None) -> dict[Difficulty, float] | None:
    """Parse ``easy=30,medium=50,hard=20`` into normalized weights (sum=1)."""
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


def normalize_pattern_token(raw: str) -> QuestionPattern:
    key = str(raw).strip().lower()
    if key in _PATTERN_ALIASES:
        return _PATTERN_ALIASES[key]
    # Persian labels without prior alias
    if raw.strip() in _PATTERN_ALIASES:
        return _PATTERN_ALIASES[raw.strip()]
    return QuestionPattern(key)


def parse_pattern_mix(spec: str | None) -> dict[QuestionPattern, float] | None:
    """Parse ``tarifi=20,mafhumi=40,halli=30,tarkibi=10`` into normalized weights."""
    if spec is None:
        return None
    text = str(spec).strip()
    if not text:
        return None
    weights: dict[QuestionPattern, float] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = _PATTERN_MIX_ITEM.match(part)
        if not m:
            raise ValueError(
                f"Invalid pattern mix item {part!r}. "
                "Use e.g. tarifi=20,mafhumi=40,halli=30,tarkibi=10"
            )
        key = normalize_pattern_token(m.group(1))
        weights[key] = float(m.group(2))
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("pattern mix weights must sum to a positive number")
    return {k: v / total for k, v in weights.items()}


def _assign_by_weights(count: int, mix: dict, order: list) -> list:
    if not mix or count <= 0:
        return [None] * count
    raw = {d: mix.get(d, 0.0) * count for d in order}
    floors = {d: int(raw[d]) for d in order}
    remainders = sorted(((raw[d] - floors[d], d) for d in order), reverse=True)
    assigned = floors.copy()
    left = count - sum(floors.values())
    for i in range(left):
        assigned[remainders[i % len(remainders)][1]] += 1
    targets: list = []
    for d in order:
        targets.extend([d] * assigned.get(d, 0))
    rng = random.Random(count * 17 + 31 + len(order))
    rng.shuffle(targets)
    while len(targets) < count:
        targets.append(None)
    return targets[:count]


def assign_target_difficulties(
    count: int, mix: dict[Difficulty, float] | None
) -> list[Difficulty | None]:
    return _assign_by_weights(
        count, mix, [Difficulty.easy, Difficulty.medium, Difficulty.hard]
    )


def assign_target_patterns(
    count: int, mix: dict[QuestionPattern, float] | None
) -> list[QuestionPattern | None]:
    return _assign_by_weights(
        count,
        mix,
        [
            QuestionPattern.tarifi,
            QuestionPattern.mafhumi,
            QuestionPattern.halli,
            QuestionPattern.tarkibi,
        ],
    )


def _seed_for(question_id: str) -> int:
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def balance_answer_positions(questions: list[Question]) -> list[Question]:
    """Deterministically shuffle options so correct answers spread over A–D."""
    if not questions:
        return questions

    shuffled: list[Question] = []
    for q in questions:
        rng = random.Random(_seed_for(q.id))
        order = list(range(4))
        rng.shuffle(order)
        new_options = [q.options[i] for i in order]
        new_correct = order.index(q.correct_index)
        shuffled.append(
            q.model_copy(update={"options": new_options, "correct_index": new_correct})
        )

    adjusted: list[Question] = []
    running = Counter()
    for q in shuffled:
        best_rot = 0
        best_score = None
        for rot in range(4):
            new_idx = (q.correct_index - rot) % 4
            projected = running[new_idx] + 1
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
    pattern_counts = {p.value: 0 for p in QuestionPattern}
    for q in qs:
        diff_counts[q.difficulty.value] += 1
        pattern_counts[q.pattern.value] += 1
    return {
        "answer_positions": answer_counts,
        "difficulties": diff_counts,
        "patterns": pattern_counts,
        "total": len(qs),
    }


def difficulty_prompt_hint(target: Difficulty | None) -> str:
    if target is None:
        lines = ["سختی را متنوع انتخاب کن و فیلد difficulty را درست بگذار."]
        lines.extend(DIFFICULTY_RUBRIC_FA.values())
        return " ".join(lines)
    return (
        f"سختی این تست باید {target.value} باشد. "
        f"{DIFFICULTY_RUBRIC_FA[target]} "
        f"(فیلد difficulty={target.value})"
    )


def pattern_prompt_hint(target: QuestionPattern | None) -> str:
    if target is None:
        lines = ["الگوی سوال را متنوع انتخاب کن و فیلد pattern را پر کن."]
        lines.extend(PATTERN_RUBRIC_FA.values())
        return " ".join(lines)
    return (
        f"الگوی این تست باید {PATTERN_LABEL_FA[target]} باشد. "
        f"{PATTERN_RUBRIC_FA[target]} "
        f"(فیلد pattern={target.value})"
    )


def looks_like_soft_hard(stem: str, options: list[str], difficulty: Difficulty) -> bool:
    """True when claimed hard but content looks like plain definitional recall."""
    if difficulty != Difficulty.hard:
        return False
    blob = " ".join([stem, *options])
    if _NUMBER_OR_FORMULA.search(blob):
        return False
    if len(stem.strip()) >= 120 and not _DEF_STEM.search(stem):
        # Longer conceptual stem without numbers can still be hard-ish; allow
        return False
    return True


def halli_missing_calc_signal(stem: str, options: list[str], pattern: QuestionPattern) -> bool:
    if pattern != QuestionPattern.halli:
        return False
    blob = " ".join([stem, *options])
    return _NUMBER_OR_FORMULA.search(blob) is None
