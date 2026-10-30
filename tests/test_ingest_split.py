from darsamooz.ingest import clean_bidi, split_chapters


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
