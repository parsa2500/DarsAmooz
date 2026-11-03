from pathlib import Path

from darsamooz.config import Settings
from darsamooz.ingest import (
    clean_bidi,
    export_prefix_for,
    ingest_document_pdf,
    make_document_slug,
    read_book_meta,
    split_chapters,
    split_chapters_generic,
)


def test_clean_bidi_removes_marks():
    s = "فصل\u200f ۱"
    assert "\u200f" not in clean_bidi(s)


def test_split_chapters_from_synthetic():
    # Simulate post-TOC chapter markers
    parts = ["TOC فصل : 1آغاز " * 100]
    for n in range(1, 6):
        parts.append(f"\fفصل {n}\nمحتوای فصل {n} " + ("متن " * 200))
    text = "".join(parts)
    chs = split_chapters(text)
    assert len(chs) == 5
    assert all(len(c.text) > 50 for c in chs)


def test_split_chapters_generic_fallback_single():
    text = "این یک سند آزمایشی بدون نشانه فصل است. " * 40
    chs = split_chapters_generic(text, fallback_title="جزوه من")
    assert len(chs) == 1
    assert chs[0].number == 1
    assert chs[0].title == "جزوه من"
    assert "سند آزمایشی" in chs[0].text


def test_split_chapters_generic_with_markers():
    parts = ["مقدمه کوتاه\n"]
    for n in range(1, 4):
        parts.append(f"\fفصل {n}: عنوان {n}\nمتن فصل {n} " + ("پاراگراف " * 80))
    text = "".join(parts)
    chs = split_chapters_generic(text, fallback_title="X")
    assert len(chs) == 3
    assert chs[0].title.startswith("عنوان")
    assert all(len(c.text) > 40 for c in chs)


def test_make_document_slug_stable():
    a = make_document_slug("My Book (v2).pdf")
    b = make_document_slug("My Book (v2).pdf")
    assert a == b
    assert a.startswith("doc-")
    assert " " not in a


def test_export_prefix_for_uploaded():
    assert export_prefix_for("physics10-riazi-110209", 2) == "phys10-ch2"
    assert export_prefix_for("doc-notes-abcd1234", 1) == "doc-notes-abcd1234-ch1"


def test_ingest_document_pdf_with_mocked_extract(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        textbooks_dir=tmp_path / "data" / "textbooks",
        uploads_dir=tmp_path / "data" / "uploads",
        extracted_dir=tmp_path / "data" / "extracted",
        exports_dir=tmp_path / "data" / "exports",
        manifests_dir=tmp_path / "data" / "manifests",
    )
    settings.extracted_dir.mkdir(parents=True)

    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    body = "فصل ۱: اندازه‌گیری\n" + ("متن علمی " * 120) + "\nفصل ۲: نیرو\n" + ("متن علمی " * 120)

    def fake_extract(path, out_txt=None):
        if out_txt:
            Path(out_txt).parent.mkdir(parents=True, exist_ok=True)
            Path(out_txt).write_text(body, encoding="utf-8")
        return body

    monkeypatch.setattr("darsamooz.ingest.extract_pdf_text", fake_extract)

    chapters, counts = ingest_document_pdf(
        pdf,
        "doc-sample-test1234",
        title="نمونه",
        settings=settings,
    )
    assert len(chapters) == 2
    assert counts[1] >= 1
    meta = read_book_meta("doc-sample-test1234", settings)
    assert meta is not None
    assert meta["kind"] == "uploaded"
    assert len(meta["chapters"]) == 2
    assert (settings.extracted_dir / "doc-sample-test1234-ch1.txt").is_file()
