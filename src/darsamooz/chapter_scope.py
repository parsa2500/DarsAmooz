"""Keep the handout flow inside the chapter that is actually in the book.

Everything here is deterministic and grounded in the extracted book text:

* ``clean_section_titles`` turns the (noisy, pdftotext) section titles into real
  chapter headings.
* ``ground_search_queries`` builds web queries from those headings and drops
  LLM queries that drift off-chapter or use terms that never occur in the book
  (e.g. «فاکتور دوم نیوتون» for «فیزیک و اندازه‌گیری»).
* ``filter_patterns_to_chapter`` drops web patterns that cannot be mapped to a
  chapter section.
* ``trivial_recall_reason`` / ``garbled_tokens`` are cheap question-quality
  filters used during generation.

Nothing is dropped silently: every helper returns the dropped items with a
reason so callers can log and persist them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from darsamooz.models import Section

_AR_MAP = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "ۀ": "ه", "أ": "ا", "إ": "ا", "\u200c": " "})
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DIAC = re.compile(r"[\u064B-\u065F\u0670]")
_NONWORD = re.compile(r"[^\w\u0600-\u06FF]+", re.UNICODE)
_PART_SUFFIX = re.compile(r"\(\s*بخش\s*[0-9۰-۹]+\s*\)\s*$")
_LEADING_NUM = re.compile(r"^[\s\dو۰-۹ـ\-–—.]+")
_PERSIAN_LETTER = re.compile(r"[\u0620-\u064A\u067E\u0686\u0698\u06A9\u06AF\u06CC]")
_LATIN = re.compile(r"[A-Za-z]")

ORDINALS_FA = {
    1: "اول", 2: "دوم", 3: "سوم", 4: "چهارم", 5: "پنجم",
    6: "ششم", 7: "هفتم", 8: "هشتم", 9: "نهم", 10: "دهم",
}

# Words that say nothing about the *topic* of a query/pattern.
GENERIC_TOKENS = frozenset(
    """
    تست تستی تستها تست‌ها سوال سؤال سوالات سؤالات پرسش آزمون آزمونهای آزمون‌های کنکور کنکوری امتحان نهایی
    نمونه چهارگزینه چهارگزینه‌ای چهارگزینه ای گزینه گزینه‌ای جواب پاسخ پاسخنامه تشریحی کلیدی مهم
    فصل درس مبحث پایه کتاب جزوه دهم یازدهم دوازدهم دبیرستان متوسطه ریاضی تجربی رشته
    فیزیک ۱ 1 10 ۱۰ اول با و در از به برای ها های یک این آن را که بر تا هر کدام چه چیست
    همراه سایت دانلود pdf
    """.split()
)

TOPIC_GENERIC_TOKENS = GENERIC_TOKENS | frozenset(
    (
        "مفهوم مفاهیم مفهومی محاسبه محاسبهٔ محاسباتی تعیین شناسایی سؤال درباره دربارهٔ اهمیت مقدمه علمی "
        "کاربرد نقش انواع مقایسه گزاره گزاره‌ها درست نادرست صحیح غلط عددی تشخیص انتخاب استدلال تحلیل "
        "ترکیبی تعریفی حلی مثلا مثال است نیست باشد چند چقدر کدامیک"
    ).split()
)


_GENERIC_FOLDED: dict[int, frozenset[str]] = {}


def norm_fa(text: str, *, fold: bool = True) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    s = s.translate(_AR_MAP).translate(_FA_DIGITS)
    s = _DIAC.sub("", s)
    s = s.replace("ـ", "")
    if fold:
        # pdftotext breaks the lam-alef ligature («اختلاف» → «اختالف», «لازم» → «الزم»).
        # Fold both spellings to one form so correct words still match the book text.
        s = s.replace("لا", "ال")
    s = _NONWORD.sub(" ", s)
    return " ".join(s.split()).lower()


def tokens(text: str) -> list[str]:
    """Normalised tokens in readable form (no lam-alef folding)."""
    return [t for t in norm_fa(text, fold=False).split() if t]


def _folded_set(words: frozenset[str]) -> frozenset[str]:
    return frozenset(w for x in words for w in norm_fa(x).split())


def content_tokens(text: str, generic: frozenset[str] = GENERIC_TOKENS) -> list[str]:
    folded_generic = _GENERIC_FOLDED.get(id(generic))
    if folded_generic is None:
        folded_generic = _folded_set(generic)
        _GENERIC_FOLDED[id(generic)] = folded_generic
    out: list[str] = []
    for t in tokens(text):
        if t in generic or norm_fa(t) in folded_generic or len(t) < 2 or t.isdigit():
            continue
        if _LATIN.search(t) and not _PERSIAN_LETTER.search(t):
            continue  # formula symbols like F, ma, si
        if t not in out:
            out.append(t)
    return out


_SUFFIXES = ("هایی", "های", "ها", "ترین", "تر", "ات", "ای", "ی", "ان")


def _inflection_variants(token: str) -> list[str]:
    """Token plus the token with one common Persian suffix removed (واحدهای → واحد)."""
    out = [token]
    for suf in _SUFFIXES:
        if token.endswith(suf) and len(token) - len(suf) >= 3:
            out.append(token[: -len(suf)])
    return out


def _stem_match(a: str, b: str) -> bool:
    """Loose Persian match: equal, or one is a ≥3-char prefix of the other."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short)


