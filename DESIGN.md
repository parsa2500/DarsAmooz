# Design

<!-- impeccable:design-schema 1 -->

## Surface

Local operator UI at `web/static/` served by `darsamooz.web` on `127.0.0.1:8765`. Mode: **Operate**.

## World

**Exam desk / lab worksheet.** Cool slate paper field, deep lab-teal ink, sharp ruled panels. Daytime desk under cool light — light theme. Persian RTL with Vazirmatn. Refuses cream+serif, purple glow, and marketing-hero scaffolds.

## Tokens

| Role | Value |
|------|--------|
| Background | `#dfe6ee` with soft cool glows |
| Paper | `#f3f6f9` |
| Ink | `#152033` / soft `#3a4a60` |
| Accent | `#0a6b6b` → `#064f50` |
| Lines | `#b7c4d4` / `#8fa0b5` |
| Success / warn / error | green `#1f6b45`, amber `#9a5b12`, rose `#8f2d3a` soft fills |
| Radius | `2px` (worksheet, not pill UI) |
| Type | Vazirmatn 400–700 |

## Composition

1. Masthead: brand **درس‌آموز** dominant, book title + provider status opposite
2. Generate panel: chapter, count, sample toggle, primary action, live status + log
3. Preview panel: question list with correct option tint and collapsible explanation; export path copy actions
4. Footer: version + localhost-only reminder

## Motion

Single pulse on the running status dot; button press offset. Respects `prefers-reduced-motion`.

## Out of scope visually

Exam-taking chrome, leaderboards, student avatars, dark neon dashboards.
