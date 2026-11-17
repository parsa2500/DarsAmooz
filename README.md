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
# در .env: LLM_PROVIDER و کلید/مدل مربوطه را بگذارید (پیش‌فرض: openrouter)
# OpenRouter: OPENROUTER_API_KEY + OPENROUTER_MODEL=… (مثلاً google/gemma-4-31b-it:free)
# Fallback قابل‌مشاهده به Gemini هنگام خطای API: LLM_FALLBACK_PROVIDER=gemini
#   (لاگ هشدار + ثبت در metrics.json و وضعیت جاب UI؛ هرگز .env را commit نکنید)
# جایگزین: LLM_PROVIDER=gemini + GEMINI_API_KEY + GEMINI_MODEL=gemini-3.6-flash

darsamooz chapters
darsamooz ingest --pdf data/textbooks/physics10-riazi-110209.pdf
darsamooz generate --chapter 1 --count 20

# انتخاب provider/مدل فقط برای همین اجرا (بدون تغییر .env):
darsamooz generate --chapter 1 --count 10 --provider gemini --model gemini-3.6-flash
darsamooz generate --chapter 1 --count 5 --provider openrouter --model google/gemma-4-31b-it:free

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
- **الگوبرداری وب (اختیاری):** در UI گزینه «الگوبرداری از تست‌های مشابه وب» — با همان کلید LLM (ترجیحاً OpenRouter و ابزار web_search)، استخراج *سبک/ساختار* به‌صورت few-shot، ذخیره در `style_pack.json`. محتوا و `source_refs` همچنان فقط از متن منبع شماست.
- **Fallback LLM:** پیش‌فرض `LLM_PROVIDER=openrouter` و `LLM_FALLBACK_PROVIDER=gemini`. هنگام خطای API ارائه‌دهندهٔ اصلی (۴۲۹/سهمیه، ۵xx، تایم‌اوت و …) به‌طور خودکار و **قابل‌مشاهده** به fallback می‌رود (هشدار در لاگ، `fallback_count` / `calls_by_provider` / دلایل در `metrics.json` و UI) — هر دو جهت openrouter↔gemini پشتیبانی می‌شود. اگر fallback هم شکست بخورد، خطای واضح پرتاب می‌شود. خاموش: خالی یا none.
- **تعادل:** گزینه‌ی درست به‌صورت قطعی روی الف–د پخش می‌شود (`--balance-answers`). ترکیب سختی با روبریک واقعی کنکور/دهم: `--difficulty-mix easy=30,medium=50,hard=20` (hard جعلیِ تعریفی به medium تنزل می‌یابد). ترکیب الگو: `--pattern-mix tarifi=20,mafhumi=40,halli=30,tarkibi=10` (تعریفی/مفهومی/حلی/ترکیبی). توزیع سختی و الگو در `metrics.json` و UI.
- **خروجی مشتری:** پروفایل نگاشت در `data/export_profiles/` (فعلاً فقط `generic`). وقتی فرمت import سایت مشتری رسید، یک JSON جدید اضافه کنید — فرمت مشتری را از خودمان اختراع نمی‌کنیم.
  ```bash
  darsamooz profiles
  darsamooz export-client data/exports/phys10-ch1.json --profile generic --out data/exports/phys10-ch1.generic.json
  darsamooz generate --chapter 1 --count 10 --verify --difficulty-mix easy=30,medium=50,hard=20 --pattern-mix tarifi=20,mafhumi=40,halli=30,tarkibi=10 --export-profile generic
  ```

### جریان جزوه ← الگوی تست ← تولید (جدید)
وقتی یک جزوه/PDF آپلود می‌شود:
1. **تشخیص موضوع:** مدل متن را می‌خواند و درس، پایه، رشته، فصل/مبحث، مفاهیم کلیدی و عبارت‌های جستجو را برمی‌گرداند. در UI قابل ویرایش و تأیید است (`data/extracted/<slug>/chN/topic_profile.json`).
2. **جستجوی واقعی در وب:** تست‌های واقعی همان مبحث (کنکور، نهایی، آزمون‌های آزمایشی، سایت‌های بانک تست) جستجو می‌شود و صفحه‌ها خوانده می‌شوند تا نمونه تست‌ها استخراج شوند. آدرس منابع و تکه‌های کوتاه در `web_sources.json` ذخیره می‌شود و در پوشه هر اجرا هم کپی می‌شود.
   - روش‌ها (بدون هزینه اضافه): `gemini` = Gemini API با `google_search` grounding (همان `GEMINI_API_KEY`؛ آدرس‌ها از groundingMetadata، نه متن مدل) و `duckduckgo` = صفحه HTML داک‌داک‌گو بدون کلید. حالت `auto` اول Gemini و بعد DuckDuckGo را امتحان می‌کند و هر تلاش ثبت می‌شود.
   - اگر جستجو هیچ منبعی پیدا نکند، خطا در UI/CLI نشان داده می‌شود و هیچ منبع ساختگی ثبت نمی‌شود. کاربر می‌تواند «ادامه بدون وب» را انتخاب کند؛ آن بسته با `status=llm_only` و صفر منبع علامت می‌خورد.
