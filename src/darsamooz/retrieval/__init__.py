"""
Phase 2 retrieval (BM25 + TF-IDF).

Intentionally NOT wired into ``generate`` / planner / web launcher.
Use ``build_chapter_index`` and ``retrieve_sections`` from CLI or future callers.
"""

from darsamooz.retrieval.service import (
    build_all_chapter_indexes,
    build_chapter_index,
    retrieve_sections,
)

__all__ = [
    "build_chapter_index",
    "build_all_chapter_indexes",
    "retrieve_sections",
]
