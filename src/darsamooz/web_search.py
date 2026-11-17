"""Real web search for exam-test *styles* (step 2 of the handout flow).

Backends:

* ``tavily``     — Tavily Search API (TAVILY_API_KEY). Preferred when the key is set.
* ``google_cse`` — Google Programmable Search / Custom Search JSON API
                   (GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX; 100 free queries/day).
                   Closed to new customers; kept as an optional fallback.
* ``duckduckgo`` — keyless DuckDuckGo HTML endpoint (rate-limits easily).
* ``gemini``     — Gemini ``google_search`` grounding. Optional, only when chosen
                   explicitly; never part of ``auto``.

``auto`` = tavily (when TAVILY_API_KEY is set), then google_cse (when both
CSE env vars are set), then duckduckgo. Every attempt is recorded, skipped
backends and fallbacks are logged (never silent), the number of search
requests per run is capped (WEB_SEARCH_MAX_QUERIES, default 6), and if
nothing is found :class:`WebSearchError` is raised — sources are never invented.
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

from darsamooz.config import provider_api_key, tavily_api_key as settings_tavily_api_key

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
VALID_BACKENDS = ("auto", "tavily", "google_cse", "duckduckgo", "gemini")
DEFAULT_MAX_QUERIES = 6
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
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


class SearchQuotaExceeded(SearchRateLimited):
    """Daily/project quota used up (e.g. Google CSE 100 free queries/day)."""


def _env(name: str, default: str | None = None) -> str | None:
    import os

    val = os.environ.get(name)
    if val is None or not str(val).strip():
        return default
    return str(val).strip()


def google_cse_credentials() -> tuple[str | None, str | None]:
    return _env("GOOGLE_CSE_API_KEY"), _env("GOOGLE_CSE_CX")


def google_cse_configured() -> bool:
    key, cx = google_cse_credentials()
    return bool(key and cx)


def tavily_credentials() -> str | None:
    return settings_tavily_api_key() or _env("TAVILY_API_KEY")


def tavily_configured() -> bool:
    return bool(tavily_credentials())


def max_queries_per_run() -> int:
    raw = _env("WEB_SEARCH_MAX_QUERIES")
    try:
        n = int(raw) if raw else DEFAULT_MAX_QUERIES
    except ValueError:
        n = DEFAULT_MAX_QUERIES
    return max(1, min(n, 20))


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


# ---------------------------------------------------------------- Google CSE

_CSE_QUOTA_REASONS = ("dailylimitexceeded", "ratelimitexceeded", "quotaexceeded", "userratelimitexceeded")


def _cse_error(res: httpx.Response) -> WebSearchError:
    try:
        err = (res.json() or {}).get("error") or {}
    except ValueError:
        err = {}
    message = str(err.get("message") or res.text[:200])
    reasons = " ".join(
        str(e.get("reason") or "") for e in (err.get("errors") or []) if isinstance(e, dict)
    ).lower()
    status = str(err.get("status") or "").lower()
    blob = f"{reasons} {status} {message.lower()}"
    if res.status_code == 429 or any(r in blob for r in _CSE_QUOTA_REASONS) or "quota" in blob:
        return SearchQuotaExceeded(
            f"سهمیه Google Custom Search تمام شد (HTTP {res.status_code}): {message[:160]} — "
            "سهمیه رایگان ۱۰۰ جستجو در روز است؛ فردا دوباره امتحان کنید."
        )
    if res.status_code == 403 and ("accessnotconfigured" in blob or "has not been used" in blob or "disabled" in blob):
        return WebSearchError(
            f"Custom Search API در پروژه Google Cloud فعال نیست (HTTP 403): {message[:160]}"
        )
    if res.status_code in (400, 401, 403):
        return WebSearchError(
            f"Google CSE درخواست را رد کرد (HTTP {res.status_code}) — GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX را بررسی کنید: {message[:160]}"
        )
    return WebSearchError(f"Google CSE HTTP {res.status_code}: {message[:160]}")


def search_google_cse(
    query: str,
    *,
    max_results: int = 10,
    start: int = 1,
    api_key: str | None = None,
    cx: str | None = None,
    client: httpx.Client | None = None,
) -> list[SearchHit]:
    """One Custom Search JSON API request (= 1 of the 100 free daily queries)."""
    env_key, env_cx = google_cse_credentials()
    key = api_key or env_key
    engine = cx or env_cx
    if not key or not engine:
        raise WebSearchError("GOOGLE_CSE_API_KEY و GOOGLE_CSE_CX تنظیم نشده‌اند.")
    params: dict[str, Any] = {
        "key": key,
        "cx": engine,
        "q": query,
        "num": max(1, min(int(max_results), 10)),
        "start": max(1, int(start)),
        "hl": "fa",
        "safe": "active",
    }
    lr = _env("GOOGLE_CSE_LR", "lang_fa")
    if lr and lr.lower() != "none":
        params["lr"] = lr
    gl = _env("GOOGLE_CSE_GL", "ir")
    if gl and gl.lower() != "none":
        params["gl"] = gl
    own = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        res = client.get(GOOGLE_CSE_URL, params=params)
    finally:
        if own:
            client.close()
    if res.status_code >= 400:
        raise _cse_error(res)
    data = res.json() or {}
    hits: list[SearchHit] = []
    for item in data.get("items") or []:
        url = str(item.get("link") or "").strip()
        if not url.startswith("http"):
            continue
        hits.append(
            SearchHit(
                url=url,
                title=str(item.get("title") or "")[:200],
                snippet=re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()[:400],
                query=query,
                backend="google_cse",
            )
        )
    return hits


# ---------------------------------------------------------------- Tavily

def _tavily_error_message(res: httpx.Response) -> str:
    try:
        data = res.json() or {}
    except ValueError:
        data = {}
    detail = data.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("error") or detail)
    if isinstance(detail, list):
        parts = []
        for item in detail:
            if isinstance(item, dict):
                parts.append(str(item.get("msg") or item.get("error") or item))
            else:
                parts.append(str(item))
        return "; ".join(parts) or res.text[:200]
    if isinstance(detail, str) and detail.strip():
        return detail
    return str(data.get("error") or res.text[:200])


def _tavily_error(res: httpx.Response) -> WebSearchError:
    message = _tavily_error_message(res)
    if res.status_code == 401:
        return WebSearchError(
            f"Tavily کلید را رد کرد (HTTP 401) — TAVILY_API_KEY را بررسی کنید: {message[:160]}"
        )
    if res.status_code == 429:
        return SearchRateLimited(
            f"Tavily درخواست‌ها را محدود کرد (HTTP 429): {message[:160]}"
        )
    if res.status_code in (432, 433):
        return SearchQuotaExceeded(
            f"سهمیه Tavily تمام شد (HTTP {res.status_code}): {message[:160]} — "
            "سهمیه پلن را در داشبورد Tavily بررسی کنید."
        )
    if res.status_code in (400, 422):
        return WebSearchError(
            f"Tavily درخواست را رد کرد (HTTP {res.status_code}): {message[:160]}"
        )
    return WebSearchError(f"Tavily HTTP {res.status_code}: {message[:160]}")


def search_tavily(
    query: str,
    *,
    max_results: int = 8,
    include_raw_content: bool = False,
    api_key: str | None = None,
    client: httpx.Client | None = None,
) -> list[SearchHit]:
    """One Tavily Search request (POST /search, Bearer auth, no key in the body)."""
    key = api_key or tavily_credentials()
    if not key:
        raise WebSearchError("TAVILY_API_KEY تنظیم نشده.")
    body: dict[str, Any] = {
        "query": query,
        "max_results": max(1, min(int(max_results), 20)),
        "search_depth": "basic",
    }
    if include_raw_content:
        body["include_raw_content"] = True
    country = _env("TAVILY_COUNTRY", "iran")
    if country and country.lower() != "none":
        body["country"] = country.lower()
    own = client is None
    client = client or httpx.Client(timeout=30.0)
    try:
        res = client.post(
            TAVILY_SEARCH_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    finally:
        if own:
            client.close()
    if res.status_code >= 400:
        raise _tavily_error(res)
    data = res.json() or {}
    hits: list[SearchHit] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        snippet_src = item.get("content") or item.get("raw_content") or ""
        hits.append(
            SearchHit(
                url=url,
                title=str(item.get("title") or "")[:200],
                snippet=re.sub(r"\s+", " ", str(snippet_src)).strip()[:400],
                query=query,
                backend="tavily",
            )
        )
    return hits


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


def _url_key(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def web_research(
    queries: list[str],
    *,
    topic_line: str,
    backend: str = "auto",
    max_sources: int = 8,
    max_queries: int | None = None,
    fetch_pages: bool = True,
    on_log: Callable[[str], None] | None = None,
    cse_search: Callable[..., list[SearchHit]] | None = None,
    tavily_search: Callable[..., list[SearchHit]] | None = None,
    ddg_search: Callable[..., list[SearchHit]] | None = None,
    gemini_search: Callable[..., tuple[list[SearchHit], dict[str, Any]]] | None = None,
    page_fetcher: Callable[[str, httpx.Client], tuple[list[str], str]] | None = None,
    delay_s: float = 1.5,
) -> dict[str, Any]:
    """Run real web search and fetch question excerpts. Raises WebSearchError when empty.

    ``max_queries`` caps search requests for the whole run (all backends together).
    """
    backend = (backend or "auto").strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"backend must be one of {VALID_BACKENDS}")
    queries = [q.strip() for q in queries if q and q.strip()][:6]
    if not queries:
        raise WebSearchError("هیچ عبارت جستجویی در پروفایل موضوع نیست.")
    budget = max(1, int(max_queries)) if max_queries else max_queries_per_run()
    log = on_log or (lambda _m: None)
    cse_search = cse_search or search_google_cse
    tavily_search = tavily_search or search_tavily
    ddg_search = ddg_search or search_duckduckgo
    gemini_search = gemini_search or search_gemini_grounded
    page_fetcher = page_fetcher or fetch_page_excerpts

    order: list[str]
    if backend == "auto":
        order = []
        skipped: list[str] = []
        if tavily_configured():
            order.append("tavily")
        else:
            skipped.append("tavily (TAVILY_API_KEY تنظیم نشده)")
        if google_cse_configured():
            order.append("google_cse")
        else:
            skipped.append("google_cse (GOOGLE_CSE_API_KEY / GOOGLE_CSE_CX تنظیم نشده)")
        order.append("duckduckgo")
        plan = " → ".join(order)
        skip_txt = ("؛ رد شد: " + "؛ ".join(skipped)) if skipped else ""
        log(f"ترتیب جستجوی auto: {plan}{skip_txt}")
    else:
        order = [backend]
        log(f"جستجوی وب با روش انتخاب‌شده: {backend}")

    attempts: list[dict[str, Any]] = []
    hits: list[SearchHit] = []
    used_backend: str | None = None
    gemini_meta: dict[str, Any] | None = None
    requests_used = 0

    for name in order:
        if requests_used >= budget:
            attempts.append(
                {"backend": name, "ok": False, "requests": 0, "error": f"سقف {budget} جستجو در این اجرا پر شد."}
            )
            continue
        found: list[SearchHit] = []
        errors: list[str] = []
        sent = 0
        try:
            if name == "gemini":
                log("جستجوی وب با Gemini google_search…")
                sent = 1
                found, gemini_meta = gemini_search(
                    queries, topic_line=topic_line, max_results=max_sources
                )
            else:
                # (query, start) plan: every query once, then page 2 of the first ones (CSE only).
                plan: list[tuple[str, int]] = [(q, 1) for q in queries]
                if name == "google_cse":
                    plan += [(q, 11) for q in queries]
                label = {"google_cse": "Google CSE", "tavily": "Tavily", "duckduckgo": "DuckDuckGo"}[name]
                seen_keys: set[str] = set()
                for q, start in plan:
                    if requests_used + sent >= budget:
                        log(f"سقف {budget} جستجو در این اجرا رسید.")
                        break
                    if len(seen_keys) >= max_sources:
                        break
                    if sent and delay_s and name == "duckduckgo":
                        time.sleep(delay_s)
                    log(f"جستجوی {label}: {q}" + (f" (صفحه {start // 10 + 1})" if start > 1 else ""))
                    sent += 1
                    try:
                        if name == "google_cse":
                            batch = cse_search(q, max_results=10, start=start)
                        elif name == "tavily":
                            batch = tavily_search(q, max_results=max_sources)
                        else:
                            batch = ddg_search(q, max_results=max_sources)
                    except SearchRateLimited as exc:
                        errors.append(str(exc))
                        break  # quota / captcha: stop sending requests to this engine
                    except WebSearchError as exc:
                        errors.append(str(exc))
                        if name in ("google_cse", "tavily"):
                            break  # config / auth errors repeat for every query
                        continue
                    except httpx.HTTPError as exc:
                        errors.append(f"{type(exc).__name__}: {exc}")
                        continue
                    for h in batch:
                        k = _url_key(h.url)
                        if k not in seen_keys:
                            seen_keys.add(k)
                            found.append(h)
                if not found:
                    raise WebSearchError(
                        f"{label} نتیجه‌ای نداد" + (f": {errors[0]}" if errors else ".")
                    )
            requests_used += sent
            attempt = {"backend": name, "ok": True, "hits": len(found), "requests": sent}
            if errors:
                attempt["warnings"] = [e[:200] for e in errors]
            attempts.append(attempt)
            hits = found
            used_backend = name
            log(f"جستجو با {name} موفق شد: {len(found)} منبع.")
            break
        except (WebSearchError, httpx.HTTPError) as exc:
            requests_used += sent
            attempts.append({"backend": name, "ok": False, "requests": sent, "error": str(exc)[:300]})
            nxt = None
            try:
                nxt = order[order.index(name) + 1]
            except (ValueError, IndexError):
                nxt = None
            if nxt:
                log(
                    f"جستجو با {name} ناموفق: {str(exc)[:160]} — "
                    f"سراغ {nxt} می‌رویم (سوییچ بی‌صدا نیست)."
                )
            else:
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
        key = _url_key(h.url)
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
        "max_queries": budget,
        "search_requests": requests_used,
        "gemini": gemini_meta,
        "sources": [h.to_dict() for h in uniq],
        "excerpt_count": sum(len(h.excerpts) for h in uniq),
    }


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)
