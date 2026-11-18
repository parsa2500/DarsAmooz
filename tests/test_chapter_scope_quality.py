"""Chapter-scope grounding (queries/patterns) and question-quality gate. All LLM/network mocked."""

from __future__ import annotations

import json
from pathlib import Path

from darsamooz.chapter_scope import (
    build_scope,
    check_query,
    clean_section_titles,
    filter_patterns_to_chapter,
    garbled_tokens,
    ground_search_queries,
    trivial_recall_reason,
)
from darsamooz.config import Settings
from darsamooz.generate import generate_from_chapter_with_run
from darsamooz.ingest import write_book_meta
from darsamooz.models import Chapter, Question, ReviewStatus, Section
from darsamooz.patterns import mine_web_patterns
from darsamooz.quality import deterministic_reject, llm_review
from darsamooz.sections import write_sections
from darsamooz.topic import analyze_topic

SLUG = "doc-measure-test-0001"
CH_TITLE = "فیزیک و اندازه‌گیری"
S1 = (
    "فیزیک از بنیادی‌ترین دانش‌هاست و آزمایش و مشاهده در فیزیک اهمیت زیادی دارد. "
    "مدل‌ها و نظریه‌های فیزیکی در طول زمان ممکن است تغییر کنند و مدل اتمی بارها اصلاح شد."
)
S2 = (
    "برای اندازه‌گیری کمیت‌های فیزیکی از یکاها استفاده می‌کنیم. یکای جرم در SI کیلوگرم و یکای طول متر است. "
    "یکی از رابطه‌های فیزیکی، قانون دوم نیوتون F = ma است که یکای نیرو را نیوتون می‌نامند. "
    "پیشوندهای یکاها مانند کیلو و میلی برای نمایش اعداد بزرگ و کوچک به کار می‌روند و تبدیل یکا با ضریب تبدیل انجام می‌شود."
)
S3 = (
    "چگالی یک ماده برابر با جرم واحد حجم آن است و یکای آن کیلوگرم بر متر مکعب است. "
    "چگالی آب حدود هزار کیلوگرم بر متر مکعب است و برای اندازه‌گیری چگالی جرم و حجم را اندازه می‌گیریم."
)
SECTIONS = [
    Section(id="ch1-s01", chapter=1, title="1ـ 1فیزیک :دانش بنیادی (بخش 1)", text=S1, order=0),
    Section(id="ch1-s04", chapter=1, title="اندازه گیری و دستگاه بینالمللی یکاها (بخش 1)", text=S2, order=1),
    Section(id="ch1-s04b", chapter=1, title="آمده است. (بخش 2)", text=S2, order=2),
    Section(id="ch1-s06", chapter=1, title="چگالی", text=S3, order=3),
]


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


def _seed(settings: Settings) -> None:
    write_sections(SECTIONS, SLUG, 1, settings)
    text = "\n".join(s.text for s in SECTIONS)
    (settings.extracted_dir / f"{SLUG}-ch1.txt").write_text(text, encoding="utf-8")
    write_book_meta(
        SLUG, title="جزوه اندازه‌گیری",
        chapters=[Chapter(number=1, title=CH_TITLE, text=text)],
        source_filename="jozve.pdf", settings=settings,
    )


def _scope():
    return build_scope(SECTIONS, chapter_num=1, chapter_title=CH_TITLE)


# ------------------------------------------------------------------ scope helpers


def test_clean_section_titles_drops_noise_and_part_suffix():
    titles = clean_section_titles(SECTIONS, CH_TITLE)
    assert titles == [CH_TITLE, "فیزیک: دانش بنیادی", "اندازه گیری و دستگاه بینالمللی یکاها", "چگالی"]


def test_check_query_drops_invented_and_off_chapter_terms():
    scope = _scope()
    assert check_query("سؤال فیزیک 10 پایه فاکتور دوم نیوتون", scope).startswith("term_not_in_book: فاکتور")
    # every word exists in the book (F=ma is mentioned as a units example) but no section is about it
    assert check_query("تست قانون دوم نیوتون", scope).startswith("off_chapter")
    assert check_query("تست فیزیک دهم چگالی", scope) is None
    assert check_query("تست تبدیل یکاها", scope) is None


