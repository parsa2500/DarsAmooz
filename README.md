# DarsAmooz / درس‌آموز

Generate **multiple-choice test questions (تست چهارگزینه‌ای)** from Iranian textbook PDFs.  
This project **only produces questions** (JSON/CSV). The client already has their own exam site — there is no exam UI and no user system.

Focus textbook for the MVP: **فیزیک (۱) پایه دهم رشته ریاضی و فیزیک** (book code **110209**).

---

## Persian / فارسی

### هدف
از PDF کتاب درسی، متن استخراج می‌شود، به فصل‌ها شکسته می‌شود، و با یک مدل زبانی (API سازگار با OpenAI) تست چهارگزینه‌ای با پاسخ و توضیح ساخته می‌شود. خروجی JSON و CSV است تا به سامانه آزمون مشتری داده شود.

### نصب
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# نیاز به poppler برای استخراج متن:
# sudo apt install poppler-utils
```

### دانلود کتاب (بدون commit کردن PDF)
```bash
python scripts/download_physics10.py
# یا دستی از صفحه رسمی: http://www.chap.sch.ir/books/4999
```

### استفاده
```bash
cp .env.example .env   # سپس OPENAI_API_KEY را بگذارید

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
3. **Generate** — OpenAI-compatible Chat Completions (`OPENAI_API_KEY`, optional `OPENAI_BASE_URL` / `OPENAI_MODEL`)  
4. **Validate** — exactly 4 options, one correct index, explanation, chapter, difficulty  
5. **Export** — JSON + CSV (UTF-8 BOM for Excel)

### Config
See `.env.example`. Any OpenAI-compatible endpoint works (OpenAI, Azure OpenAI, Groq, Together, local vLLM, …).

### Sample output
`samples/physics10_ch1_sample.json` contains **5 hand-written SAMPLE** questions for chapter 1 so the schema is visible without an API key.

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