def clean_section_titles(sections: list[Section], chapter_title: str = "") -> list[str]:
    """Real headings only: strip «(بخش n)», numbering; drop sentence fragments."""
    out: list[str] = []
    seen: set[str] = set()
    for s in sections:
        t = (s.title or "").strip()
        t = _PART_SUFFIX.sub("", t).strip()
        t = _LEADING_NUM.sub("", t).strip()
        t = _DIAC.sub("", t).replace(" :", ": ").replace("  ", " ").strip(" :،")
        t = " ".join(t.split())
        if len(t) < 3:
            continue
        if t.endswith((".", "؟", "?", "!")):
            continue  # a sentence picked up as a "title" by pdftotext
        words = t.split()
        if len(words) > 9 or words[-1] in {"و", "در", "از", "به", "که"}:
            continue  # truncated / run-on
        key = norm_fa(t)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(t)
    ct = " ".join((chapter_title or "").split())
    if ct and norm_fa(ct) not in seen:
        out.insert(0, ct)
    return out


@dataclass
class ChapterScope:
    chapter_num: int
    chapter_title: str
    section_titles: list[str]
    text_norm: str
    heading_tokens: list[str] = field(default_factory=list)
    section_text_norm: dict[str, str] = field(default_factory=dict)

    def in_book(self, token: str) -> bool:
        token = norm_fa(token).replace(" ", "")
        if not token:
            return False
        for cand in _inflection_variants(token):
            if f" {cand} " in f" {self.text_norm} ":
                return True
            # ZWNJ / spacing variants: «اندازه‌گیری» vs «اندازه گیری»
            if len(cand) >= 3 and cand in self._text_nospace:
                return True
        return False

    @property
    def _text_nospace(self) -> str:
        cached = getattr(self, "_nospace_cache", None)
        if cached is None:
            cached = self.text_norm.replace(" ", "")
            object.__setattr__(self, "_nospace_cache", cached)
        return cached

    def heading_hit(self, token: str) -> bool:
        t = norm_fa(token)
        return any(_stem_match(t, norm_fa(h)) for h in self.heading_tokens)

    def match_section(self, label: str) -> str | None:
        """Return the chapter section title that ``label`` refers to (fuzzy)."""
        lab = content_tokens(label, TOPIC_GENERIC_TOKENS)
        if not lab:
            return None
        lab_ns = norm_fa(label).replace(" ", "")
        chapter_key = norm_fa(self.chapter_title)
        best, best_score = None, 0.0
        for title in self.section_titles:
            if chapter_key and norm_fa(title) == chapter_key and len(self.section_titles) > 1:
                continue  # the chapter itself is not a section
            title_ns = norm_fa(title).replace(" ", "")
            if lab_ns and title_ns and (lab_ns in title_ns or title_ns in lab_ns):
                return title
            tt = content_tokens(title, TOPIC_GENERIC_TOKENS)
            if not tt:
                continue
            hits = sum(1 for t in tt if any(_stem_match(norm_fa(t), norm_fa(x)) for x in lab))
            score = hits / len(tt)
            if score > best_score:
                best, best_score = title, score
        return best if best_score >= 0.5 else None


def build_scope(
    sections: list[Section], *, chapter_num: int, chapter_title: str
) -> ChapterScope:
    titles = clean_section_titles(sections, chapter_title)
    heading: list[str] = []
    for t in titles:
        for tok in content_tokens(t, TOPIC_GENERIC_TOKENS):
            if tok not in heading:
                heading.append(tok)
    full = norm_fa("\n".join(s.text or "" for s in sections))
    return ChapterScope(
        chapter_num=chapter_num,
        chapter_title=chapter_title,
        section_titles=titles,
        text_norm=full,
        heading_tokens=heading,
    )