def test_ground_search_queries_puts_section_queries_first_and_logs_drops():
    profile = {
        "subject": "فیزیک", "grade": "دهم",
        "search_queries": ["سؤال فیزیک 10 پایه فاکتور دوم نیوتون", "تست چگالی کنکور"],
    }
    g = ground_search_queries(profile, _scope(), max_queries=6)
    assert g["queries"][0] == f"تست فیزیک دهم فصل اول {CH_TITLE}"
    assert "تست چگالی کنکور" in g["queries"]
    assert "تست فیزیک دهم اندازه گیری و دستگاه بینالمللی یکاها" in g["queries"]
    assert all("نیوتون" not in q for q in g["queries"])
    assert g["dropped"] == [
        {"query": "سؤال فیزیک 10 پایه فاکتور دوم نیوتون", "reason": "term_not_in_book: فاکتور"}
    ]
    assert len(ground_search_queries(profile, _scope(), max_queries=2)["queries"]) == 2


def test_filter_patterns_requires_a_chapter_section():
    pats = [
        {"id": "p1", "name_fa": "محاسبه چگالی از جرم و حجم", "section": "چگالی"},
        {"id": "p2", "name_fa": "محاسبه شتاب با قانون دوم نیوتون", "section": "دینامیک و حرکت"},
        {"id": "p3", "name_fa": "تبدیل یکاها با پیشوندها", "section": "اندازه‌گیری و دستگاه بین‌المللی یکاها"},
        {"id": "p4", "name_fa": "پیشوندهای یکا", "section": ""},
    ]
    kept, dropped = filter_patterns_to_chapter(pats, _scope())
    assert [p["name_fa"] for p in kept] == ["محاسبه چگالی از جرم و حجم", "تبدیل یکاها با پیشوندها"]
    assert [p["id"] for p in kept] == ["p1", "p2"]  # renumbered
    assert kept[1]["section"] == "اندازه گیری و دستگاه بینالمللی یکاها"
    assert {d["name_fa"] for d in dropped} == {"محاسبه شتاب با قانون دوم نیوتون", "پیشوندهای یکا"}
    assert all(d["reason"].startswith("off_chapter") for d in dropped)


def test_lam_alef_pdf_artifact_still_matches_book():
    secs = [Section(id="s1", chapter=1, title="تأثیر اختالف منظر در خواندن", order=0,
                    text="خطای اختالف منظر هنگام خواندن درجه رخ می‌دهد و اطالعات دقیق الزم است.")]
    scope = build_scope(secs, chapter_num=1, chapter_title="اندازه‌گیری")
    assert check_query("تست اختلاف منظر", scope) is None
    assert scope.in_book("لازم") and scope.in_book("اطلاعات")
    # generic words are recognised in either spelling; reasons show readable words
    assert check_query("سوالات تست اختلاف منظر", scope) is None
    assert check_query("تست اختلاف منظر معادلات", scope) == "term_not_in_book: معادلات"


def test_book_snippets_rank_sentences_by_shared_words():
    from darsamooz.chapter_scope import book_snippets

    snips = book_snippets(SECTIONS, "محاسبه چگالی از جرم و حجم", k=1)
    assert snips and snips[0].startswith("چگالی یک ماده")
    assert book_snippets(SECTIONS, "مفهوم", k=2) == []


def test_trivial_recall_and_garbled_detection():
    titles = _scope().section_titles
    assert trivial_recall_reason("فیزیک به معنای چه است؟", ["دانش بنیادی", "مهندسی", "تاریخ", "هنر"], 0, titles)
    assert trivial_recall_reason("منظور از چگالی چیست؟", ["a", "b", "c", "d"], 0, titles)
    assert trivial_recall_reason(
        "بر اساس متن، چرا در فیزیک آزمایش و مشاهده اهمیت زیادی دارد و چه نقشی دارد؟",
        ["الف", "ب", "ج", "د"], 0, titles,
    ) is None
    bad = garbled_tokens(
        ["اگر طول اِسکاپی‌التور 27 کیلومتر باشد، هر دانشمند چقدر از آن می‌خورد؟"], _scope()
    )
    assert bad and "اِسکاپی" in bad[0]
    assert garbled_tokens(["چگالی آب چند کیلوگرم بر متر مکعب است؟"], _scope()) == []


