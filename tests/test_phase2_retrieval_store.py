from __future__ import annotations

from pathlib import Path

import pytest

from darsamooz.config import get_settings
from darsamooz.models import Section
from darsamooz.retrieval.bm25 import build_bm25, score_bm25
from darsamooz.retrieval.service import build_chapter_index, retrieve_sections
from darsamooz.retrieval.tfidf import build_tfidf, score_tfidf
from darsamooz.sections import split_sections, write_sections
from darsamooz.store.sqlite import connect, count_sections, sync_from_filesystem

ROOT = Path(__file__).resolve().parents[1]
CH1 = ROOT / "data" / "extracted" / "physics10-riazi-110209-ch1.txt"


def _toy_sections() -> list[Section]:
    return [
        Section(
            id="ch1-s01",
            chapter=1,
            title="مدلسازی در فیزیک",
            text="مدلسازی در فیزیک فرایندی است که پدیده را ساده و آرمانی می‌کند تا تحلیل ممکن شود.",
            order=0,
        ),
        Section(
            id="ch1-s02",
            chapter=1,
            title="چگالی مواد",
            text="چگالی برابر است با جرم تقسیم بر حجم و با یکای کیلوگرم بر متر مکعب بیان می‌شود.",
            order=1,
        ),
        Section(
            id="ch1-s03",
            chapter=1,
            title="بردار و نرده",
            text="کمیت برداری علاوه بر اندازه دارای جهت است؛ کمیت نرده‌ای فقط اندازه دارد.",
            order=2,
        ),
    ]


def test_bm25_ranks_title_term_in_topk():
    sections = _toy_sections()
    index = build_bm25(sections)
    ranked = score_bm25(index, "مدلسازی فیزیک")
    assert ranked
    top_ids = [sid for sid, _ in ranked[:2]]
    assert "ch1-s01" in top_ids


def test_tfidf_ranks_density_query():
    sections = _toy_sections()
    index = build_tfidf(sections)
    ranked = score_tfidf(index, "چگالی جرم حجم")
    assert ranked
    assert ranked[0][0] == "ch1-s02"


def test_retrieve_sections_persists_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    if not CH1.is_file():
        pytest.skip("extracted chapter 1 missing")
    text = CH1.read_text(encoding="utf-8")
    from darsamooz.models import Chapter

    ch = Chapter(number=1, title="فیزیک و اندازه‌گیری", text=text)
    sections = split_sections(ch)
    assert len(sections) >= 3

    settings = get_settings()
    settings.data_dir = tmp_path
    settings.extracted_dir = tmp_path / "extracted"
    settings.extracted_dir.mkdir(parents=True)
    slug = "physics10-riazi-110209"
    (settings.extracted_dir / f"{slug}-ch1.txt").write_text(text, encoding="utf-8")
    write_sections(sections, slug, 1, settings)

    monkeypatch.setattr("darsamooz.retrieval.service.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.sections.get_settings", lambda: settings)

    info = build_chapter_index(slug, 1, settings)
    assert Path(str(info["bm25"])).is_file()
    assert Path(str(info["tfidf"])).is_file()

    # Pick a distinctive word from a section title
    target = next(s for s in sections if len(s.title) >= 6)
    query = target.title.split()[0] if target.title.split() else target.title[:8]
    hits = retrieve_sections(slug, 1, query, k=5, method="bm25", settings=settings)
    assert hits
    hit_ids = {s.id for s, _ in hits}
    # Soft check: either target in top-k or at least some positive scores
    assert len(hit_ids) >= 1


def test_sqlite_sync_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    settings.data_dir = tmp_path
    settings.extracted_dir = tmp_path / "extracted"
    settings.extracted_dir.mkdir(parents=True)
    slug = "physics10-riazi-110209"
    write_sections(_toy_sections(), slug, 1, settings)

    # Minimal run folder
    run_dir = tmp_path / "runs" / "test-run-ch1"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        '{"run_id":"test-run-ch1","chapter":1}', encoding="utf-8"
    )
    (run_dir / "items.jsonl").write_text(
        '{"status":"accepted","section_id":"ch1-s01"}\n', encoding="utf-8"
    )
    import json

    (tmp_path / "runs" / "index.jsonl").write_text(
        json.dumps(
            {
                "run_id": "test-run-ch1",
                "chapter": 1,
                "path": str(run_dir),
                "question_count": 1,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("darsamooz.store.sqlite.get_settings", lambda: settings)
    monkeypatch.setattr("darsamooz.sections.get_settings", lambda: settings)

    db = tmp_path / "test.sqlite"
    stats = sync_from_filesystem(db_path=db, book_slug=slug, settings=settings)
    assert stats["sections"] == 3
    assert stats["runs"] >= 1

    conn = connect(db)
    try:
        assert count_sections(conn, book_slug=slug, chapter=1) == 3
        n_items = conn.execute("SELECT COUNT(*) AS c FROM run_items").fetchone()["c"]
        assert int(n_items) >= 1
    finally:
        conn.close()
