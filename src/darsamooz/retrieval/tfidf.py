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
class TfidfIndex:
    """Sparse TF-IDF vectors (pure Python) over textbook sections."""

    section_ids: list[str]
    # term -> idf
    idf: dict[str, float]
    # parallel to section_ids: term -> tf-idf weight
    vectors: list[dict[str, float]]
    norms: list[float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "kind": "tfidf",
            "section_ids": self.section_ids,
            "idf": self.idf,
            "vectors": self.vectors,
            "norms": self.norms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TfidfIndex:
        return cls(
            section_ids=list(data["section_ids"]),
            idf={str(k): float(v) for k, v in (data.get("idf") or {}).items()},
            vectors=[
                {str(k): float(v) for k, v in (vec or {}).items()}
                for vec in (data.get("vectors") or [])
            ],
            norms=[float(x) for x in (data.get("norms") or [])],
        )


def build_tfidf(sections: list[Section]) -> TfidfIndex:
    section_ids: list[str] = []
    raw_tfs: list[Counter[str]] = []
    df: Counter[str] = Counter()

    for sec in sections:
        section_ids.append(sec.id)
        tokens = tokenize(f"{sec.title}\n{sec.text}")
        tf = Counter(tokens)
        raw_tfs.append(tf)
        for term in tf:
            df[term] += 1

    n = len(sections) or 1
    idf = {
        term: math.log((1.0 + n) / (1.0 + freq)) + 1.0 for term, freq in df.items()
    }

    vectors: list[dict[str, float]] = []
    norms: list[float] = []
    for tf in raw_tfs:
        vec: dict[str, float] = {}
        for term, count in tf.items():
            # log tf
            w = (1.0 + math.log(count)) * idf.get(term, 0.0)
            if w > 0:
                vec[term] = w
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        vectors.append(vec)
        norms.append(norm)

    return TfidfIndex(
        section_ids=section_ids, idf=idf, vectors=vectors, norms=norms
    )


def score_tfidf(index: TfidfIndex, query: str) -> list[tuple[str, float]]:
    q_tokens = tokenize(query)
    if not q_tokens or not index.section_ids:
        return []
    q_tf = Counter(q_tokens)
    q_vec: dict[str, float] = {}
    for term, count in q_tf.items():
        if term not in index.idf:
            continue
        q_vec[term] = (1.0 + math.log(count)) * index.idf[term]
    q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
    if not q_vec:
        return []

    scored: list[tuple[str, float]] = []
    for i, doc_vec in enumerate(index.vectors):
        dot = 0.0
        # iterate smaller side
        if len(q_vec) <= len(doc_vec):
            for term, qw in q_vec.items():
                dw = doc_vec.get(term)
                if dw:
                    dot += qw * dw
        else:
            for term, dw in doc_vec.items():
                qw = q_vec.get(term)
                if qw:
                    dot += qw * dw
        if dot <= 0:
            continue
        sim = dot / (q_norm * (index.norms[i] or 1.0))
        scored.append((index.section_ids[i], sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def save_tfidf(index: TfidfIndex, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_dict(), ensure_ascii=False), encoding="utf-8")
    return path


def load_tfidf(path: Path) -> TfidfIndex:
    data = json.loads(path.read_text(encoding="utf-8"))
    return TfidfIndex.from_dict(data)
