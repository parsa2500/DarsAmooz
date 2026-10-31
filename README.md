# DarsAmooz / درس‌آموز

Generate **multiple-choice test questions (تست چهارگزینه‌ای)** from Iranian textbook PDFs.  
This project **only produces questions** (JSON/CSV). The client already has their own exam site — there is no exam UI and no user system.

Focus textbook for the MVP: **فیزیک (۱) پایه دهم رشته ریاضی و فیزیک** (book code **110209**).

---

## Persian / فارسی

### هدف
از PDF کتاب درسی، متن استخراج می‌شود، به فصل‌ها شکسته می‌شود، و با یک مدل زبانی تست چهارگزینه‌ای با پاسخ و توضیح ساخته می‌شود. خروجی JSON و CSV است تا به سامانه آزمون مشتری داده شود.

### نصب
```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# نیاز به poppler برای استخراج متن:
# Linux: sudo apt install poppler-utils
# Windows: install Poppler and put pdftotext on PATH
```

### دانلود کتاب (بدون commit کردن PDF)
```bash
python scripts/download_physics10.py
# یا دستی از صفحه رسمی: http://www.chap.sch.ir/books/4999
```

### استفاده
```bash
cp .env.example .env
# در .env: LLM_PROVIDER و کلید/مدل مربوطه را بگذارید
# مثال Gemini: GEMINI_API_KEY + GEMINI_MODEL=gemini-3.6-flash
# مثال OpenRouter: LLM_PROVIDER=openrouter + OPENROUTER_API_KEY + OPENROUTER_MODEL=google/gemma-4-31b-it:free

darsamooz chapters
darsamooz ingest --pdf data/textbooks/physics10-riazi-110209.pdf
darsamooz generate --chapter 1 --count 20

# بدون کلید API — خروجی نمونهٔ دستی فصل ۱:
darsamooz generate --chapter 1 --sample
```

خروجی‌ها در `data/exports/` ذخیره می‌شوند.

### نکته حقوقی
PDF کتاب درسی مشمول حق‌تألیف سازمان پژوهش و برنامه‌ریزی آموزشی است و **داخل ریپو commit نمی‌شود**. فقط اسکریپت دانلود و manifest نگه داشته می‌شود.

---

## English

### Pipeline
1. **Ingest** — `pdftotext` (poppler) → cleaned Persian text → chapter split  
2. **Chunk** — paragraph-aware chunks for LLM context  
3. **Generate** — Gemini / OpenRouter / OpenAI-compatible API; questions are grounded in extracted chapter PDF text  
4. **Validate** — exactly 4 options, one correct index, explanation, chapter, difficulty  
5. **Export** — JSON + CSV (UTF-8 BOM for Excel)

### Config
See `.env.example`. Select provider with `LLM_PROVIDER=gemini` | `openrouter` | `openai`.

**Gemini**
- `GEMINI_API_KEY` — local `.env` only (gitignored; never commit)
- `GEMINI_MODEL` — exact model id (required; no silent default). Prefer Flash, e.g. `gemini-3.6-flash`
- `GEMINI_BASE_URL` — defaults to `https://generativelanguage.googleapis.com/v1beta/openai/`

**OpenRouter**
- `OPENROUTER_API_KEY` — local `.env` only
- `OPENROUTER_MODEL` — exact model id (required). Free models end with `:free` (e.g. `google/gemma-4-31b-it:free`)
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1`

**OpenAI-compatible**
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` when `LLM_PROVIDER=openai`

There is **no silent model or syllabus fallback**. If the configured provider/model rejects a request, darsamooz raises the error.

### Sample output
- `samples/physics10_ch1_sample.json` — **5 hand-written SAMPLE** questions (no API key: `darsamooz generate --chapter 1 --sample`).
- `samples/physics10_ch1_gemini.json` / `.csv` — **20 Gemini-generated** chapter-1 questions grounded in book text.
- `samples/physics10_ch1_openrouter.json` / `.csv` — **5 OpenRouter** chapter-1 questions (`OPENROUTER_MODEL`, free `:free` pool).

### Tests
```bash
pytest
```

### Chapters (Physics 10 math track)
| # | Title |
|---|--------|
| 1 | فیزیک و اندازه‌گیری |
| 2 | ویژگی‌های فیزیکی مواد |
| 3 | کار، انرژی و توان |
| 4 | دما و گرما |
| 5 | ترمودینامیک |

### Source
- Official catalog page: http://www.chap.sch.ir/books/4999  
- Download helper uses a documented mirror URL in `data/manifests/physics10-riazi-110209.json`
