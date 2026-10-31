# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: FastAPI + Uvicorn local server; static HTML/CSS/JS (no SPA build). Chosen in the approved plan for a simple Windows launcher that opens a browser.

## Users

Content operators and teachers who generate grade-10 physics multiple-choice items from the official textbook PDF on a local Windows machine, then hand JSON/CSV to a separate exam site.

## Product Purpose

DarsAmooz (درس‌آموز) extracts textbook text, generates four-option Persian MCQs with explanations via an LLM, validates them, and exports JSON/CSV/Markdown. Success = usable export files grounded in chapter text, produced without leaving the local launcher flow.

## Positioning

Generation is grounded in extracted chapter PDF text (not generic quiz inventing). The client already owns the exam-taking UI; this product only produces items.

## Capabilities

- Ingest Physics 10 (math track, book 110209) via CLI
- Generate questions per chapter (CLI or local web UI)
- Sample mode without API key (chapter 1 bundled sample)
- Export JSON, CSV (Excel BOM), RTL Markdown
- Windows desktop shortcuts: start local web UI / stop processes

## Constraints

- No exam-taking UI, no user accounts
- Textbook PDF is copyrighted; not committed to git
- Localhost-only web UI (127.0.0.1)
- API keys stay in `.env` (never exposed to the browser)
- Ingest remains CLI-only in the first web UI phase

## Terminology

- فصل / chapter — Physics 10 chapters 1–5
- تست چهارگزینه‌ای — four-option MCQ
- حالت نمونه — bundled sample output (no LLM)
- خروجی — files under `data/exports/`

## Brand Commitments

- Product name: درس‌آموز / DarsAmooz
- Primary language of the operator UI: Persian RTL
- Focus textbook for MVP: فیزیک (۱) پایه دهم رشته ریاضی و فیزیک

## Accessibility

Keyboard-operable form controls; sufficient contrast for daytime desk use; focus-visible states on interactive controls.
