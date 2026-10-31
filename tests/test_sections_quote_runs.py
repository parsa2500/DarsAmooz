from __future__ import annotations

from pathlib import Path

import pytest

from darsamooz.config import get_settings
from darsamooz.generate import (
    generate_from_chapter_with_run,
    load_sample_questions,
    persist_sample_run,
    plan_unit_schedule,
)
from darsamooz.ingest import load_extracted_chapters
from darsamooz.llm import LLMProvider
from darsamooz.models import Chapter, Section
from darsamooz.sections import load_sections, split_sections, write_sections
from darsamooz.validate import any_quote_grounded, normalize_fa, quote_in_source


ROOT = Path(__file__).resolve().parents[1]
CH1 = ROOT / "data" / "extracted" / "physics10-riazi-110209-ch1.txt"


@pytest.fixture()
def ch1_chapter() -> Chapter:
    if not CH1.is_file():
        pytest.skip("extracted chapter 1 text not present")
    text = CH1.read_text(encoding="utf-8")
    return Chapter(number=1, title="فیزیک و اندازه‌گیری", text=text)


def test_split_sections_finds_numbered_units(ch1_chapter: Chapter):
    sections = split_sections(ch1_chapter)
    assert len(sections) >= 3
    assert all(s.id.startswith("ch1-") for s in sections)
    assert any("مدل" in s.title or "فیزیک" in s.title for s in sections)
    # Units should cover a meaningful share of the chapter
    total = sum(len(s.text) for s in sections)
    assert total > 500


def test_quote_in_source_positive_and_negative(ch1_chapter: Chapter):
    sections = split_sections(ch1_chapter)
    unit = next(s for s in sections if len(s.text) > 200)
    # Take a real substring from the unit
    snippet = unit.text.strip()[20:60].strip()
    assert len(snippet) >= 12
    assert quote_in_source(snippet, unit.text) is True
    assert quote_in_source("این نقل‌قول جعلی در کتاب نیست ۱۲۳۴۵", unit.text) is False
    assert normalize_fa("يک  كلمه") == normalize_fa("یک کلمه") or "یک" in normalize_fa(
        "يک  كلمه"
    )


def test_any_quote_grounded():
    src = "مدلسازی در فیزیک فرایندی است که طی آن یک پدیده فیزیکی ساده می‌شود."
    assert any_quote_grounded(["مدلسازی در فیزیک فرایندی است"], src)
    assert not any_quote_grounded(["نقل قول ساختگی کاملاً بی‌ربط"], src)


def test_plan_unit_schedule_round_robin():
    secs = [
        Section(id="ch1-s01", chapter=1, title="a", text="x" * 100, order=0),
        Section(id="ch1-s02", chapter=1, title="b", text="y" * 100, order=1),
        Section(id="ch1-s03", chapter=1, title="c", text="z" * 100, order=2),
    ]
    sched = plan_unit_schedule(secs, count=5)
    assert sched
    # First visits should rotate units
    ids = [s.id for s, _ in sched]
    assert ids[0] != ids[1] or len(secs) == 1
    assert sum(b for _, b in sched) >= 5


def test_persist_sample_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    settings.data_dir = tmp_path
    settings.exports_dir = tmp_path / "exports"
    monkeypatch.setattr("darsamooz.runs.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.generate.get_settings", lambda: settings)

    qs = load_sample_questions()[:2]
    paths, run, _qs = persist_sample_run(qs, chapter=1, prefix="phys10-ch1", settings=settings)
    assert run.root.is_dir()
    assert (run.root / "request.json").is_file()
    assert (run.root / "metrics.json").is_file()
    assert (run.root / "items.jsonl").is_file()
    assert paths["json"].is_file()
    assert (run.root / "export" / "phys10-ch1.json").is_file()


class _MockProvider(LLMProvider):
    def __init__(self, unit_text_by_call: list[str]):
        self._texts = unit_text_by_call
        self.calls = 0
        self.last_usage = {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def complete_json(self, system: str, user: str):
        self.calls += 1
        # Pull a short quote from the unit block in the prompt
        marker = "--- یونیت ---"
        end = "--- پایان ---"
        start = user.find(marker)
        stop = user.find(end)
        body = user[start + len(marker) : stop].strip() if start >= 0 and stop > start else ""
        quote = body.replace("\n", " ").strip()[10:50].strip() or body[:40]
        if self.calls == 1:
            # First item grounded, second with fake quote → one reject
            return {
                "questions": [
                    {
                        "stem": "بر اساس متن یونیت، کدام گزینه درست است؟ مورد یک برای تست",
                        "options": ["گزینه الف درست", "گزینه ب غلط۱", "گزینه ج غلط۲", "گزینه د غلط۳"],
                        "correct_index": 0,
                        "explanation": "چون در متن یونیت آمده است و این توضیح فارسی است.",
                        "difficulty": "easy",
                        "source_refs": [quote],
                    },
                    {
                        "stem": "سوال دوم با نقل‌قول جعلی برای رد شدن در اعتبارسنجی است؟",
                        "options": ["الف یک", "ب دو", "ج سه", "د چهار"],
                        "correct_index": 1,
                        "explanation": "توضیح فارسی برای سوال ردشده.",
                        "difficulty": "medium",
                        "source_refs": ["این جمله ابداً در متن کتاب درسی وجود ندارد زز"],
                    },
                ]
            }
        # Subsequent calls: one valid grounded item each
        return {
            "questions": [
                {
                    "stem": f"سوال معتبر شماره {self.calls} بر اساس متن یونیت کدام است؟",
                    "options": ["پاسخ صحیح این است", "غلط اول", "غلط دوم", "غلط سوم"],
                    "correct_index": 0,
                    "explanation": "بر اساس نقل مستقیم از متن یونیت این گزینه درست است.",
                    "difficulty": "medium",
                    "source_refs": [quote],
                }
            ]
        }


def test_generate_with_mock_provider_and_quote_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ch1_chapter: Chapter
):
    settings = get_settings()
    settings.data_dir = tmp_path
    settings.exports_dir = tmp_path / "exports"
    settings.extracted_dir = tmp_path / "extracted"
    settings.extracted_dir.mkdir(parents=True)
    # Mirror chapter text where loader expects it
    slug = "physics10-riazi-110209"
    (settings.extracted_dir / f"{slug}-ch1.txt").write_text(ch1_chapter.text, encoding="utf-8")
    sections = split_sections(ch1_chapter)
    write_sections(sections, slug, 1, settings)

    monkeypatch.setattr("darsamooz.runs.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.generate.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.sections.get_settings", lambda: settings)
    monkeypatch.setattr(
        "darsamooz.ingest.load_extracted_chapters",
        lambda book_slug="physics10-riazi-110209", settings=None: [ch1_chapter],
    )

    provider = _MockProvider([])
    result = generate_from_chapter_with_run(
        1, count=2, provider=provider, settings=settings, book_slug=slug
    )
    assert len(result.questions) == 2
    assert result.metrics["rejected"] >= 1
    assert result.metrics["prompt_tokens"] is not None
    assert result.run.root.is_dir()
    assert (result.run.root / "items.jsonl").is_file()
    # Loaded sections path exists
    assert load_sections(slug, 1, settings, rebuild_if_missing=False)
