from pathlib import Path

from darsamooz.export import export_csv, export_json
from darsamooz.generate import load_sample_questions
from darsamooz.validate import validate_batch


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
