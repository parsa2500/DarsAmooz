from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from darsamooz.config import (
    Settings,
    get_settings,
    provider_api_key,
    provider_base_url,
    provider_model,
)
from darsamooz.llm import LLMProvider
from darsamooz.sections import sections_dir

DISTILL_SYSTEM = """You extract MCQ *style patterns* from web exam snippets for Iranian school tests.
Return ONLY JSON:
{
  "topic": "...",
  "patterns": ["short Persian notes about stem structure / option design"],
  "traps": ["common distractor styles"],
  "few_shot_shapes": [
    {"stem_shape": "abstract stem skeleton without real facts", "option_shape": "how options are built", "notes": "..."}
  ],
  "sources": [{"title": "...", "url": "..."}]
}
Rules:
- Do NOT copy full real question stems or answers from the snippets.
- Abstract shapes only (e.g. "تعریف + کدام گزینه درست است؟").
- Persian for patterns/traps/notes.
- At most 3 few_shot_shapes, 5 patterns, 5 traps.
"""

WEB_MINE_SYSTEM = """You research Iranian school MCQ (تست چهارگزینه‌ای) exam *style* for the given topic.
Prefer live web search when the tool is available; look for آزمون/تست نمونه on the same subject.
Return ONLY JSON:
{
  "topic": "...",
  "patterns": ["short Persian notes about stem structure / option design"],
  "traps": ["common distractor styles"],
  "few_shot_shapes": [
    {"stem_shape": "abstract stem skeleton without real facts", "option_shape": "how options are built", "notes": "..."}
  ],
  "sources": [{"title": "...", "url": "..."}]
}
Rules:
- Style/structure only — never copy full real stems or answer keys.
- Abstract shapes only.
- Persian for patterns/traps/notes.
- At most 3 few_shot_shapes, 5 patterns, 5 traps.
- List real source titles/URLs you used when possible.
"""


class StyleMineError(RuntimeError):
    """User-visible failure while mining web style patterns."""


