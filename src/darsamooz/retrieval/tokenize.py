from __future__ import annotations

import re

from darsamooz.validate import normalize_fa

# Keep Latin alphanumerics (units like kg, SI) and Persian letters/digits.
_TOKEN = re.compile(r"[0-9a-zA-Z\u0600-\u06FF]+", re.UNICODE)

# Very common Persian function words — drop for BM25/TF-IDF signal.
_STOP = frozenset(
    {
        "و",
        "در",
        "به",
        "از",
        "که",
        "این",
        "آن",
        "را",
        "با",
        "برای",
        "یک",
        "یا",
        "تا",
        "بر",
        "می",
        "شد",
        "است",
        "هست",
        "بود",
        "شود",
        "کرد",
        "کنید",
        "خود",
        "هم",
        "نیز",
        "اگر",
        "اما",
        "چون",
        "مثل",
        "روی",
        "پس",
        "هر",
        "چه",
        "را",
    }
)


def tokenize(text: str, *, drop_stop: bool = True) -> list[str]:
    """Normalize Persian text and split into retrieval tokens."""
    norm = normalize_fa(text)
    if not norm:
        return []
    toks = [t.lower() for t in _TOKEN.findall(norm)]
    if drop_stop:
        toks = [t for t in toks if t not in _STOP and len(t) > 1]
    return toks