3. **استخراج الگو:** مدل از روی نمونه‌ها فقط *الگو* درمی‌آورد: اسکلت صورت سؤال، نوع (تعریفی/مفهومی/حلی/ترکیبی)، عددی یا مفهومی، سختی، شکل گزینه‌ها، روش ساخت گزینه‌های غلط و دام‌ها (`pattern_pack.json`، قابل ویرایش/غیرفعال‌سازی در UI).
4. **تولید:** هر دور یک الگو را هدف می‌گیرد و محتوا فقط از متن جزوه می‌آید (نقل‌قول منبع همچنان چک می‌شود). هر صورت سؤال با تست‌های خوانده‌شده از وب مقایسه می‌شود و اگر بیش از ۶۰٪ شبیه باشد با دلیل `web_near_duplicate` رد می‌شود. بازبینی، تأیید دوم، پخش جواب‌ها و خروجی مثل قبل است.

```bash
darsamooz ingest-doc --pdf jozve.pdf                 # یا آپلود در UI
darsamooz analyze-topic --slug <slug> --chapter 1     # مرحله ۱
darsamooz mine-web-patterns --slug <slug> --chapter 1 [--backend auto|gemini|duckduckgo] [--llm-only]
darsamooz generate --slug <slug> --chapter 1 --count 10 --pattern-pack auto --difficulty-mix easy=30,medium=50,hard=20
```
در `metrics.json` فیلدهای `pattern_pack_status`، `web_sources_count`، `patterns_count`، `pattern_usage`، `search_backend`، `search_error` و `web_dup_rejected` ثبت می‌شود.

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
- **Web style mining (optional):** UI toggle uses your existing LLM keys (OpenRouter web search preferred) for structure-only few-shots (`style_pack.json`). Facts/`source_refs` stay grounded in your source text.
- **LLM fallback:** defaults are `LLM_PROVIDER=openrouter` and `LLM_FALLBACK_PROVIDER=gemini`. Primary API errors (429/quota, 5xx, timeouts, other HTTP/API failures) **visibly** switch to the fallback — warning log, plus `fallback_count` / `calls_by_provider` / reasons in `metrics.json` and the web job status. Works both ways (openrouter↔gemini). Applies to generation and verify. If fallback also fails, a clear combined error is raised. Disable with empty/none.
- **Balance:** correct options are redistributed over A–D (`--balance-answers`). Steer difficulty with `--difficulty-mix easy=30,medium=50,hard=20` (soft fake-hard definitional items are downgraded). Steer patterns with `--pattern-mix tarifi=20,mafhumi=40,halli=30,tarkibi=10`. Distributions land in `metrics.json` and the UI.
- **Client export:** mapping profiles live under `data/export_profiles/` (shipped: `generic` only). When the client's exam-site import format arrives, add a new profile JSON — do not invent their schema.
  ```bash
  darsamooz profiles
  darsamooz export-client path/to/questions.json --profile generic --out path/to/mapped.json --approved-only
  ```

### Handout → test patterns → generation (new)
When a handout/PDF is uploaded:
1. **Topic detection** — the LLM reads the text and returns subject, grade, track, chapter/topic, key concepts and search queries. Editable/confirmable in the UI (`data/extracted/<slug>/chN/topic_profile.json`).
2. **Real web search** for real exam tests on that topic (Konkur, final exams, mock exams, Persian test-bank sites). Result pages are fetched and question excerpts extracted. URLs + short snippets go to `web_sources.json` (also copied into each run folder).
   - Backends (no extra cost): `gemini` = Gemini API `google_search` grounding (same `GEMINI_API_KEY`; URLs from groundingMetadata, never from model text) and `duckduckgo` = keyless DuckDuckGo HTML. `auto` tries Gemini then DuckDuckGo and records every attempt. OpenRouter web search is not used by default because it is billed per result.
   - If no source is found the failure is shown in UI/CLI/metrics and nothing is invented. The user may choose "continue without web"; that pack is marked `status=llm_only` with zero sources.
3. **Pattern extraction** — the LLM turns the excerpts into structure-only patterns: stem skeleton, type (tarifi/mafhumi/halli/tarkibi), numeric vs conceptual, difficulty, option format, distractor strategies, traps (`pattern_pack.json`, editable / per-pattern enable in the UI).
4. **Generation** — each round targets one pattern; facts come only from the handout (source-quote grounding still enforced). Every stem is compared against fetched web questions (5-char shingle containment ≥ 0.6 → rejected as `web_near_duplicate`). Review/verify/balance/export unchanged.

CLI: `ingest-doc`, `analyze-topic`, `mine-web-patterns [--backend] [--llm-only] [--no-fetch]`, `generate --slug … --pattern-pack auto|PATH`.
Metrics: `pattern_pack_status`, `web_sources_count`, `patterns_count`, `pattern_usage`, `search_backend`, `search_error`, `web_dup_rejected`.
Optional env: `GEMINI_SEARCH_MODEL` (model used only for grounded search; defaults to `GEMINI_MODEL`).

### Config
See `.env.example`. Default provider is **openrouter**. Select with `LLM_PROVIDER=openrouter` | `gemini` | `openai`.

**Gemini**
- `GEMINI_API_KEY` — local `.env` only (gitignored; never commit)
- `GEMINI_MODEL` — exact model id (required; no silent default). Prefer Flash, e.g. `gemini-3.6-flash`
- `GEMINI_BASE_URL` — defaults to `https://generativelanguage.googleapis.com/v1beta/openai/`

**OpenRouter**
- `OPENROUTER_API_KEY` — local `.env` only
- `OPENROUTER_MODEL` — exact model id (required). Free models end with `:free` (e.g. `google/gemma-4-31b-it:free`)
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1`
- `LLM_FALLBACK_PROVIDER=gemini` — optional visible fallback (default in `.env.example` when primary is OpenRouter)

**OpenAI-compatible**
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` when `LLM_PROVIDER=openai`

**Web style mining** reuses `OPENROUTER_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` (no separate search key).

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
