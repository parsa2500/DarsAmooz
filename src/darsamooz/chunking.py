from __future__ import annotations

import re

from darsamooz.models import Chapter


def _hard_split(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def chunk_chapter(chapter: Chapter, max_chars: int = 3500, overlap: int = 200) -> list[str]:
    """
    Split a chapter into overlapping text chunks suitable for LLM context.

    Prefers paragraph boundaries; hard-splits oversized paragraphs.
    """
    text = chapter.text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paras = re.split(r"\n\s*\n|\f", text)
    units: list[str] = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        units.extend(_hard_split(p, max_chars))

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in units:
        extra = len(p) + (2 if buf else 0)
        if size + extra > max_chars and buf:
            chunks.append("\n\n".join(buf))
            if overlap > 0:
                prev = chunks[-1]
                tail = prev[-overlap:]
                buf = [tail, p]
                size = len(tail) + 2 + len(p)
            else:
                buf = [p]
                size = len(p)
        else:
            buf.append(p)
            size += extra
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks
