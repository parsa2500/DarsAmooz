"""Curriculum topic packs used when the LLM gateway blocks raw Persian textbook text."""

from __future__ import annotations

import re
from dataclasses import dataclass

from darsamooz.models import Chapter


@dataclass
class TopicPack:
    chapter: int
    title_fa: str
    title_en: str
    topics_en: list[str]
    formulas: list[str]
    notes_en: list[str]


CHAPTER1 = TopicPack(
    chapter=1,
    title_fa="فیزیک و اندازه‌گیری",
    title_en="Physics and Measurement",
    topics_en=[
        "What physics studies; modeling and idealization",
        "Physical quantities vs numbers; measurement",
        "Scalar vs vector quantities (examples)",
        "Base SI quantities and units (length m, mass kg, time s, …)",
        "Derived units; unit conversion and prefixes",
        "Significant figures and estimation",
        "Density ρ = m / V and applications",
    ],
    formulas=["ρ = m / V", "SI base: m, kg, s", "prefixes: k, c, m, μ, …"],
    notes_en=[
        "Grade-10 Iranian math-track Physics 1 (book code 110209).",
        "Keep answers suitable for تست چهارگزینه‌ای exams.",
    ],
)

CHAPTER_PACKS: dict[int, TopicPack] = {
    1: CHAPTER1,
    2: TopicPack(
        chapter=2,
        title_fa="ویژگی‌های فیزیکی مواد",
        title_en="Physical Properties of Matter",
        topics_en=[
            "States of matter",
            "Intermolecular forces",
            "Pressure in fluids; hydrostatic pressure",
            "Buoyancy / Archimedes",
            "Fluids in motion; Bernoulli principle (intro)",
        ],
        formulas=["P = F / A", "P = P0 + ρgh", "F_b = ρ V g"],
        notes_en=["Iranian Physics 1 chapter 2."],
    ),
    3: TopicPack(
        chapter=3,
        title_fa="کار، انرژی و توان",
        title_en="Work, Energy and Power",
        topics_en=[
            "Kinetic energy",
            "Work done by a constant force",
            "Work–energy theorem",
            "Potential energy; mechanical energy conservation",
            "Internal energy and non-conservative work",
            "Power",
        ],
        formulas=["K = 1/2 mv^2", "W = F d cosθ", "P = W / t", "E_mech = K + U"],
        notes_en=["Iranian Physics 1 chapter 3."],
    ),
    4: TopicPack(
        chapter=4,
        title_fa="دما و گرما",
        title_en="Temperature and Heat",
        topics_en=[
            "Temperature and thermometry",
            "Thermal expansion",
            "Heat capacity and specific heat",
            "Phase changes; latent heat",
            "Heat transfer: conduction, convection, radiation",
            "Gas laws (intro)",
        ],
        formulas=["Q = mcΔT", "Q = mL", "ΔL = L0 α ΔT", "PV = nRT (intro)"],
        notes_en=["Iranian Physics 1 chapter 4."],
    ),
    5: TopicPack(
        chapter=5,
        title_fa="ترمودینامیک",
        title_en="Thermodynamics",
        topics_en=[
            "Equation of state; quasi-static processes",
            "Energy exchange; first law of thermodynamics",
            "Internal energy",
            "Selected thermodynamic processes",
            "Cycles; heat engines",
            "Second law; refrigerators (intro)",
        ],
        formulas=["ΔU = Q - W (sign convention as in the book)", "efficiency η = W/Q_h"],
        notes_en=["Iranian Physics 1 chapter 5."],
    ),
}


_FORMULA_RE = re.compile(
    r"(?:ρ\s*=\s*m\s*/\s*V)|"
    r"(?:P\s*=\s*F\s*/\s*A)|"
    r"(?:[A-Za-zρΔθη]\s*=\s*[^\n،.]{0,40})|"
    r"(?:\d+(?:\.\d+)?\s*(?:kg|m|s|N|J|W|Pa|°C|K)\b)",
    re.IGNORECASE,
)


def extract_formula_snippets(text: str, limit: int = 25) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _FORMULA_RE.finditer(text or ""):
        s = " ".join(m.group(0).split())
        if s.lower() in seen or len(s) < 3:
            continue
        seen.add(s.lower())
        found.append(s)
        if len(found) >= limit:
            break
    return found


def build_generation_user_prompt(
    chapter: Chapter,
    count: int,
    *,
    topic_index: int = 0,
    avoid_stems: list[str] | None = None,
) -> str:
    """
    Build an English grounding prompt.

    AgentRouter currently content-blocks raw Persian textbook dumps, so we
    ground on syllabus topic packs + ASCII/formula snippets extracted locally.
    """
    pack = CHAPTER_PACKS.get(chapter.number)
    formulas = extract_formula_snippets(chapter.text)
    topics = []
    if pack:
        topics = pack.topics_en
        formulas = list(dict.fromkeys(pack.formulas + formulas))
        title_en = pack.title_en
        title_fa = pack.title_fa
        notes = pack.notes_en
    else:
        title_en = f"Chapter {chapter.number}"
        title_fa = chapter.title
        notes = []

    # Rotate focus topics so batches cover the chapter
    if topics:
        focus = topics[topic_index % len(topics)]
        topic_list = "\n".join(f"- {t}" for t in topics)
    else:
        focus = "core chapter ideas"
        topic_list = "- (no pack; use formulas only)"

    formula_list = "\n".join(f"- {f}" for f in formulas[:20]) or "- (none extracted)"
    notes_list = "\n".join(f"- {n}" for n in notes) if notes else "- Iranian grade-10 physics"

    avoid_block = ""
    if avoid_stems:
        # AgentRouter content-blocks Persian dumps; send ASCII fingerprints only.
        fps: list[str] = []
        for s in avoid_stems[:12]:
            nums = re.findall(r"\d+(?:\.\d+)?\s*(?:kg|m|s|km|N|J|W|Pa|μm|mm|cm|K)?", s)
            fps.append(", ".join(nums[:4]) if nums else f"item-{len(fps)+1}")
        listed = "\n".join(f"- already used numeric motif: {fp}" for fp in fps)
        avoid_block = (
            "\nDo NOT reuse these numeric examples/motifs:\n"
            f"{listed}\n"
            "Also vary the conceptual angle; no near-duplicate stems.\n"
        )

    mix = (
        "Aim for difficulty mix across the batch "
        "(prefer at least one medium or hard when count>=3).\n"
    )

    return (
        f"Book: Physics 1, grade 10, math track (110209)\n"
        f"Chapter {chapter.number}: {title_en} / {title_fa}\n"
        f"Write {count} multiple-choice questions in Persian.\n"
        f"Focus especially on: {focus}\n"
        f"{mix}"
        f"{avoid_block}\n"
        f"Allowed topics for this chapter:\n{topic_list}\n\n"
        f"Useful formulas/units extracted or standard for this chapter:\n{formula_list}\n\n"
        f"Notes:\n{notes_list}\n"
        f"Do not invent advanced university topics. Stay within grade-10 scope."
    )
