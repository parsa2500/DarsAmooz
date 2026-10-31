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
### لانچر ویندوز (رابط وب محلی)
- نصب وابستگی وب (یک‌بار): `pip install -e ".[web]"` (در `.[dev]` هم هست)
- اجرا: دوبارکلیک روی میانبر **DarsAmooz - اجرا** یا `scripts\launch.bat` — سرور روی `http://127.0.0.1:8765` بالا می‌آید و مرورگر باز می‌شود
- در UI: انتخاب فصل/تعداد، تولید، پیش‌نمایش سوالات، کپی مسیر خروجی
- توقف: **DarsAmooz - توقف** یا `scripts\kill.bat` (یا Ctrl+C در پنجرهٔ لانچر)
- ساخت دوبارهٔ میانبرها: `powershell -File scripts\create-desktop-shortcuts.ps1`
- اجرا بدون مرورگر / با پیش‌پر کردن فرم:
  `powershell -File scripts\launch.ps1 -Chapter 1 -Count 5 -Sample -NoBrowser`

```bash
cp .env.example .env
# در .env: LLM_PROVIDER و کلید/مدل مربوطه را بگذارید
# مثال Gemini: GEMINI_API_KEY + GEMINI_MODEL=gemini-3.6-flash
# مثال OpenRouter: LLM_PROVIDER=openrouter + OPENROUTER_API_KEY + OPENROUTER_MODEL=google/gemma-4-31b-it:free
# Fallback قابل‌مشاهده هنگام خطای API جمینای: LLM_FALLBACK_PROVIDER=openrouter
#   (لاگ هشدار + ثبت در metrics.json و وضعیت جاب UI؛ هرگز .env را commit نکنید)

darsamooz chapters
darsamooz ingest --pdf data/textbooks/physics10-riazi-110209.pdf
darsamooz generate --chapter 1 --count 20

# بدون کلید API — خروجی نمونهٔ دستی فصل ۱:
darsamooz generate --chapter 1 --sample

# تبدیل JSON موجود به Markdown خوانا (راست‌چین):
darsamooz to-md samples/physics10_ch1_gemini.json --out samples/physics10_ch1_gemini.md
```

خروجی‌های هر `generate` در `data/exports/` به صورت JSON و CSV و Markdown (`.md`) ذخیره می‌شوند.

هر تولید یک **run** در `data/runs/{run_id}/` هم می‌نویسد (`request.json`, `units_used.json`, `items.jsonl`, `metrics.json`, کپی export). بعد از `ingest`، نقشهٔ زیرفصل‌ها در `data/extracted/{slug}/chN/sections.json` ذخیره می‌شود تا تولید روی یونیت‌های کتاب و با verify نقل‌قول (`source_refs`) انجام شود.

### فاز ۲ — بازیابی / SQLite (هنوز به generate وصل نیست)
ماژول‌های BM25 و TF-IDF و ذخیرهٔ SQLite آمادهٔ استفاده‌اند ولی **در مسیر تولید سوال صدا زده نمی‌شوند**. برای آزمایش دستی:

```bash
darsamooz index-build --all
darsamooz retrieve --chapter 1 --query "مدلسازی" --method bm25
darsamooz db-sync
```

ایندکس‌ها کنار sections: `data/extracted/{slug}/chN/index_bm25.json` و `index_tfidf.json`. دیتابیس اختیاری: `data/darsamooz.sqlite`.

### بازبینی، تأیید جواب، تعادل و خروجی مشتری (MVP)
- **بازبینی:** هر سؤال وضعیت `pending` / `approved` / `rejected` / `needs_review` دارد. در UI یا با `darsamooz review` تأیید/رد/ویرایش کنید. فقط تأییدشده‌ها: `darsamooz export-approved`.
- **تأیید جواب:** بعد از تولید، یک پاس دوم LLM کور سؤال را حل می‌کند (`--verify` پیش‌فرض روشن؛ `--no-verify` برای خاموش). اختلاف یا ابهام → `needs_review` (حذف نمی‌شود). مدل جدا: `VERIFY_MODEL` در `.env`.
- **Fallback LLM:** اگر `LLM_FALLBACK_PROVIDER=openrouter` باشد، هنگام خطای API ارائه‌دهندهٔ اصلی (۴۲۹/سهمیه، ۵xx، تایم‌اوت و …) به‌طور خودکار و **قابل‌مشاهده** به OpenRouter می‌رود (هشدار در لاگ، `fallback_count` / `calls_by_provider` / دلایل در `metrics.json` و UI). اگر fallback هم شکست بخورد، خطای واضح پرتاب می‌شود. خاموش: خالی/`none`.
- **تعادل:** گزینه‌ی درست به‌صورت قطعی روی الف–د پخش می‌شود (`--balance-answers`). ترکیب سختی: `--difficulty-mix easy=30,medium=50,hard=20`. توزیع در `metrics.json` و UI.
- **خروجی مشتری:** پروفایل نگاشت در `data/export_profiles/` (فعلاً فقط `generic`). وقتی فرمت import سایت مشتری رسید، یک JSON جدید اضافه کنید — فرمت مشتری را از خودمان اختراع نمی‌کنیم.
  ```bash
  darsamooz profiles
  darsamooz export-client data/exports/phys10-ch1.json --profile generic --out data/exports/phys10-ch1.generic.json
  darsamooz generate --chapter 1 --count 10 --verify --difficulty-mix easy=30,medium=50,hard=20 --export-profile generic
  ```