# ------------------------------------------------------------------ queries


def check_query(query: str, scope: ChapterScope) -> str | None:
    """None when the query is on-chapter, else a short reason."""
    toks = content_tokens(query)
    if not toks:
        return "no_topic_terms"
    missing = [t for t in toks if not scope.in_book(t)]
    if missing:
        return "term_not_in_book: " + "، ".join(missing)
    if not any(scope.heading_hit(t) for t in toks):
        return "off_chapter: no chapter-section term"
    return None


def grounded_queries_from_sections(
    scope: ChapterScope, *, subject: str = "", grade: str = ""
) -> list[str]:
    base = " ".join(x for x in (subject, grade) if x).strip() or "فیزیک دهم"
    qs: list[str] = []
    ordinal = ORDINALS_FA.get(scope.chapter_num)
    if scope.chapter_title:
        qs.append(f"تست {base} فصل {ordinal or scope.chapter_num} {scope.chapter_title}")
    for title in scope.section_titles:
        if norm_fa(title) == norm_fa(scope.chapter_title):
            continue
        qs.append(f"تست {base} {title.replace(':', '')}")
    return [" ".join(q.split()) for q in qs]


def ground_search_queries(
    profile: dict[str, Any], scope: ChapterScope, *, max_queries: int = 6
) -> dict[str, Any]:
    """Section-grounded queries first, then on-chapter LLM queries.

    Returns ``{"queries": [...], "dropped": [{"query", "reason"}], "section_titles": [...]}``.
    """
    dropped: list[dict[str, str]] = []
    kept_llm: list[str] = []
    for q in profile.get("search_queries") or []:
        reason = check_query(str(q), scope)
        if reason:
            dropped.append({"query": str(q), "reason": reason})
        else:
            kept_llm.append(str(q))
    grounded = grounded_queries_from_sections(
        scope, subject=str(profile.get("subject") or ""), grade=str(profile.get("grade") or "")
    )
    out: list[str] = []
    seen: set[str] = set()
    # Interleave so both the chapter heading and every section get searched early.
    for q in [*grounded[:1], *kept_llm[:1], *grounded[1:], *kept_llm[1:]]:
        k = norm_fa(q)
        if k and k not in seen:
            seen.add(k)
            out.append(q)
    return {
        "queries": out[: max(1, max_queries)],
        "dropped": dropped,
        "section_titles": scope.section_titles,
    }


def filter_topic_items(items: list[str], scope: ChapterScope) -> tuple[list[str], list[dict[str, str]]]:
    """Drop topic/concept strings that use any word absent from the book text."""
    kept: list[str] = []
    dropped: list[dict[str, str]] = []
    for it in items:
        toks = content_tokens(it, TOPIC_GENERIC_TOKENS)
        missing = [t for t in toks if not scope.in_book(t)]
        if missing:
            dropped.append({"item": it, "reason": "term_not_in_book: " + "، ".join(missing)})
        else:
            kept.append(it)
    return kept, dropped


# ------------------------------------------------------------------ patterns


def check_pattern(pattern: dict[str, Any], scope: ChapterScope) -> tuple[str | None, str | None]:
    """Return (matched_section_title, drop_reason)."""
    if pattern.get("off_chapter") is True:
        return None, "off_chapter: model marked it outside this chapter"
    declared = str(pattern.get("section") or "").strip()
    matched = scope.match_section(declared) if declared else None
    if matched is None:
        return None, (
            f"off_chapter: section «{declared}» is not a section of this chapter"
            if declared
            else "off_chapter: no chapter section given"
        )
    toks = content_tokens(str(pattern.get("name_fa") or ""), TOPIC_GENERIC_TOKENS)
    if toks:
        present = sum(1 for t in toks if scope.in_book(t))
        if present / len(toks) < 0.5:
            missing = [t for t in toks if not scope.in_book(t)]
            return matched, "term_not_in_book: " + "، ".join(missing[:6])
    return matched, None


