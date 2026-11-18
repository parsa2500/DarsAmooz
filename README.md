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
# OpenRouter: OPENROUTER_API_KEY + OPENROUTER_MODEL=… (مثلاً nvidia/nemotron-3-ultra-550b-a55b:free)
# پیش‌فرض: همه فراخوانی‌های مدل فقط به OpenRouter (LLM_FALLBACK_PROVIDER خالی). fallback اختیاری: LLM_FALLBACK_PROVIDER=gemini
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
- **Fallback LLM (پیش‌فرض خاموش):** پیش‌فرض `LLM_PROVIDER=openrouter` و `LLM_FALLBACK_PROVIDER` خالی است؛ یعنی تولید، تأیید جواب، تشخیص موضوع و استخراج الگو همه فقط با OpenRouter انجام می‌شود. اگر `LLM_FALLBACK_PROVIDER=gemini` گذاشته شود، هنگام خطای API ارائه‌دهندهٔ اصلی (۴۲۹/سهمیه، ۵xx، تایم‌اوت و …) به‌طور خودکار و **قابل‌مشاهده** به fallback می‌رود (هشدار در لاگ، `fallback_count` / `calls_by_provider` / دلایل در `metrics.json` و UI) — هر دو جهت openrouter↔gemini پشتیبانی می‌شود. اگر fallback هم شکست بخورد، خطای واضح پرتاب می‌شود. خاموش: خالی یا none.
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
   - روش‌ها: `tavily` = [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) با `TAVILY_API_KEY` (پیشنهادی؛ سهمیه رایگان ماهانه). `google_cse` = Google Programmable Search (Custom Search JSON API، ۱۰۰ جستجوی رایگان در روز، جستجوی فارسی با `lr=lang_fa` و `gl=ir`) — **برای مشتری جدید بسته است** و برای کاربران فعلی هم از ۱ ژانویه ۲۰۲۷ خاموش می‌شود؛ فقط اگر از قبل دسترسی دارید به‌عنوان مرحله بعد در `auto` می‌ماند. `duckduckgo` = صفحه HTML داک‌داک‌گو بدون کلید (به‌خاطر captcha سریع محدود می‌شود). حالت `auto` اول Tavily (اگر کلید باشد)، بعد Google CSE (اگر هر دو کلید باشد) و بعد DuckDuckGo را امتحان می‌کند. هر تلاش و دلیل سوییچ ثبت و چاپ می‌شود (سوییچ بی‌صدا نیست). `gemini` (grounding) فقط با انتخاب صریح `--backend gemini` استفاده می‌شود.
   - سقف جستجو در هر اجرا: `WEB_SEARCH_MAX_QUERIES` (پیش‌فرض ۶، برای همه روش‌ها روی هم). خطای سهمیه یا ۴۲۹ فوراً همان موتور را متوقف می‌کند و پیام واضح می‌دهد.
   - **گرفتن کلید Tavily (یک‌بار، پیشنهادی):** در [app.tavily.com](https://app.tavily.com) ثبت‌نام کنید و کلید را فقط در `.env` محلی به‌صورت `TAVILY_API_KEY` بگذارید (هرگز commit نکنید). اختیاری: `TAVILY_COUNTRY` (پیش‌فرض `iran`؛ برای خاموش کردن `none`).
   - **کلید Google (فقط اگر از قبل به Custom Search JSON API دسترسی دارید):** این سرویس مشتری جدید قبول نمی‌کند. اگر کلید و شناسه موتور را دارید، `GOOGLE_CSE_API_KEY` و `GOOGLE_CSE_CX` را فقط در `.env` محلی بگذارید.
   - اگر جستجو هیچ منبعی پیدا نکند، خطا در UI/CLI نشان داده می‌شود و هیچ منبع ساختگی ثبت نمی‌شود. کاربر می‌تواند «ادامه بدون وب» را انتخاب کند؛ آن بسته با `status=llm_only` و صفر منبع علامت می‌خورد.
3. **استخراج الگو:** مدل از روی نمونه‌ها فقط *الگو* درمی‌آورد: اسکلت صورت سؤال، نوع (تعریفی/مفهومی/حلی/ترکیبی)، عددی یا مفهومی، سختی، شکل گزینه‌ها، روش ساخت گزینه‌های غلط و دام‌ها (`pattern_pack.json`، قابل ویرایش/غیرفعال‌سازی در UI).
4. **تولید:** هر دور یک الگو را هدف می‌گیرد و محتوا فقط از متن جزوه می‌آید (نقل‌قول منبع همچنان چک می‌شود). هر صورت سؤال با تست‌های خوانده‌شده از وب مقایسه می‌شود و اگر بیش از ۶۰٪ شبیه باشد با دلیل `web_near_duplicate` رد می‌شود. بازبینی، تأیید دوم، پخش جواب‌ها و خروجی مثل قبل است.
5. **ماندن در فصل:** کوئری‌های جستجو از عنوان بخش‌های واقعی فصل (از متن کتاب) ساخته می‌شوند. کوئری‌هایی که واژه‌ی ناموجود در کتاب دارند (`term_not_in_book`) یا به هیچ بخشی از فصل نمی‌خورند (`off_chapter`) حذف و در خروجی اعلام می‌شوند. هر الگو باید به یک بخش فصل نسبت داده شود و یک بررسی جداگانه‌ی مدل، الگوهای خارج از فصل را حذف می‌کند (`dropped_patterns`). اگر این بررسی خطا بدهد، با هشدار اعلام می‌شود.
6. **فیلتر کیفیت:** سؤال حفظی و تعریفی یک‌خطی (`trivial_recall`)، فارسی خراب یا واژه‌ی ساختگی ناموجود در کتاب (`garbled_text`) و ناهماهنگی با الگو (`pattern_mismatch`) رد می‌شوند. اگر سؤال با الگوی دیگری جور باشد، برچسبش اصلاح می‌شود (`web_pattern_retagged`). اگر بررسی کیفیت انجام نشود، سؤال با برچسب `quality_unchecked` برای بازبینی علامت می‌خورد.

```bash
darsamooz ingest-doc --pdf jozve.pdf                 # یا آپلود در UI
darsamooz analyze-topic --slug <slug> --chapter 1     # مرحله ۱
darsamooz mine-web-patterns --slug <slug> --chapter 1 [--backend auto|tavily|google_cse|duckduckgo|gemini] [--max-queries 6] [--llm-only]
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
- **LLM fallback (off by default):** defaults are `LLM_PROVIDER=openrouter` and an empty `LLM_FALLBACK_PROVIDER`, so generation, verify, topic detection and pattern extraction all go to OpenRouter only. If you set `LLM_FALLBACK_PROVIDER=gemini`, primary API errors (429/quota, 5xx, timeouts, other HTTP/API failures) **visibly** switch to the fallback — warning log, plus `fallback_count` / `calls_by_provider` / reasons in `metrics.json` and the web job status. Works both ways (openrouter↔gemini). Applies to generation and verify. If fallback also fails, a clear combined error is raised. Disable with empty/none.
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
   - Backends: `tavily` = [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) with `TAVILY_API_KEY` (recommended; monthly free allowance). `google_cse` = Google Programmable Search / Custom Search JSON API (100 free queries/day; Persian via `lr=lang_fa`, `gl=ir`) — **closed to new customers** and shutting down for remaining users on 1 January 2027; kept only as a later `auto` step if you already have access. `duckduckgo` = keyless DuckDuckGo HTML (hits captchas quickly). `auto` = tavily (when `TAVILY_API_KEY` is set) → google_cse (when both CSE keys are set) → duckduckgo. Every attempt and the reason for falling back are logged (never silent). `gemini` grounding is optional and only used with an explicit `--backend gemini`. OpenRouter web search is not used because it is billed per result.
   - Quota: `WEB_SEARCH_MAX_QUERIES` (default 6) caps search requests per run across backends. Quota or 429 errors stop that engine immediately with a clear message.
   - **Getting a Tavily key (once, recommended):** sign up at [app.tavily.com](https://app.tavily.com) and put the key only in local `.env` as `TAVILY_API_KEY` (never commit it). Optional: `TAVILY_COUNTRY` (default `iran`; `none` disables the boost).
   - **Google keys (only if you already have Custom Search JSON API access):** this API does not accept new customers. If you already have a key and engine id, put `GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_CX` only in local `.env`.
   - If no source is found the failure is shown in UI/CLI/metrics and nothing is invented. The user may choose "continue without web"; that pack is marked `status=llm_only` with zero sources.
3. **Pattern extraction** — the LLM turns the excerpts into structure-only patterns: stem skeleton, type (tarifi/mafhumi/halli/tarkibi), numeric vs conceptual, difficulty, option format, distractor strategies, traps (`pattern_pack.json`, editable / per-pattern enable in the UI).
4. **Generation** — each round targets one pattern; facts come only from the handout (source-quote grounding still enforced). Every stem is compared against fetched web questions (5-char shingle containment ≥ 0.6 → rejected as `web_near_duplicate`). Review/verify/balance/export unchanged.
5. **Chapter scope** — search queries are built from the chapter's real section titles (book text). LLM queries using words absent from the book (`term_not_in_book`) or matching no section (`off_chapter`) are dropped and reported. Every pattern must map to a chapter section; an LLM scope audit drops off-chapter patterns (`dropped_patterns`). A failed audit is reported, never treated as a pass.
6. **Quality gate** — rejects one-line definition/heading recall (`trivial_recall`), broken/transliterated Persian not in the book (`garbled_text`) and questions that do not fit their web pattern (`pattern_mismatch`; retagged when another pattern fits). If the LLM review fails, the question is tagged `quality_unchecked` + needs_review. Disable with `--no-quality-review`.

CLI: `ingest-doc`, `analyze-topic`, `mine-web-patterns [--backend] [--max-queries] [--llm-only] [--no-fetch]`, `generate --slug … --pattern-pack auto|PATH`.
Metrics: `pattern_pack_status`, `web_sources_count`, `patterns_count`, `pattern_usage`, `search_backend`, `search_error`, `web_dup_rejected`.
Env: `TAVILY_API_KEY`, optional `TAVILY_COUNTRY` (default `iran`; `none` disables); `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX`, `WEB_SEARCH_MAX_QUERIES`; optional `GOOGLE_CSE_LR` (default `lang_fa`), `GOOGLE_CSE_GL` (default `ir`; `none` disables), `GEMINI_SEARCH_MODEL` (only for `--backend gemini`).

### Config
See `.env.example`. Default provider is **openrouter**. Select with `LLM_PROVIDER=openrouter` | `gemini` | `openai`.

**Gemini**
- `GEMINI_API_KEY` — local `.env` only (gitignored; never commit)
- `GEMINI_MODEL` — exact model id (required; no silent default). Prefer Flash, e.g. `gemini-3.6-flash`
- `GEMINI_BASE_URL` — defaults to `https://generativelanguage.googleapis.com/v1beta/openai/`

**OpenRouter**
- `OPENROUTER_API_KEY` — local `.env` only
- `OPENROUTER_MODEL` — exact model id (required). Free models end with `:free` (e.g. `nvidia/nemotron-3-ultra-550b-a55b:free`). `VERIFY_MODEL` optionally sets a separate model for answer verification
- `OPENROUTER_BASE_URL` — defaults to `https://openrouter.ai/api/v1`
- `LLM_FALLBACK_PROVIDER` — empty by default (OpenRouter only). Set `gemini` for an optional visible fallback.

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