### نکته حقوقی
PDF کتاب درسی مشمول حق‌تألیف سازمان پژوهش و برنامه‌ریزی آموزشی است و **داخل ریپو commit نمی‌شود**. فقط اسکریپت دانلود و manifest نگه داشته می‌شود.

---

## English

### Windows local web UI
Install web extras (`pip install -e ".[web]"`), then run `scripts\launch.bat` (or the desktop shortcut). That starts `python -m darsamooz.web` on `http://127.0.0.1:8765` and opens the browser. Stop with `scripts\kill.bat`. CLI `generate` remains available for scripting.

### Pipeline
1. **Ingest** — `pdftotext` (poppler) → cleaned Persian text → chapter split  
2. **Chunk** — paragraph-aware chunks for LLM context  
3. **Generate** — Gemini / OpenRouter / OpenAI-compatible API; questions are grounded in extracted chapter PDF text  
4. **Validate** — exactly 4 options, one correct index, explanation, chapter, difficulty  
5. **Export** — JSON + CSV (UTF-8 BOM for Excel) + RTL Persian Markdown (`.md`, answers in `<details>`), plus a run folder under `data/runs/` with unit coverage and token metrics when the API reports usage. Ingest also writes `data/extracted/{slug}/chN/sections.json` for subsection-grounded generation.

**Phase 2 retrieval/store (not wired into generate):** `darsamooz index-build`, `darsamooz retrieve`, and `darsamooz db-sync` build BM25/TF-IDF indexes and an optional SQLite mirror. The planner still uses round-robin over sections.

Convert an existing JSON export:
```bash
darsamooz to-md path/to/questions.json --out path/to/questions.md
```

### Review, verify, balance, client export (MVP)
- **Review:** each question has `pending` / `approved` / `rejected` / `needs_review`. Approve/reject/edit in the web UI or via `darsamooz review`. Export approved-only with `darsamooz export-approved`.
- **Answer verification:** after generation a blind second LLM pass solves each item (`--verify` on by default). Mismatch/ambiguity → `needs_review` (never silently dropped). Optional `VERIFY_MODEL` in `.env`.
- **LLM fallback:** with `LLM_FALLBACK_PROVIDER=openrouter`, primary API errors (429/quota, 5xx, timeouts, other HTTP/API failures) **visibly** switch to OpenRouter — warning log, plus `fallback_count` / `calls_by_provider` / reasons in `metrics.json` and the web job status. Applies to generation and verify. If fallback also fails, a clear combined error is raised. Disable with empty/`none`.
- **Balance:** correct options are deterministically redistributed over A–D (`--balance-answers`). Steer difficulty with `--difficulty-mix easy=30,medium=50,hard=20`. Distributions land in `metrics.json` and the UI.
- **Client export:** mapping profiles live under `data/export_profiles/` (shipped: `generic` only). When the client's exam-site import format arrives, add a new profile JSON — do not invent their schema.
  ```bash
  darsamooz profiles
  darsamooz export-client path/to/questions.json --profile generic --out path/to/mapped.json --approved-only
  ```

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
- `LLM_FALLBACK_PROVIDER=openrouter` — optional visible fallback target when primary is e.g. Gemini

**OpenAI-compatible**
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` when `LLM_PROVIDER=openai`

There is **no silent model or syllabus fallback**. Provider/model misconfiguration still raises. When `LLM_FALLBACK_PROVIDER` is set, primary **API** failures visibly fall back to that provider (logged and recorded); if both fail, the error is raised.

### Sample output
- `samples/physics10_ch1_sample.json` — **5 hand-written SAMPLE** questions (no API key: `darsamooz generate --chapter 1 --sample`).
- `samples/physics10_ch1_gemini.json` / `.csv` / `.md` — **20 Gemini-generated** chapter-1 questions grounded in book text (Markdown is RTL-friendly for reading on GitHub).
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
