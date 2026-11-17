"""Real web search for exam-test *styles* (step 2 of the handout flow).

Backends (all free / no extra cost):

* ``gemini``     — Gemini API ``google_search`` grounding (uses GEMINI_API_KEY).
                   Source URLs come from ``groundingMetadata`` (not model text).
* ``duckduckgo`` — keyless DuckDuckGo HTML endpoint.

``auto`` tries gemini (when a key exists) then duckduckgo. Every attempt is
recorded so failures are visible. If no backend returns a single source,
:class:`WebSearchError` is raised — sources are never invented.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from darsamooz.config import provider_api_key

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
VALID_BACKENDS = ("auto", "gemini", "duckduckgo")
# Video / social pages carry no question text worth fetching.
_SKIP_FETCH_DOMAINS = (
    "aparat.com",
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "t.me",
    "telegram.me",
)
_MAX_PAGE_BYTES = 1_500_000
_MAX_EXCERPTS_PER_PAGE = 8
_MAX_EXCERPT_CHARS = 420


class WebSearchError(RuntimeError):
    """No usable web sources. ``attempts`` lists what was tried and why it failed."""

    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


class SearchRateLimited(WebSearchError):
    """The search engine answered with a captcha / rate limit; stop sending queries."""


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""
    backend: str = ""
    excerpts: list[str] = field(default_factory=list)
    fetch_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "query": self.query,
            "backend": self.backend,
            "excerpts": list(self.excerpts),
            "fetch_status": self.fetch_status,
        }


def _clean_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _ddg_real_url(href: str) -> str:
    href = _html.unescape(href or "").strip()
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in (parsed.netloc or "") and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


_DDG_RESULT = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r"(?P<rest>.*?)(?=<a[^>]+class=\"result__a\"|\Z)",
    re.DOTALL,
)
_DDG_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(?P<s>.*?)</(?:a|div|td)>', re.DOTALL)


def parse_duckduckgo_html(page: str, *, query: str = "") -> list[SearchHit]:
    hits: list[SearchHit] = []
    for m in _DDG_RESULT.finditer(page or ""):
        url = _ddg_real_url(m.group("href"))
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue  # ads
        sm = _DDG_SNIPPET.search(m.group("rest") or "")
        hits.append(
            SearchHit(
                url=url,
                title=_clean_html_text(m.group("title"))[:200],
                snippet=_clean_html_text(sm.group("s"))[:400] if sm else "",
                query=query,
                backend="duckduckgo",
            )
        )
    return hits


def search_duckduckgo(
    query: str,
    *,
    max_results: int = 8,
    client: httpx.Client | None = None,
) -> list[SearchHit]:
    own = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        res = client.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "ir-fa"},
            headers={"User-Agent": USER_AGENT},
        )
    finally:
        if own:
            client.close()
    if res.status_code >= 400:
        raise WebSearchError(f"DuckDuckGo HTTP {res.status_code}")
    hits = parse_duckduckgo_html(res.text, query=query)
    if not hits and ("anomaly" in res.text.lower() or res.status_code == 202):
        raise SearchRateLimited(
            "DuckDuckGo موقتاً درخواست‌ها را محدود کرد (captcha). چند دقیقه بعد دوباره امتحان کنید."
        )
    return hits[:max_results]


def _resolve_redirect(url: str, client: httpx.Client) -> str:
    """Gemini grounding URIs are vertexaisearch redirects; resolve to the real page."""
    if "vertexaisearch.cloud.google.com" not in url:
        return url
    try:
        res = client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=False)
        loc = res.headers.get("location")
        if loc:
            return loc
    except Exception:
        pass
    return url


GEMINI_SEARCH_PROMPT = """با جستجوی گوگل، صفحه‌هایی را پیدا کن که «تست چهارگزینه‌ای» واقعی این مبحث را دارند
(کنکور سراسری، امتحان نهایی، آزمون‌های آزمایشی قلم‌چی/گاج/ماز، سایت‌های بانک تست فارسی).
برای هر منبع یک خط بنویس: عنوان صفحه — نوع آزمون — توضیح کوتاه از سبک تست‌ها (عددی/مفهومی، شکل گزینه‌ها).
صورت سؤال‌ها را کامل کپی نکن."""


def search_gemini_grounded(
    queries: list[str],
    *,
    topic_line: str,
    model: str | None = None,
    api_key: str | None = None,
    max_results: int = 8,
    client: httpx.Client | None = None,
) -> tuple[list[SearchHit], dict[str, Any]]:
    """Gemini native generateContent + google_search tool. URLs from grounding metadata only."""
    import os

    key = api_key or provider_api_key("gemini")
    if not key:
        raise WebSearchError("GEMINI_API_KEY تنظیم نشده.")
    model_id = (
        model
        or os.environ.get("GEMINI_SEARCH_MODEL")
        or os.environ.get("GEMINI_MODEL")
        or ""
    ).strip()
    if not model_id:
        raise WebSearchError("GEMINI_MODEL / GEMINI_SEARCH_MODEL تنظیم نشده.")
    prompt = (
        f"مبحث: {topic_line}\n"
        f"عبارت‌های جستجو: {' | '.join(queries)}\n\n{GEMINI_SEARCH_PROMPT}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    own = client is None
    client = client or httpx.Client(timeout=120.0)
    try:
        res = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json=body,
        )
        if res.status_code >= 400:
            raise WebSearchError(
                f"Gemini google_search HTTP {res.status_code}: {res.text[:200]}"
            )
        data = res.json()
        try:
            cand = data["candidates"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise WebSearchError(f"پاسخ غیرمنتظره Gemini: {str(data)[:200]}") from exc
        parts = (cand.get("content") or {}).get("parts") or []
        summary = "".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        gm = cand.get("groundingMetadata") or {}
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for chunk in gm.get("groundingChunks") or []:
            web = (chunk or {}).get("web") or {}
            uri = str(web.get("uri") or "").strip()
            if not uri:
                continue
            real = _resolve_redirect(uri, client)
            if real in seen:
                continue
            seen.add(real)
            hits.append(
                SearchHit(
                    url=real,
                    title=str(web.get("title") or "")[:200],
                    snippet="",
                    query=" | ".join(gm.get("webSearchQueries") or queries)[:300],
                    backend="gemini",
                )
            )
            if len(hits) >= max_results:
                break
    finally:
        if own:
            client.close()
    meta = {
        "model": model_id,
        "web_search_queries": gm.get("webSearchQueries") or [],
        "summary": summary[:2000],
    }
    if not hits:
        raise WebSearchError("Gemini grounding هیچ منبع وبی برنگرداند.")
    return hits, meta


# ---------------------------------------------------------------- page excerpts

_OPTION_LINE = re.compile(
    r"^\s*(?:\(?[1-4۱-۴]\s*[\)\-\.:]|\(?[الفبجد]\s*\)|گزینه\s*[1-4۱-۴])"
)
_QUESTION_CUE = re.compile(r"؟|\?|کدام|چند\s|چقدر|برابر\s+است|درست\s+است|نادرست")
_NAV_WORDS = ("ورود", "ثبت نام", "سبد خرید", "فروشگاه", "صفحه اصلی", "تماس با ما", "منو")


def html_to_lines(raw_html: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw_html or "")
    text = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6]|td|span)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"[ \t\u00a0]+", " ", ln).strip()
        if ln:
            lines.append(ln)
    return lines


def extract_question_excerpts(lines: list[str], *, limit: int = _MAX_EXCERPTS_PER_PAGE) -> list[str]:
    """Heuristic: question-looking line + up to 4 following option-looking lines."""
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines) and len(out) < limit:
        ln = lines[i]
        if (
            15 <= len(ln) <= 400
            and _QUESTION_CUE.search(ln)
            and not any(w in ln for w in _NAV_WORDS)
            and re.search(r"[\u0600-\u06FF]", ln)
        ):
            block = [ln]
            j = i + 1
            while j < len(lines) and len(block) < 5:
                nxt = lines[j]
                if _OPTION_LINE.match(nxt) and len(nxt) <= 160:
                    block.append(nxt)
                    j += 1
                    continue
                break
            excerpt = " / ".join(block)[:_MAX_EXCERPT_CHARS]
            key = excerpt[:80]
            if key not in seen and (len(block) > 1 or "؟" in ln or "?" in ln):
                seen.add(key)
                out.append(excerpt)
            i = j
            continue
        i += 1
    return out


def fetch_page_excerpts(url: str, client: httpx.Client) -> tuple[list[str], str]:
    host = (urlparse(url).netloc or "").lower()
    if any(host.endswith(d) for d in _SKIP_FETCH_DOMAINS):
        return [], "skipped_video_or_social"
    if url.lower().split("?")[0].endswith(".pdf"):
        return [], "skipped_pdf"
    try:
        res = client.get(url, headers={"User-Agent": USER_AGENT})
    except Exception as exc:
        return [], f"error: {type(exc).__name__}"
    if res.status_code >= 400:
        return [], f"http_{res.status_code}"
    ctype = res.headers.get("content-type", "")
    if "html" not in ctype and "text" not in ctype:
        return [], f"skipped_content_type:{ctype[:40]}"
    body = res.content[:_MAX_PAGE_BYTES].decode(res.encoding or "utf-8", errors="replace")
    excerpts = extract_question_excerpts(html_to_lines(body))
    return excerpts, "ok" if excerpts else "ok_no_questions"


# ---------------------------------------------------------------- orchestration


def web_research(
    queries: list[str],
    *,
    topic_line: str,
    backend: str = "auto",
    max_sources: int = 8,
    fetch_pages: bool = True,
    on_log: Callable[[str], None] | None = None,
    ddg_search: Callable[..., list[SearchHit]] | None = None,
    gemini_search: Callable[..., tuple[list[SearchHit], dict[str, Any]]] | None = None,
    page_fetcher: Callable[[str, httpx.Client], tuple[list[str], str]] | None = None,
    delay_s: float = 1.5,
) -> dict[str, Any]:
    """Run real web search and fetch question excerpts. Raises WebSearchError when empty."""
    backend = (backend or "auto").strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"backend must be one of {VALID_BACKENDS}")
    queries = [q.strip() for q in queries if q and q.strip()][:4]
    if not queries:
        raise WebSearchError("هیچ عبارت جستجویی در پروفایل موضوع نیست.")
    log = on_log or (lambda _m: None)
    ddg_search = ddg_search or search_duckduckgo
    gemini_search = gemini_search or search_gemini_grounded
    page_fetcher = page_fetcher or fetch_page_excerpts

    order: list[str]
    if backend == "auto":
        order = (["gemini"] if provider_api_key("gemini") else []) + ["duckduckgo"]
    else:
        order = [backend]

    attempts: list[dict[str, Any]] = []
    hits: list[SearchHit] = []
    used_backend: str | None = None
    gemini_meta: dict[str, Any] | None = None

    for name in order:
        try:
            if name == "gemini":
                log("جستجوی وب با Gemini google_search…")
                found, gemini_meta = gemini_search(
                    queries, topic_line=topic_line, max_results=max_sources
                )
            else:
                found = []
                errors: list[str] = []
                for qi, q in enumerate(queries):
                    log(f"جستجوی DuckDuckGo: {q}")
                    try:
                        found.extend(ddg_search(q, max_results=max_sources))
                    except SearchRateLimited as exc:
                        errors.append(str(exc))
                        break  # don't hammer a rate-limited engine
                    except WebSearchError as exc:
                        errors.append(str(exc))
                    except httpx.HTTPError as exc:
                        errors.append(f"{type(exc).__name__}: {exc}")
                    if qi + 1 < len(queries) and delay_s:
                        time.sleep(delay_s)
                if not found:
                    raise WebSearchError(
                        "DuckDuckGo نتیجه‌ای نداد" + (f": {errors[0]}" if errors else ".")
                    )
            attempts.append({"backend": name, "ok": True, "hits": len(found)})
            hits = found
            used_backend = name
            break
        except (WebSearchError, httpx.HTTPError) as exc:
            attempts.append({"backend": name, "ok": False, "error": str(exc)[:300]})
            log(f"جستجو با {name} ناموفق: {str(exc)[:160]}")

    if not hits:
        raise WebSearchError(
            "جستجوی وب هیچ منبعی پیدا نکرد. "
            + "؛ ".join(f"{a['backend']}: {a.get('error')}" for a in attempts),
            attempts,
        )

    # Dedupe by URL, cap
    uniq: list[SearchHit] = []
    seen: set[str] = set()
    for h in hits:
        key = h.url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
        if len(uniq) >= max_sources:
            break

    if fetch_pages:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            for h in uniq:
                log(f"خواندن صفحه: {h.url[:90]}")
                h.excerpts, h.fetch_status = page_fetcher(h.url, client)

    return {
        "ok": True,
        "backend": used_backend,
        "attempts": attempts,
        "queries": queries,
        "gemini": gemini_meta,
        "sources": [h.to_dict() for h in uniq],
        "excerpt_count": sum(len(h.excerpts) for h in uniq),
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