def filter_patterns_to_chapter(
    patterns: list[dict[str, Any]], scope: ChapterScope
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for p in patterns:
        matched, reason = check_pattern(p, scope)
        if reason:
            dropped.append({"name_fa": p.get("name_fa"), "section": p.get("section"), "reason": reason})
            continue
        p = {**p, "section": matched}
        p.pop("off_chapter", None)
        kept.append(p)
    for i, p in enumerate(kept, 1):
        p["id"] = f"p{i}"
    return kept, dropped


# ------------------------------------------------------------------ question quality

_DEF_ASK = re.compile(
    r"(به\s*معنا(ی)?\s*(چه|چیست)|یعنی\s*چه|منظور\s*از\s*.{1,30}\s*چیست|تعریف\s*.{1,30}\s*(کدام|چیست)"
    r"|.{1,25}\s*چیست\s*[؟?]?\s*$|.{1,25}\s*چه\s*(است|نام\s*دارد)\s*[؟?]?\s*$)"
)


def trivial_recall_reason(stem: str, options: list[str], correct_index: int, section_titles: list[str]) -> str | None:
    """Detect one-line definition/heading recall questions (e.g. «فیزیک به معنای چه است؟»)."""
    words = tokens(stem)
    if len(words) < 5:
        return "trivial_recall: stem shorter than 5 words"
    if len(words) <= 8 and _DEF_ASK.search(" ".join(stem.split())):
        return "trivial_recall: one-line definition question"
    try:
        correct = norm_fa(options[correct_index])
    except (IndexError, TypeError):
        correct = ""
    if correct and len(words) <= 10:
        for t in section_titles:
            nt = norm_fa(t)
            if len(correct) >= 4 and correct in nt:
                return f"trivial_recall: answer is the section heading «{t}»"
    return None


def garbled_tokens(texts: list[str], scope: ChapterScope) -> list[str]:
    """Tokens that look garbled/transliterated AND never occur in the book.

    Flags: Persian letters with vocalisation marks (اِسکاپی‌التور), Latin+Persian
    letters mixed inside one word, or a letter repeated 3+ times.
    """
    bad: list[str] = []
    for text in texts:
        for raw in re.split(r"[\s\u200c،,.؛;:!?؟()«»\"'/\[\]{}=+\-–—]+", text or ""):
            if not raw or not _PERSIAN_LETTER.search(raw):
                continue
            suspicious = (
                bool(_DIAC.search(raw))
                or bool(_LATIN.search(raw))
                or bool(re.search(r"(.)\1\1", raw))
            )
            if not suspicious:
                continue
            tok = norm_fa(raw).replace(" ", "")
            if tok and not scope.in_book(tok) and raw not in bad:
                bad.append(raw)
    return bad


def unknown_terms(terms: list[str], scope: ChapterScope) -> list[str]:
    """Of the terms a reviewer called garbled, keep the ones not in the book text."""
    out: list[str] = []
    for t in terms or []:
        tok = norm_fa(str(t))
        if not tok:
            continue
        if not all(scope.in_book(x) for x in tok.split()):
            out.append(str(t))
    return out


def load_scope(book_slug: str, chapter: int, settings: Any = None) -> tuple[ChapterScope | None, list[Section]]:
    """Build the scope from stored sections; (None, []) when the chapter has no text yet."""
    from darsamooz.ingest import chapter_title_for
    from darsamooz.sections import load_sections

    try:
        sections = load_sections(book_slug, chapter, settings)
    except FileNotFoundError:
        return None, []
    if not sections:
        return None, []
    title = chapter_title_for(book_slug, chapter, settings)
    return build_scope(sections, chapter_num=chapter, chapter_title=title), sections


_SENT_SPLIT = re.compile(r"(?<=[.؟?!])\s+|\n{2,}")


def book_snippets(sections: list[Section], query: str, *, k: int = 2, width: int = 220) -> list[str]:
    """Up to ``k`` book sentences that share the most topic words with ``query``."""
    q = [norm_fa(t) for t in content_tokens(query, TOPIC_GENERIC_TOKENS)]
    if not q:
        return []
    scored: list[tuple[int, int, str]] = []
    order = 0
    for sec in sections:
        for sent in _SENT_SPLIT.split(sec.text or ""):
            flat = " ".join(sent.split())
            if len(flat) < 20:
                continue
            words = norm_fa(flat).split()
            hits = sum(1 for t in q if any(_stem_match(t, w) for w in words))
            if hits:
                scored.append((hits, -order, flat[:width]))
            order += 1
    scored.sort(reverse=True)
    out: list[str] = []
    for _h, _o, txt in scored:
        if txt not in out:
            out.append(txt)
        if len(out) >= k:
            break
    return out
