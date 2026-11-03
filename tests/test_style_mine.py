import pytest

from darsamooz.config import Settings
from darsamooz.generate import _build_unit_prompt
from darsamooz.models import Section
from darsamooz.style_mine import (
    distill_style_pack,
    distill_style_pack_from_topic,
    format_style_pack_for_prompt,
    load_style_pack,
    mine_style_pack,
    normalize_style_pack,
)


class FakeProvider:
    def complete_json(self, system: str, user: str) -> dict:
        return {
            "topic": "اندازه‌گیری",
            "patterns": ["صورت کوتاه با یک مفهوم"],
            "traps": ["واحد اشتباه"],
            "few_shot_shapes": [
                {
                    "stem_shape": "کدام تعریف درست است؟",
                    "option_shape": "چهار تعریف کوتاه هم‌طول",
                    "notes": "تعریفی",
                }
            ],
            "sources": [{"title": "نمونه", "url": "https://example.com"}],
        }


def test_normalize_and_format_style_pack():
    pack = normalize_style_pack(
        {
            "patterns": ["a", "b"],
            "traps": ["t"],
            "few_shot_shapes": [
                {"stem_shape": "S", "option_shape": "O", "notes": "N"}
            ],
        },
        topic="موضوع",
    )
    block = format_style_pack_for_prompt(pack)
    assert "الگوی سبک وب" in block
    assert "S" in block
    assert pack["topic"]


def test_mine_uses_provider_when_no_openrouter(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        textbooks_dir=tmp_path / "data" / "textbooks",
        uploads_dir=tmp_path / "data" / "uploads",
        extracted_dir=tmp_path / "data" / "extracted",
        exports_dir=tmp_path / "data" / "exports",
        manifests_dir=tmp_path / "data" / "manifests",
    )
    settings.extracted_dir.mkdir(parents=True)
    monkeypatch.setattr("darsamooz.style_mine.provider_api_key", lambda name: None)

    pack, path, hits = mine_style_pack(
        "اندازه‌گیری",
        book_slug="doc-demo",
        chapter=1,
        chapter_title="اندازه‌گیری",
        provider=FakeProvider(),
        settings=settings,
        force_refresh=True,
    )
    assert path.is_file()
    assert pack["few_shot_shapes"]
    assert pack.get("search_backend") == "llm_provider"
    assert hits == 0
    loaded = load_style_pack("doc-demo", 1, settings)
    assert loaded is not None


def test_mine_prefers_openrouter_web(tmp_path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        textbooks_dir=tmp_path / "data" / "textbooks",
        uploads_dir=tmp_path / "data" / "uploads",
        extracted_dir=tmp_path / "data" / "extracted",
        exports_dir=tmp_path / "data" / "exports",
        manifests_dir=tmp_path / "data" / "manifests",
    )
    settings.extracted_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "darsamooz.style_mine.provider_api_key",
        lambda name: "sk-or-test" if name == "openrouter" else None,
    )

    def fake_or(**kwargs):
        return (
            {
                "topic": "دما",
                "patterns": ["الگوی وب"],
                "traps": [],
                "few_shot_shapes": [
                    {"stem_shape": "S", "option_shape": "O", "notes": ""}
                ],
                "sources": [{"title": "site", "url": "https://ex.com"}],
                "search_backend": "openrouter_web_search",
            },
            2,
        )

    monkeypatch.setattr("darsamooz.style_mine._openrouter_web_style_json", fake_or)

    pack, path, hits = mine_style_pack(
        "دما",
        book_slug="doc-or",
        chapter=1,
        provider=FakeProvider(),
        settings=settings,
        force_refresh=True,
    )
    assert hits == 2
    assert pack["search_backend"] == "openrouter_web_search"
    assert path.is_file()


def test_build_unit_prompt_includes_style_block():
    sec = Section(
        id="u1",
        chapter=1,
        title="بخش",
        text="متن یونیت کافی برای تست. " * 10,
        order=0,
    )
    pack = {
        "topic": "X",
        "patterns": ["P1"],
        "traps": [],
        "few_shot_shapes": [
            {"stem_shape": "شکل صورت", "option_shape": "شکل گزینه", "notes": ""}
        ],
    }
    block = format_style_pack_for_prompt(pack)
    prompt = _build_unit_prompt(
        1,
        "عنوان",
        sec,
        2,
        style_block=block,
        book_label="doc-x",
    )
    assert "الگوی سبک وب" in prompt
    assert "شکل صورت" in prompt
    assert "متن یونیت" in prompt


def test_distill_helpers():
    snippets = [{"title": "t", "url": "u", "content": "c"}]
    pack = distill_style_pack(snippets, "موضوع", FakeProvider())
    assert pack["patterns"]
    pack2 = distill_style_pack_from_topic("موضوع", FakeProvider())
    assert pack2["search_backend"] == "llm_provider"
