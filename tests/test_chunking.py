from darsamooz.chunking import chunk_chapter
from darsamooz.models import Chapter


def test_chunk_small_chapter_single():
    ch = Chapter(number=1, title="t", text="سلام\n\nدنیا")
    assert chunk_chapter(ch) == ["سلام\n\nدنیا"]


def test_chunk_splits_long():
    body = ("پاراگراف بلند. " * 50 + "\n\n") * 20
    ch = Chapter(number=1, title="t", text=body)
    chunks = chunk_chapter(ch, max_chars=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)  # allow slight over from overlap glue
