from pathlib import Path

from darsamooz.export import (
    export_csv,
    export_json,
    export_markdown,
    load_questions_from_json,
    render_markdown,
)
from darsamooz.generate import load_sample_questions


def test_sample_loads_five_valid_questions():
    qs = load_sample_questions(Path("samples/physics10_ch1_sample.json"))
    assert len(qs) == 5
    for q in qs:
        assert len(q.options) == 4
        assert 0 <= q.correct_index <= 3
        assert q.is_sample is True
        assert q.chapter == 1


def test_export_roundtrip(tmp_path: Path):
    qs = load_sample_questions(Path("samples/physics10_ch1_sample.json"))
    j = export_json(qs, tmp_path / "out.json")
    c = export_csv(qs, tmp_path / "out.csv")
    assert j.exists() and c.exists()
    assert "option_a" in c.read_text(encoding="utf-8")


def test_export_markdown_rtl_and_details(tmp_path: Path):
    qs = load_sample_questions(Path("samples/physics10_ch1_sample.json"))
    md_path = export_markdown(qs, tmp_path / "out.md")
    text = md_path.read_text(encoding="utf-8")
    assert 'dir="rtl"' in text
    assert "lang=\"fa\"" in text or "lang='fa'" in text
    assert "**الف)**" in text and "**ب)**" in text
    assert "<details>" in text and "<summary>پاسخ و توضیح</summary>" in text
    assert "پاسخ صحیح:" in text
    assert "نقل از متن کتاب:" in text
    assert qs[0].stem in text
    # Convert path: JSON -> MD matches render
    roundtrip = load_questions_from_json(export_json(qs, tmp_path / "round.json"))
    assert render_markdown(roundtrip).startswith('<div dir="rtl"')
