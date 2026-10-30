from __future__ import annotations

import json
from pathlib import Path

from darsamooz.chunking import chunk_chapter
from darsamooz.config import Settings, get_settings
from darsamooz.ingest import PHYSICS10_CHAPTERS, load_extracted_chapters
from darsamooz.llm import LLMProvider, get_provider
from darsamooz.models import Question
from darsamooz.validate import validate_batch

SYSTEM_PROMPT = """تو طراح تست فیزیک پایه دهم رشته ریاضی هستی.
فقط بر اساس «متن منبع» زیر سؤال چهارگزینه‌ای استاندارد بساز.
خروجی فقط JSON معتبر با این شکل:
{
  "questions": [
    {
      "stem": "متن سؤال به فارسی",
      "options": ["گزینه ۱", "گزینه ۲", "گزینه ۳", "گزینه ۴"],
      "correct_index": 0,
      "explanation": "توضیح کوتاه درست به فارسی",
      "difficulty": "easy|medium|hard",
      "section": "نام زیربخش در صورت وجود",
      "tags": ["برچسب"]
    }
  ]
}
قوانین سخت:
- دقیقاً ۴ گزینه؛ گزینه‌ها یکتا؛ فقط یک پاسخ کاملاً درست
- correct_index حتماً ۰ تا ۳ و اشاره به همان گزینه درست باشد
- گزینه‌های گمراه‌کننده معقول ولی نادرست باشند (نه غلط املایی واضح)
- از «همه موارد» / «هیچکدام» پرهیز کن مگر متن صریحاً چنین باشد
- مفاهیم، فرمول‌ها، یکاها و اعداد را مطابق متن نگه دار؛ چیزی از کتاب‌های دیگر قاطی نکن
- سؤال و توضیح را فارسی روان و بدون لاتین بی‌مورد بنویس (فرمول/یکا مثل m، kg، SI مجاز است)
- هر سؤال باید مستقل و قابل فهم بدون دیدن متن کتاب باشد
"""


def _chapter_title(num: int) -> str:
    for n, t in PHYSICS10_CHAPTERS:
        if n == num:
            return t
    return f"فصل {num}"


def generate_from_chapter(
    chapter_num: int,
    count: int = 10,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
    book_slug: str = "physics10-riazi-110209",
) -> list[Question]:
    settings = settings or get_settings()
    chapters = {c.number: c for c in load_extracted_chapters(book_slug, settings)}
    if chapter_num not in chapters:
        raise FileNotFoundError(
            f"Chapter {chapter_num} not found. Run `darsamooz ingest` first."
        )
    chapter = chapters[chapter_num]
    chunks = chunk_chapter(chapter)
    if not chunks:
        raise ValueError(f"Chapter {chapter_num} has empty text")

    provider = provider or get_provider(settings)
    remaining = count
    collected: list[dict] = []
    # Round-robin over chunks until we have enough
    i = 0
    while remaining > 0 and i < max(len(chunks) * 3, 3):
        chunk = chunks[i % len(chunks)]
        batch = min(5, remaining)
        user = (
            f"فصل {chapter.number}: {chapter.title}\n"
            f"تعداد سؤال درخواستی: {batch}\n\n"
            f"متن منبع:\n{chunk[:6000]}"
        )
        data = provider.complete_json(SYSTEM_PROMPT, user)
        items = data.get("questions", data if isinstance(data, list) else [])
        if not isinstance(items, list):
            items = []
        collected.extend(items)
        remaining = count - len(collected)
        i += 1

    title = chapter.title or _chapter_title(chapter_num)
    prefix = f"phys10-ch{chapter_num}"
    questions = validate_batch(
        collected[:count], chapter=chapter_num, chapter_title=title, id_prefix=prefix
    )
    return questions


def load_sample_questions(path: Path | None = None) -> list[Question]:
    path = path or Path("samples/physics10_ch1_sample.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["questions"] if isinstance(data, dict) else data
    return validate_batch(
        items,
        chapter=1,
        chapter_title=_chapter_title(1),
        id_prefix="phys10-ch1-sample",
    )