def style_pack_path(
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> Path:
    return sections_dir(book_slug, chapter, settings) / "style_pack.json"


def load_style_pack(
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    path = style_pack_path(book_slug, chapter, settings)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_style_pack(
    pack: dict[str, Any],
    book_slug: str,
    chapter: int,
    settings: Settings | None = None,
) -> Path:
    path = style_pack_path(book_slug, chapter, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_search_query(topic: str, *, chapter_title: str | None = None) -> str:
    topic = (topic or "").strip() or "فیزیک"
    bits = [f"تست چهارگزینه‌ای {topic}"]
    if chapter_title and chapter_title.strip() and chapter_title.strip() != topic:
        bits.append(chapter_title.strip())
    bits.append("آزمون فیزیک دهم")
    return " ".join(bits)


def _parse_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise StyleMineError("مدل برای بسته سبک پاسخ خالی برگرداند.")
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StyleMineError(f"بسته سبک JSON نامعتبر: {text[:300]}") from exc
    if not isinstance(parsed, dict):
        raise StyleMineError("ریشه JSON بسته سبک باید object باشد.")
    return parsed


def _openrouter_web_style_json(
    *,
    topic: str,
    chapter_title: str | None,
    max_results: int,
    settings: Settings,
    model: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Mine style pack via OpenRouter + server-side web_search (same OPENROUTER_API_KEY)."""
    key = provider_api_key("openrouter")
    if not key:
        raise StyleMineError("کلید OpenRouter برای جستجوی وب موجود نیست.")
    query = build_search_query(topic, chapter_title=chapter_title)
    model_id = (model or "").strip() or provider_model("openrouter")
    base = provider_base_url("openrouter").rstrip("/")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/local/darsamooz",
        "X-Title": "DarsAmooz",
    }
    user = (
        f"موضوع: {topic}\n"
        f"عنوان فصل: {chapter_title or topic}\n"
        f"Hint جستجو: {query}\n"
        "با جستجوی وب، سبک تست‌های مشابه را پیدا کن و فقط JSON بسته سبک را برگردان."
    )
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": WEB_MINE_SYSTEM},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "tools": [
            {
                "type": "openrouter:web_search",
                "parameters": {"max_results": max(1, min(int(max_results), 8))},
            }
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            res = client.post(f"{base}/chat/completions", headers=headers, json=payload)
            if res.status_code >= 400:
                raise StyleMineError(
                    f"جستجوی وب OpenRouter ناموفق (HTTP {res.status_code}): {res.text[:240]}"
                )
            data = res.json()
    except StyleMineError:
        raise
    except Exception as exc:
        raise StyleMineError(f"ارتباط OpenRouter برای الگوبرداری وب برقرار نشد: {exc}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise StyleMineError(f"پاسخ غیرمنتظره OpenRouter: {data!r}") from exc

    raw = _parse_json_object(str(content))
    pack = normalize_style_pack(raw, topic=topic)
    # Approximate hit count from returned sources
    hits = len(pack.get("sources") or []) or 1
    pack["search_backend"] = "openrouter_web_search"
    pack["search_model"] = model_id
    return pack, hits


def distill_style_pack(
    snippets: list[dict[str, str]],
    topic: str,
    provider: LLMProvider,
) -> dict[str, Any]:
    blob_parts = []
    for i, s in enumerate(snippets, 1):
        blob_parts.append(
            f"[{i}] {s.get('title') or 'بدون عنوان'}\n"
            f"URL: {s.get('url') or '—'}\n"
            f"{s.get('content') or ''}\n"
        )
    user = (
        f"موضوع: {topic}\n\n"
        f"--- snippets ---\n" + "\n".join(blob_parts) + "\n--- end ---\n"
        "فقط JSON بسته سبک را برگردان."
    )
    raw = provider.complete_json(DISTILL_SYSTEM, user)
    return normalize_style_pack(raw, topic=topic, snippets=snippets)


def distill_style_pack_from_topic(
    topic: str,
    provider: LLMProvider,
    *,
    chapter_title: str | None = None,
) -> dict[str, Any]:
    """Fallback: style pack via the same LLM API (no separate search key)."""
    query = build_search_query(topic, chapter_title=chapter_title)
    user = (
        f"موضوع: {topic}\n"
        f"عنوان فصل: {chapter_title or topic}\n"
        f"Hint: {query}\n"
        "بر اساس الگوی رایج آزمون‌های مدرسه ایران برای این موضوع، "
        "فقط JSON بسته سبک را برگردان (بدون کپی صورت سؤال واقعی)."
    )
    raw = provider.complete_json(WEB_MINE_SYSTEM, user)
    pack = normalize_style_pack(raw, topic=topic)
    pack["search_backend"] = "llm_provider"
    return pack


def normalize_style_pack(
    raw: dict[str, Any] | None,
    *,
    topic: str,
    snippets: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    raw = raw or {}
    patterns = [str(p).strip() for p in (raw.get("patterns") or []) if str(p).strip()][:5]
    traps = [str(t).strip() for t in (raw.get("traps") or []) if str(t).strip()][:5]
    shapes: list[dict[str, str]] = []
    for item in raw.get("few_shot_shapes") or []:
        if not isinstance(item, dict):
            continue
        stem = str(item.get("stem_shape") or "").strip()
        opts = str(item.get("option_shape") or "").strip()
        notes = str(item.get("notes") or "").strip()
        if stem or opts:
            shapes.append(
                {
                    "stem_shape": stem[:400],
                    "option_shape": opts[:400],
                    "notes": notes[:300],
                }
            )
        if len(shapes) >= 3:
            break
    sources = []
    for s in raw.get("sources") or []:
        if isinstance(s, dict) and (s.get("url") or s.get("title")):
            sources.append(
                {
                    "title": str(s.get("title") or "")[:200],
                    "url": str(s.get("url") or "")[:500],
                }
            )
    if not sources and snippets:
        sources = [
            {"title": s.get("title") or "", "url": s.get("url") or ""} for s in snippets[:5]
        ]
    if not patterns and not shapes:
        patterns = [
            "صورت کوتاه با یک مفهوم مرکزی و چهار گزینه هم‌طول",
            "گزینه‌های غلط از اشتباه مفهومی رایج همان مبحث",
        ]
    return {
        "topic": str(raw.get("topic") or topic).strip() or topic,
        "patterns": patterns,
        "traps": traps,
        "few_shot_shapes": shapes,
        "sources": sources[:5],
    }


def format_style_pack_for_prompt(pack: dict[str, Any] | None) -> str:
    if not pack:
        return ""
    lines = [
        "--- الگوی سبک وب (فقط ساختار؛ محتوا و نقل‌قول فقط از یونیت) ---",
        f"موضوع الگو: {pack.get('topic') or '—'}",
    ]
    patterns = pack.get("patterns") or []
    if patterns:
        lines.append("الگوها:")
        for p in patterns:
            lines.append(f"- {p}")
    traps = pack.get("traps") or []
    if traps:
        lines.append("دام‌های رایج (سبک گزینه‌های غلط):")
        for t in traps:
            lines.append(f"- {t}")
    shapes = pack.get("few_shot_shapes") or []
    if shapes:
        lines.append("شکل‌های few-shot (بدون حقیقت واقعی):")
        for i, sh in enumerate(shapes, 1):
            lines.append(
                f"{i}) stem_shape: {sh.get('stem_shape') or '—'}\n"
                f"   option_shape: {sh.get('option_shape') or '—'}\n"
                f"   notes: {sh.get('notes') or '—'}"
            )
    lines.append("--- پایان الگوی سبک ---")
    return "\n".join(lines)


def mine_style_pack(
    topic: str,
    *,
    book_slug: str,
    chapter: int,
    chapter_title: str | None = None,
    provider: LLMProvider,
    settings: Settings | None = None,
    max_results: int = 5,
    force_refresh: bool = False,
) -> tuple[dict[str, Any], Path, int]:
    """
    Load cached pack or mine via existing LLM APIs (OpenRouter web search preferred).

    Returns (pack, path, search_hit_count).
    """
    settings = settings or get_settings()
    if not force_refresh:
        cached = load_style_pack(book_slug, chapter, settings)
        if cached:
            return cached, style_pack_path(book_slug, chapter, settings), 0

    topic_s = topic or chapter_title or "موضوع"
    query = build_search_query(topic_s, chapter_title=chapter_title)
    hits = 0
    pack: dict[str, Any]

    # Prefer OpenRouter web search — uses the same OPENROUTER_API_KEY already configured.
    if provider_api_key("openrouter"):
        try:
            pack, hits = _openrouter_web_style_json(
                topic=topic_s,
                chapter_title=chapter_title,
                max_results=max_results,
                settings=settings,
            )
        except StyleMineError:
            # Fall through to generation provider
            pack = distill_style_pack_from_topic(
                topic_s, provider, chapter_title=chapter_title
            )
            hits = 0
            pack["search_backend"] = "llm_provider_fallback"
    else:
        pack = distill_style_pack_from_topic(
            topic_s, provider, chapter_title=chapter_title
        )
        hits = 0

    pack["query"] = query
    pack["search_hits"] = hits
    # Keep legacy key for older metrics readers
    pack["tavily_hits"] = hits
    path = save_style_pack(pack, book_slug, chapter, settings)
    return pack, path, hits


_WS = re.compile(r"\s+")


def topic_from_titles(*parts: str | None) -> str:
    bits = [_WS.sub(" ", (p or "").strip()) for p in parts if (p or "").strip()]
    return " — ".join(bits[:2]) if bits else "فیزیک"