# ------------------------------------------------------------------ mining with scope


PATTERNS_RAW = {
    "exam_styles": ["کنکور سراسری"],
    "patterns": [
        {"name_fa": "محاسبه چگالی از جرم و حجم", "question_type": "halli", "numeric": True,
         "stem_template": "اگر جرم [جسم] [عدد+یکا] و حجم آن [عدد+یکا] باشد، چگالی چند [یکا] است؟",
         "section": "چگالی", "evidence": [1]},
        {"name_fa": "محاسبه وزن از جرم و شتاب", "question_type": "halli", "numeric": True,
         "stem_template": "جسمی به جرم [عدد] با شتاب [عدد]؛ نیرو چند نیوتون است؟",
         "section": "اندازه گیری و دستگاه بینالمللی یکاها", "evidence": [1]},
        {"name_fa": "تبدیل یکاها با پیشوندها", "question_type": "halli", "numeric": True,
         "stem_template": "[عدد] [یکا] چند [یکای دیگر] است؟", "section": "اندازه گیری و دستگاه بینالمللی یکاها",
         "evidence": [1]},
    ],
}


class ScopeProvider:
    def __init__(self, audit: dict | None = None, review: dict | None = None, questions: list | None = None):
        self.audit = audit
        self.review = review
        self.questions = questions or []
        self.calls: list[tuple[str, str]] = []
        self.last_call = {"provider": "fake", "model": "fake-1"}
        self.last_usage = None
        self.call_history: list[dict] = []

    def complete_json(self, system: str, user: str):
        self.calls.append((system, user))
        if "classify Iranian school study material" in system:
            return {
                "subject": "فیزیک", "grade": "دهم", "chapter": CH_TITLE,
                "topics": ["چگالی", "فاکتور دوم نیوتون"],
                "key_concepts": ["چگالی"],
                "search_queries": ["سؤال فیزیک 10 پایه فاکتور دوم نیوتون", "تست چگالی کنکور"],
                "confidence": 0.8,
            }
        if "extract reusable *patterns*" in system:
            return json.loads(json.dumps(PATTERNS_RAW))
        if "audit whether exam-question patterns" in system:
            return self.audit if self.audit is not None else {"patterns": []}
        if "review ONE generated Iranian MCQ" in system:
            return self.review if self.review is not None else {
                "fluent_persian": True, "garbled_terms": [], "trivial_recall": False,
                "pattern_fits": True, "best_pattern_id": None, "reason": "ok",
            }
        return {"questions": self.questions}


def _research(seen: list):
    def fn(queries, **kw):
        seen.extend(queries)
        return {
            "ok": True, "backend": "tavily", "attempts": [], "queries": queries, "search_requests": len(queries),
            "sources": [{"url": "https://ex.ir/t", "title": "تست", "excerpts": ["چگالی کدام است؟ / 1) 2 / 2) 3"]}],
            "excerpt_count": 1,
        }
    return fn


