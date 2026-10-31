from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from darsamooz.models import Section
from darsamooz.retrieval.tokenize import tokenize


@dataclass
class BM25Index:
    """Serializable Okapi BM25 index over textbook sections."""

    section_ids: list[str]
    doc_lens: list[int]
    avgdl: float
    df: dict[str, int]
    # term -> list of (doc_index, tf)
    postings: dict[str, list[list[int]]]
    k1: float = 1.5
    b: float = 0.75

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "kind": "bm25",
            "section_ids": self.section_ids,
            "doc_lens": self.doc_lens,
            "avgdl": self.avgdl,
            "df": self.df,
            "postings": self.postings,
            "k1": self.k1,
            "b": self.b,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BM25Index:
        return cls(
            section_ids=list(data["section_ids"]),
            doc_lens=list(data["doc_lens"]),
            avgdl=float(data["avgdl"]),
            df={str(k): int(v) for k, v in (data.get("df") or {}).items()},
            postings={
                str(k): [[int(a), int(b)] for a, b in pairs]
                for k, pairs in (data.get("postings") or {}).items()
            },
            k1=float(data.get("k1", 1.5)),
            b=float(data.get("b", 0.75)),
        )


def build_bm25(sections: list[Section], *, k1: float = 1.5, b: float = 0.75) -> BM25Index:
    section_ids: list[str] = []
    doc_lens: list[int] = []
    postings: dict[str, list[list[int]]] = {}
    df: Counter[str] = Counter()

    for i, sec in enumerate(sections):
        section_ids.append(sec.id)
        tokens = tokenize(f"{sec.title}\n{sec.text}")
        doc_lens.append(len(tokens))
        tf = Counter(tokens)
        for term, count in tf.items():
            df[term] += 1
            postings.setdefault(term, []).append([i, count])

    n = len(sections) or 1
    avgdl = sum(doc_lens) / n if doc_lens else 0.0
    return BM25Index(
        section_ids=section_ids,
        doc_lens=doc_lens,
        avgdl=avgdl or 1.0,
        df=dict(df),
        postings=postings,
        k1=k1,
        b=b,
    )


def score_bm25(index: BM25Index, query: str) -> list[tuple[str, float]]:
    q_terms = tokenize(query)
    if not q_terms or not index.section_ids:
        return []

    n = len(index.section_ids)
    scores = [0.0] * n
    k1, b = index.k1, index.b
    avgdl = index.avgdl or 1.0

    for term in set(q_terms):
        df = index.df.get(term, 0)
        if df <= 0:
            continue
        idf = math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        for doc_i, tf in index.postings.get(term, []):
            dl = index.doc_lens[doc_i] or 1
            denom = tf + k1 * (1.0 - b + b * dl / avgdl)
            scores[doc_i] += idf * (tf * (k1 + 1.0)) / denom

    ranked = sorted(
        ((index.section_ids[i], scores[i]) for i in range(n) if scores[i] > 0),
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked


def save_bm25(index: BM25Index, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path


def load_bm25(path: Path) -> BM25Index:
    data = json.loads(path.read_text(encoding="utf-8"))
    return BM25Index.from_dict(data)
