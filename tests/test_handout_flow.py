"""Handout flow: topic profile → real web search → pattern pack → grounded generation.

All LLM and network calls are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from darsamooz.config import Settings
from darsamooz.generate import generate_from_chapter_with_run
from darsamooz.ingest import write_book_meta
from darsamooz.models import Chapter, Section
from darsamooz.patterns import (
    is_web_near_duplicate,
    load_pattern_pack,
    load_web_sources,
    mine_web_patterns,
    normalize_pattern_pack,
    update_pattern_pack,
    web_question_texts,
)
from darsamooz.sections import write_sections
from darsamooz.topic import analyze_topic, load_topic_profile, update_topic_profile
from darsamooz.web_search import (
    SearchHit,
    SearchRateLimited,
    WebSearchError,
    extract_question_excerpts,
    html_to_lines,
    parse_duckduckgo_html,
    web_research,
)

SLUG = "doc-jozve-test-1234abcd"
UNIT_TEXT = (
    "چگالی یک ماده برابر با جرم واحد حجم آن ماده است و یکای آن در SI کیلوگرم بر متر مکعب است. "
    "برای اندازه‌گیری چگالی یک جسم جامد، جرم آن را با ترازو و حجم آن را با استوانه مدرج اندازه می‌گیریم. "
    "چگالی آب تقریباً هزار کیلوگرم بر متر مکعب است و چگالی جیوه حدود سیزده هزار و ششصد کیلوگرم بر متر مکعب است."
)
WEB_EXCERPT = (
    "اگر جرم یک قطعه فلز ۵۴۰ گرم و حجم آن ۲۰۰ سانتی‌متر مکعب باشد چگالی آن چند گرم بر سانتی‌متر مکعب است؟"
    " / 1) 2.7 / 2) 3.7 / 3) 0.37 / 4) 27"
)


def _settings(tmp_path: Path) -> Settings:
    s = Settings(
        data_dir=tmp_path / "data",
        textbooks_dir=tmp_path / "data" / "textbooks",
        uploads_dir=tmp_path / "data" / "uploads",
        extracted_dir=tmp_path / "data" / "extracted",
        exports_dir=tmp_path / "data" / "exports",
        manifests_dir=tmp_path / "data" / "manifests",
    )
    s.extracted_dir.mkdir(parents=True, exist_ok=True)
    return s


def _seed_source(settings: Settings) -> None:
    sections = [
        Section(id="ch1-s01", chapter=1, title="چگالی", text=UNIT_TEXT, order=0),
    ]
    write_sections(sections, SLUG, 1, settings)
    (settings.extracted_dir / f"{SLUG}-ch1.txt").write_text(UNIT_TEXT, encoding="utf-8")
    write_book_meta(
        SLUG,
        title="جزوه چگالی",
        chapters=[Chapter(number=1, title="چگالی", text=UNIT_TEXT)],
        source_filename="jozve.pdf",
        settings=settings,
    )


TOPIC_JSON = {
    "subject": "فیزیک",
    "grade": "دهم",
    "track": "ریاضی و فیزیک",
    "book": "فیزیک (۱)",
    "chapter": "ویژگی‌های فیزیکی مواد",
    "topics": ["چگالی"],
    "key_concepts": ["چگالی", "جرم", "حجم"],
    "exam_targets": ["کنکور سراسری"],
    "search_queries": ["تست چگالی فیزیک دهم", "تست کنکور چگالی"],
    "summary_fa": "جزوه درباره چگالی",
    "confidence": 0.9,
}

PATTERN_JSON = {
    "exam_styles": ["کنکور سراسری"],
    "patterns": [
        {
            "name_fa": "محاسبه چگالی با تبدیل یکا",
            "question_type": "halli",
            "stem_template": "اگر جرم [جسم] برابر [عدد+یکا] و حجم آن [عدد+یکا] باشد، چگالی چند [یکا] است؟",
            "option_format": "چهار عدد با یکای یکسان",
            "distractor_strategies": ["خطای تبدیل یکا", "جابجایی صورت و مخرج"],
            "numeric": True,
            "difficulty": "medium",
            "traps": ["گرم و کیلوگرم"],
            "evidence": [1, 9],
        },
        {
            "name_fa": "مقایسه مفهومی",
            "question_type": "mafhumi",
            "stem_template": "کدام گزاره درباره [کمیت] درست است؟",
            "option_format": "چهار گزاره کوتاه",
            "distractor_strategies": ["تعمیم نادرست"],
            "numeric": False,
            "difficulty": "easy",
            "traps": [],
            "evidence": [],
        },
    ],
    "general_traps": ["یکا"],
}


class ScriptedProvider:
    """Returns topic / pattern / generation JSON based on the system prompt."""

    def __init__(self, questions: list[dict] | None = None):
        self.questions = questions or []
        self.calls: list[str] = []
        self.last_call = {"provider": "fake", "model": "fake-1"}
        self.last_usage = None
        self.call_history: list[dict] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls.append(user)
        if "classify Iranian school study material" in system:
            return dict(TOPIC_JSON)
        if "extract reusable *patterns*" in system:
            return json.loads(json.dumps(PATTERN_JSON))
        return {"questions": self.questions}


def _fake_research(queries, **kwargs):
    return {
        "ok": True,
        "backend": "duckduckgo",
        "attempts": [{"backend": "duckduckgo", "ok": True, "hits": 1}],
        "queries": queries,
        "gemini": None,
        "sources": [
            {
                "url": "https://example.ir/physics10-tests",
                "title": "تست چگالی",
                "snippet": "بانک تست فیزیک دهم",
                "query": queries[0],
                "backend": "duckduckgo",
                "excerpts": [WEB_EXCERPT],
                "fetch_status": "ok",
            }
        ],
        "excerpt_count": 1,
    }


# ------------------------------------------------------------------ web_search


DDG_HTML = """
<div class="result"><a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgama.ir%2Fexams%2F1&amp;rut=x">گاما | آزمون <b>فیزیک</b> دهم</a>
<a class="result__snippet" href="#">تست‌های <b>فصل اول</b> با پاسخ</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad_provider=x">Ad</a></div>
<div class="result"><a class="result__a" href="https://www.aligebra.com/tests/ch1/">علی جبرا</a>
<div class="result__snippet">کنکور ۹۸ تا ۱۴۰۴</div></div>
"""


def test_parse_duckduckgo_html_unwraps_redirects_and_skips_ads():
    hits = parse_duckduckgo_html(DDG_HTML, query="q")
    urls = [h.url for h in hits]
    assert urls == ["https://gama.ir/exams/1", "https://www.aligebra.com/tests/ch1/"]
    assert "فیزیک" in hits[0].title
    assert "فصل اول" in hits[0].snippet
    assert hits[1].snippet == "کنکور ۹۸ تا ۱۴۰۴"


def test_extract_question_excerpts_finds_stem_and_options():
    page = """<html><body><nav>صفحه اصلی</nav>
    <p>1- چگالی کدام ماده از همه بیشتر است؟</p>
    <p>1) آب</p><p>2) جیوه</p><p>3) هوا</p><p>4) روغن</p>
    <script>var x = 'کدام؟';</script>
    <p>متن عادی بدون سؤال</p></body></html>"""
    ex = extract_question_excerpts(html_to_lines(page))
    assert len(ex) == 1
    assert "جیوه" in ex[0] and "کدام" in ex[0]


def test_web_research_falls_back_gemini_to_duckduckgo(monkeypatch):
    monkeypatch.setattr("darsamooz.web_search.provider_api_key", lambda name: "k")

    def gemini_fail(queries, **kw):
        raise WebSearchError("Gemini google_search HTTP 429: quota")

    def ddg(q, max_results=8):
        return [SearchHit(url="https://a.ir/t", title="A", snippet="s", query=q, backend="duckduckgo")]

    res = web_research(
        ["تست چگالی", "تست کنکور چگالی"],
        topic_line="فیزیک دهم",
        gemini_search=gemini_fail,
        ddg_search=ddg,
        page_fetcher=lambda url, client: ([WEB_EXCERPT], "ok"),
        delay_s=0,
    )
    assert res["backend"] == "duckduckgo"
    assert res["attempts"][0] == {
        "backend": "gemini",
        "ok": False,
        "error": "Gemini google_search HTTP 429: quota",
    }
    assert res["attempts"][1]["ok"] is True
    assert len(res["sources"]) == 1  # deduped across queries
    assert res["excerpt_count"] == 1


def test_web_research_raises_when_nothing_found(monkeypatch):
    monkeypatch.setattr("darsamooz.web_search.provider_api_key", lambda name: None)

    def ddg_empty(q, max_results=8):
        raise WebSearchError("DuckDuckGo HTTP 503")

    with pytest.raises(WebSearchError) as ei:
        web_research(["q"], topic_line="t", ddg_search=ddg_empty, delay_s=0)
    assert ei.value.attempts and ei.value.attempts[0]["ok"] is False


# ------------------------------------------------------------------ topic + patterns


def test_analyze_topic_saves_profile_and_edit_confirms(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    prov = ScriptedProvider()
    profile = analyze_topic(SLUG, 1, provider=prov, settings=settings)
    assert profile["subject"] == "فیزیک"
    assert profile["confirmed"] is False
    assert "چگالی" in prov.calls[0]  # handout text was sent
    edited = update_topic_profile(
        SLUG, 1, {"grade": "یازدهم", "search_queries": ["تست جدید"]}, settings=settings
    )
    assert edited["confirmed"] is True and edited["grade"] == "یازدهم"
    assert load_topic_profile(SLUG, 1, settings)["search_queries"] == ["تست جدید"]


def test_mine_web_patterns_success_maps_evidence(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    prov = ScriptedProvider()
    analyze_topic(SLUG, 1, provider=prov, settings=settings)
    pack = mine_web_patterns(SLUG, 1, provider=prov, settings=settings, research_fn=_fake_research)
    assert pack["status"] == "web"
    assert pack["sources_count"] == 1
    assert [p["id"] for p in pack["patterns"]] == ["p1", "p2"]
    # evidence [1, 9] → only the valid index survives as a URL
    assert pack["patterns"][0]["evidence_urls"] == ["https://example.ir/physics10-tests"]
    assert load_pattern_pack(SLUG, 1, settings)["status"] == "web"
    assert load_web_sources(SLUG, 1, settings)["sources"][0]["excerpts"] == [WEB_EXCERPT]
    # the web excerpt was given to the pattern extractor as evidence
    assert "۵۴۰ گرم" in prov.calls[-1]


def test_mine_web_patterns_failure_is_visible_then_llm_only(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    prov = ScriptedProvider()
    analyze_topic(SLUG, 1, provider=prov, settings=settings)

    def failing(queries, **kw):
        raise WebSearchError("no results", [{"backend": "duckduckgo", "ok": False, "error": "503"}])

    with pytest.raises(WebSearchError):
        mine_web_patterns(SLUG, 1, provider=prov, settings=settings, research_fn=failing)
    ws = load_web_sources(SLUG, 1, settings)
    assert ws["ok"] is False and ws["error"] == "no results" and ws["sources"] == []
    assert load_pattern_pack(SLUG, 1, settings) is None

    pack = mine_web_patterns(SLUG, 1, provider=prov, settings=settings, llm_only=True)
    assert pack["status"] == "llm_only"
    assert pack["sources"] == [] and pack["sources_count"] == 0
    assert all(p["evidence_urls"] == [] for p in pack["patterns"])
    assert pack["note"]


def test_update_pattern_pack_keeps_evidence_and_enabled(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    prov = ScriptedProvider()
    analyze_topic(SLUG, 1, provider=prov, settings=settings)
    pack = mine_web_patterns(SLUG, 1, provider=prov, settings=settings, research_fn=_fake_research)
    pack["patterns"][1]["enabled"] = False
    pack["patterns"][0]["stem_template"] = "اسکلت ویرایش‌شده [کمیت]؟"
    saved = update_pattern_pack(SLUG, 1, pack, settings=settings)
    assert saved["patterns"][0]["stem_template"] == "اسکلت ویرایش‌شده [کمیت]؟"
    assert saved["patterns"][0]["evidence_urls"] == ["https://example.ir/physics10-tests"]
    assert saved["patterns"][1]["enabled"] is False
    assert saved["status"] == "web" and saved.get("edited_at")


def test_normalize_pattern_pack_fixes_bad_types():
    pack = normalize_pattern_pack(
        {"patterns": [{"name_fa": "x", "question_type": "weird", "numeric": "true", "difficulty": "??"}]}
    )
    p = pack["patterns"][0]
    assert p["question_type"] == "halli" and p["numeric"] is True and p["difficulty"] == "medium"


def test_web_near_duplicate_check():
    texts = web_question_texts(_fake_research(["q"]))
    dup, score, _ = is_web_near_duplicate(
        "اگر جرم یک قطعه فلز ۵۴۰ گرم و حجم آن ۲۰۰ سانتی متر مکعب باشد، چگالی آن چند گرم بر سانتی‌متر مکعب است؟",
        texts,
    )
    assert dup and score >= 0.6
    dup2, score2, _ = is_web_near_duplicate(
        "طبق متن، یکای چگالی در دستگاه SI کدام است؟", texts
    )
    assert not dup2 and score2 < 0.6


# ------------------------------------------------------------------ generation


def _q(stem: str, quote: str) -> dict:
    return {
        "stem": stem,
        "options": ["کیلوگرم بر متر مکعب", "گرم", "متر مکعب", "نیوتن"],
        "correct_index": 0,
        "explanation": "طبق متن جزوه یکای چگالی کیلوگرم بر متر مکعب است.",
        "difficulty": "easy",
        "pattern": "mafhumi",
        "source_refs": [quote],
    }


def test_generate_with_pattern_pack_rejects_web_copies(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    copied = _q(
        "اگر جرم یک قطعه فلز ۵۴۰ گرم و حجم آن ۲۰۰ سانتی‌متر مکعب باشد چگالی آن چند گرم بر سانتی‌متر مکعب است؟",
        "یکای آن در SI کیلوگرم بر متر مکعب است",
    )
    good = _q(
        "بر اساس متن جزوه، یکای چگالی در دستگاه SI کدام است؟",
        "یکای آن در SI کیلوگرم بر متر مکعب است",
    )
    prov = ScriptedProvider(questions=[copied, good])
    analyze_topic(SLUG, 1, provider=prov, settings=settings)
    mine_web_patterns(SLUG, 1, provider=prov, settings=settings, research_fn=_fake_research)

    result = generate_from_chapter_with_run(
        1,
        count=1,
        provider=prov,
        settings=settings,
        book_slug=SLUG,
        verify=False,
        pattern_pack="auto",
    )
    assert len(result.questions) == 1
    q = result.questions[0]
    assert "web_pattern:p1" in q.tags
    m = result.metrics
    assert m["pattern_pack_used"] is True
    assert m["pattern_pack_status"] == "web"
    assert m["web_dup_rejected"] == 1
    assert m["web_sources_count"] == 1
    assert m["patterns_count"] == 2
    # the per-round prompt carried the abstract pattern, not the web question
    gen_prompt = prov.calls[-1]
    assert "الگوی هدف (p1)" in gen_prompt
    assert "۵۴۰" not in gen_prompt
    root = result.run.root
    for name in ("pattern_pack.json", "web_sources.json", "topic_profile.json"):
        assert (root / name).is_file(), name
    items = [json.loads(x) for x in (root / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(i.get("reason") == "web_near_duplicate" for i in items)


def test_generate_pattern_pack_missing_is_clear_error(tmp_path):
    settings = _settings(tmp_path)
    _seed_source(settings)
    with pytest.raises(FileNotFoundError, match="بسته الگو"):
        generate_from_chapter_with_run(
            1, count=1, provider=ScriptedProvider(), settings=settings,
            book_slug=SLUG, verify=False, pattern_pack="auto",
        )


# ------------------------------------------------------------------ web API + CLI


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from darsamooz import config
    from darsamooz.web.app import create_app

    settings_dirs = _settings(tmp_path)
    _seed_source(settings_dirs)
    original = config.get_settings

    def patched():
        s = original()
        for attr in ("data_dir", "uploads_dir", "extracted_dir", "exports_dir", "manifests_dir", "textbooks_dir"):
            setattr(s, attr, getattr(settings_dirs, attr))
        return s

    for target in ("darsamooz.config.get_settings", "darsamooz.web.app.get_settings", "darsamooz.web.jobs.get_settings"):
        monkeypatch.setattr(target, patched)
    prov = ScriptedProvider()
    monkeypatch.setattr("darsamooz.llm.get_provider", lambda *a, **k: prov)
    with TestClient(create_app()) as c:
        yield c, monkeypatch


def test_api_handout_flow(api_client):
    c, monkeypatch = api_client
    base = f"/api/handout/{SLUG}/1"
    assert c.get(base).json() == {"topic_profile": None, "web_sources": None, "pattern_pack": None}

    r = c.post(f"{base}/analyze", json={})
    assert r.status_code == 200, r.text
    assert r.json()["topic_profile"]["subject"] == "فیزیک"

    r = c.put(f"{base}/topic", json={"chapter": "چگالی مواد", "confirm": True})
    assert r.json()["topic_profile"]["confirmed"] is True

    # search failure → 200 with search_failed + continue option, no fake sources
    def failing(queries, **kw):
        raise WebSearchError("DuckDuckGo نتیجه‌ای نداد", [{"backend": "duckduckgo", "ok": False, "error": "x"}])

    monkeypatch.setattr("darsamooz.web_search.web_research", failing)
    r = c.post(f"{base}/mine", json={"backend": "duckduckgo"})
    body = r.json()
    assert r.status_code == 200 and body["ok"] is False and body["search_failed"] is True
    assert body["can_continue_llm_only"] is True
    assert c.get(base).json()["web_sources"]["ok"] is False

    monkeypatch.setattr("darsamooz.web_search.web_research", _fake_research)
    r = c.post(f"{base}/mine", json={"backend": "auto"})
    body = r.json()
    assert body["ok"] is True and body["pattern_pack"]["status"] == "web"
    assert body["web_sources"]["sources"][0]["url"] == "https://example.ir/physics10-tests"

    pack = body["pattern_pack"]
    pack["patterns"][0]["name_fa"] = "ویرایش شد"
    r = c.put(f"{base}/patterns", json=pack)
    assert r.json()["pattern_pack"]["patterns"][0]["name_fa"] == "ویرایش شد"

    assert c.get(f"/api/handout/{SLUG}/9").status_code == 404
    assert c.get("/api/handout/bad slug!/1").status_code in (400, 404)


def test_api_generate_requires_pack(api_client):
    c, _ = api_client
    r = c.post("/api/generate", json={"chapter": 1, "count": 1, "book_slug": SLUG, "use_pattern_pack": True})
    assert r.status_code == 400
    assert "بسته الگو" in r.json()["detail"]


def test_cli_mine_web_patterns_failure_message(tmp_path, monkeypatch):
    from darsamooz import cli

    settings = _settings(tmp_path)
    _seed_source(settings)
    prov = ScriptedProvider()
    analyze_topic(SLUG, 1, provider=prov, settings=settings)
    monkeypatch.setattr("darsamooz.cli.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.config.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.llm.get_provider", lambda *a, **k: prov)

    def failing(queries, **kw):
        raise WebSearchError("DuckDuckGo HTTP 503")

    monkeypatch.setattr("darsamooz.web_search.web_research", failing)
    runner = CliRunner()
    res = runner.invoke(cli.main, ["mine-web-patterns", "--slug", SLUG, "--chapter", "1"])
    assert res.exit_code != 0
    assert "--llm-only" in res.output and "No sources were invented" in res.output

    monkeypatch.setattr("darsamooz.web_search.web_research", _fake_research)
    res = runner.invoke(cli.main, ["mine-web-patterns", "--slug", SLUG, "--chapter", "1"])
    assert res.exit_code == 0, res.output
    assert "status=web" in res.output and "https://example.ir/physics10-tests" in res.output


def test_duckduckgo_rate_limit_stops_after_first_query(monkeypatch):
    monkeypatch.setattr("darsamooz.web_search.provider_api_key", lambda name: None)
    calls = []

    def blocked(q, max_results=8):
        calls.append(q)
        raise SearchRateLimited("captcha")

    with pytest.raises(WebSearchError) as ei:
        web_research(["q1", "q2", "q3"], topic_line="t", ddg_search=blocked, delay_s=0)
    assert calls == ["q1"]
    assert "captcha" in ei.value.attempts[0]["error"]