def test_analyze_topic_grounds_queries_and_drops_invented_topics(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    prof = analyze_topic(SLUG, 1, provider=ScopeProvider(), settings=settings)
    assert all("فاکتور" not in q for q in prof["search_queries"])
    assert prof["dropped_search_queries"][0]["reason"] == "term_not_in_book: فاکتور"
    assert prof["topics"] == ["چگالی"]
    assert prof["dropped_topic_items"][0]["item"] == "فاکتور دوم نیوتون"
    assert "چگالی" in prof["chapter_sections"]


def test_mine_drops_off_chapter_queries_and_audited_patterns(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    audit = {"patterns": [
        {"id": "p1", "on_chapter": True, "section": "چگالی", "reason": "ok"},
        {"id": "p2", "on_chapter": False, "section": "", "reason": "F=ma فقط مثال یکاست"},
        {"id": "p3", "on_chapter": True, "section": "اندازه گیری و دستگاه بینالمللی یکاها", "reason": "ok"},
    ]}
    prov = ScopeProvider(audit=audit)
    profile = {"subject": "فیزیک", "grade": "دهم", "chapter": CH_TITLE,
               "search_queries": ["سؤال فیزیک 10 پایه فاکتور دوم نیوتون"]}
    seen: list[str] = []
    logs: list[str] = []
    pack = mine_web_patterns(SLUG, 1, provider=prov, settings=settings, profile=profile,
                             research_fn=_research(seen), on_log=logs.append, max_queries=4)
    assert len(seen) == 4 and all("فاکتور" not in q for q in seen)
    assert pack["dropped_queries"][0]["query"] == "سؤال فیزیک 10 پایه فاکتور دوم نیوتون"
    assert [p["name_fa"] for p in pack["patterns"]] == ["محاسبه چگالی از جرم و حجم", "تبدیل یکاها با پیشوندها"]
    assert [p["id"] for p in pack["patterns"]] == ["p1", "p2"]
    assert pack["dropped_patterns"][0]["name_fa"] == "محاسبه وزن از جرم و شتاب"
    assert pack["dropped_patterns"][0]["reason"].startswith("off_chapter (audit)")
    assert any("کوئری حذف شد" in m for m in logs) and any("الگو حذف شد" in m for m in logs)
    audit_user = next(u for s, u in prov.calls if "audit whether exam-question patterns" in s)
    assert "شواهد از متن کتاب" in audit_user and "قانون دوم نیوتون F = ma" in audit_user
    # pattern extractor got book sections, not the LLM topic list
    extract_user = next(u for s, u in prov.calls if "extract reusable *patterns*" in s)
    assert "بخش‌های این فصل در کتاب" in extract_user and "- چگالی" in extract_user


def test_mine_reports_failed_audit_instead_of_passing_silently(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    logs: list[str] = []
    pack = mine_web_patterns(SLUG, 1, provider=ScopeProvider(audit={"oops": 1}), settings=settings,
                             profile={"subject": "فیزیک", "grade": "دهم", "search_queries": []},
                             research_fn=_research([]), on_log=logs.append)
    assert pack["scope_audit"]["ok"] is False
    assert "audit_malformed" in pack["scope_audit"]["error"]
    assert any("بررسی هم‌فصل بودن الگوها انجام نشد" in m for m in logs)
    # deterministic checks still ran: «وزن/شتاب» never occur in this book text
    assert [p["name_fa"] for p in pack["patterns"]] == ["محاسبه چگالی از جرم و حجم", "تبدیل یکاها با پیشوندها"]
    assert pack["dropped_patterns"][0]["reason"].startswith("term_not_in_book")


# ------------------------------------------------------------------ quality gate


def _question(stem: str, options: list[str], correct: int = 0) -> Question:
    return Question(id="q1", chapter=1, chapter_title=CH_TITLE, stem=stem, options=options,
                    correct_index=correct, explanation="توضیح فارسی", tags=["section:ch1-s06"])


def test_deterministic_reject_codes():
    scope = _scope()
    q = _question("فیزیک به معنای چه است؟", ["دانش بنیادی", "مهندسی", "تاریخ", "هنر"])
    assert deterministic_reject(q, scope)[0] == "trivial_recall"
    q = _question("اگر طول اِسکاپی‌التور ۲۷ کیلومتر باشد طول آن چند متر است؟", ["۲۷۰۰۰", "۲۷۰", "۲۷۰۰", "۲۷"])
    assert deterministic_reject(q, scope)[0] == "garbled_text"
    q = _question("کدام گزاره درباره چگالی آب طبق متن کتاب درست است؟", ["الف", "ب", "ج", "د"])
    code, detail = deterministic_reject(q, scope, {"id": "p1", "numeric": True})
    assert code == "pattern_mismatch" and "p1" in detail
    assert deterministic_reject(q, scope, {"id": "p2", "numeric": False}) is None


def test_llm_review_retag_reject_and_unchecked():
    scope = _scope()
    pats = [{"id": "p1", "name_fa": "محاسبه چگالی", "numeric": True}, {"id": "p2", "name_fa": "مفهومی", "numeric": False}]
    q = _question("کدام گزاره درباره چگالی آب طبق متن کتاب درست است؟", ["الف", "ب", "ج", "د"])
    base = {"fluent_persian": True, "garbled_terms": [], "trivial_recall": False, "reason": "r"}

    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope,
                   provider=ScopeProvider(review={**base, "pattern_fits": False, "best_pattern_id": "p2"}))
    assert d["action"] == "retag" and d["pattern_id"] == "p2"
    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope,
                   provider=ScopeProvider(review={**base, "pattern_fits": False, "best_pattern_id": None}))
    assert (d["action"], d["code"]) == ("reject", "pattern_mismatch")
    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope,
                   provider=ScopeProvider(review={**base, "pattern_fits": True, "fluent_persian": False,
                                                  "garbled_terms": ["می‌خوردگی"]}))
    assert (d["action"], d["code"]) == ("reject", "garbled_text")
    # a reviewer flag on a word that IS in the book (pdftotext artefact) is not a reject
    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope,
                   provider=ScopeProvider(review={**base, "pattern_fits": True, "garbled_terms": ["چگالی"]}))
    assert d["action"] == "accept"
    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope,
                   provider=ScopeProvider(review={**base, "pattern_fits": True, "trivial_recall": True}))
    assert (d["action"], d["code"]) == ("reject", "trivial_recall")
    d = llm_review(q, S3, target=pats[0], patterns=pats, scope=scope, provider=ScopeProvider(review={"x": 1}))
    assert (d["action"], d["code"]) == ("unchecked", "quality_review_malformed")


def _pack_settings(tmp_path):
    settings = _settings(tmp_path)
    _seed(settings)
    audit = {"patterns": [{"id": f"p{i}", "on_chapter": True, "section": "", "reason": "ok"} for i in (1, 2, 3)]}
    mine_web_patterns(SLUG, 1, provider=ScopeProvider(audit=audit), settings=settings,
                      profile={"subject": "فیزیک", "grade": "دهم", "search_queries": []},
                      research_fn=_research([]))
    return settings


def _gq(stem, options, quote, pattern="halli"):
    return {"stem": stem, "options": options, "correct_index": 0, "difficulty": "medium", "pattern": pattern,
            "explanation": "طبق متن کتاب چگالی برابر جرم تقسیم بر حجم است.", "source_refs": [quote]}


def test_generation_rejects_trivial_and_garbled_and_marks_unchecked(tmp_path):
    settings = _pack_settings(tmp_path)
    quote = "آزمایش و مشاهده در فیزیک اهمیت زیادی دارد"
    trivial = _gq("فیزیک به معنای چه است؟", ["دانش بنیادی", "مهندسی", "تاریخ", "هنر"], quote, "tarifi")
    garbled = _gq("اگر طول اِسکاپی‌التور ۲۷ کیلومتر باشد، چند متر است؟", ["۲۷۰۰۰ متر", "۲۷۰ متر", "۲۷۰۰ متر", "۲۷ متر"], quote)
    good = _gq("اگر جرم جسمی ۲۰۰ گرم و حجم آن ۲۵ سانتی‌متر مکعب باشد، چگالی آن چند گرم بر سانتی‌متر مکعب است؟",
               ["۸", "۰٫۱۲۵", "۵", "۲۲۵"], quote)
    prov = ScopeProvider(review={"broken": True}, questions=[trivial, garbled, good])
    result = generate_from_chapter_with_run(1, count=1, provider=prov, settings=settings, book_slug=SLUG,
                                            verify=False, pattern_pack="auto")
    m = result.metrics
    assert m["quality_rejected"] == {"trivial_recall": 1, "garbled_text": 1}
    assert m["quality_unchecked"] == 1
    q = result.questions[0]
    assert "quality_unchecked" in q.tags and q.review_status == ReviewStatus.needs_review
    items = [json.loads(x) for x in (result.run.root / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    reasons = {i.get("reason") for i in items if i["status"] == "rejected"}
    assert {"trivial_recall", "garbled_text"} <= reasons
    acc = next(i for i in items if i["status"] == "accepted")
    assert acc["quality_unchecked"].startswith("quality_review_malformed")


def test_generation_retags_pattern_from_reviewer(tmp_path):
    settings = _pack_settings(tmp_path)
    quote = "آزمایش و مشاهده در فیزیک اهمیت زیادی دارد"
    good = _gq("اگر جرم جسمی ۲۰۰ گرم و حجم آن ۲۵ سانتی‌متر مکعب باشد، چگالی آن چند گرم بر سانتی‌متر مکعب است؟",
               ["۸", "۰٫۱۲۵", "۵", "۲۲۵"], quote)
    review = {"fluent_persian": True, "garbled_terms": [], "trivial_recall": False,
              "pattern_fits": False, "best_pattern_id": "p2", "reason": "قالب تبدیل یکاست"}
    prov = ScopeProvider(review=review, questions=[good])
    result = generate_from_chapter_with_run(1, count=1, provider=prov, settings=settings, book_slug=SLUG,
                                            verify=False, pattern_pack="auto")
    q = result.questions[0]
    assert "web_pattern:p2" in q.tags and "web_pattern_retagged" in q.tags
    assert result.metrics["pattern_retagged"] == 1
    assert result.metrics["pattern_usage"] == {"p2": 1}


def test_quote_match_ignores_persian_digits_punctuation_and_lam_alef():
    from darsamooz.validate import quote_in_source

    book = "دالتون 1807 ،میالدی مدل توپ بیلیارد"
    assert quote_in_source("دالتون ۱۸۰۷، میلادی مدل توپ بیلیارد", book)
    assert not quote_in_source("دالتون ۱۹۰۷، میلادی مدل توپ بیلیارد", book)


def test_pick_pattern_prefers_the_units_section():
    from darsamooz.generate import _pick_pattern_for_unit

    scope = _scope()
    pats = [
        {"id": "p1", "section": "فیزیک: دانش بنیادی"},
        {"id": "p2", "section": "چگالی"},
        {"id": "p3", "section": "چگالی"},
    ]
    density = SECTIONS[3]
    tried: dict[str, int] = {}
    assert _pick_pattern_for_unit(density, pats, scope, tried, 1)["id"] == "p2"
    assert _pick_pattern_for_unit(density, pats, scope, tried, 2)["id"] == "p3"  # p2 already tried
    assert tried == {"p2": 1, "p3": 1}
    fragment = SECTIONS[2]  # «آمده است.» — no section → round robin
    assert _pick_pattern_for_unit(fragment, pats, scope, {}, 2)["id"] == "p2"


def test_schedule_spreads_over_chapter_and_keeps_spare_units():
    from darsamooz.generate import plan_unit_schedule

    secs = [Section(id=f"s{i}", chapter=1, title=f"t{i}", text="x" * 100, order=i) for i in range(10)]
    sched = plan_unit_schedule(secs, count=4)
    ids = [s.id for s, _ in sched]
    assert ids[:2] == ["s0", "s9"]  # first visits are far apart, not just the intro
    assert sum(b for _, b in sched[:2]) == 4
    assert sorted(ids) == sorted(f"s{i}" for i in range(10))  # spare units for retries


def test_short_run_keeps_accepted_questions(tmp_path):
    import pytest

    settings = _pack_settings(tmp_path)
    quote = "آزمایش و مشاهده در فیزیک اهمیت زیادی دارد"
    good = _gq("اگر جرم جسمی ۲۰۰ گرم و حجم آن ۲۵ سانتی‌متر مکعب باشد، چگالی آن چند گرم بر سانتی‌متر مکعب است؟",
               ["۸", "۰٫۱۲۵", "۵", "۲۲۵"], quote)
    prov = ScopeProvider(questions=[good])
    with pytest.raises(RuntimeError, match="partial=") as exc:
        generate_from_chapter_with_run(1, count=3, provider=prov, settings=settings, book_slug=SLUG,
                                       verify=False, pattern_pack="auto")
    path = Path(str(exc.value).split("partial=")[1].strip())
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved) == 1 and saved[0]["options"][saved[0]["correct_index"]] == "۸"
