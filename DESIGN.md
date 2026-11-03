# Design

<!-- impeccable:design-schema 1 -->

## Surface

Local operator UI at `web/static/` served by `darsamooz.web` on `127.0.0.1:8765`. Mode: **Operate**.

## World

**Exam desk / lab worksheet.** Cool slate paper field, deep lab-teal ink, sharp ruled panels. Daytime desk under cool light — light theme. Persian RTL with Vazirmatn. Refuses cream+serif, purple glow, eyebrow chrome, and marketing-hero scaffolds.

## Tokens

| Role | Value |
|------|--------|
| Background | `#dfe6ee` with soft cool glows |
| Paper | `#f3f6f9` / secondary `#e9eef4` |
| Ink | `#152033` / soft `#3a4a60` |
| Accent | `#0a6b6b` → `#064f50` (soft fill `#d4ecec`) |
| Lines | `#b7c4d4` / `#8fa0b5` |
| Success / warn / error | green `#1f6b45`, amber `#9a5b12`, rose `#8f2d3a` + soft fills |
| Radius | `2px` (worksheet, not pill UI) |
| Type | Vazirmatn 400–700 |
| Shadow | `0 12px 32px rgba(21, 32, 51, 0.09)` |

## Composition

1. **Masthead:** brand **درس‌آموز** at hero scale; English `DarsAmooz` underline mark; book title + provider/model line + version opposite (no eyebrow).
2. **Workspace (desktop two-column):**
   - **Work panel:** title «تولید تست» + lede; ruled field groups (not nested white cards) — محتوا، مدل، کیفیت (verify/balance primary; advanced mixes/style-mine collapsed); sticky action bar for generate/cancel.
   - **Status rail:** sticky live status + taller log + export paths beside the form.
3. **Preview:** toolbar (approved-only, export approved, reload) + dense question list with difficulty + Persian pattern badges, review status, tinted correct option, collapsible source quote, approve/reject/edit.
4. **Footer:** localhost-only + grounding reminder.

## Motion

Page enter fade/slide; status-strip enter; pulse on running status dot; question enter on preview load; button press offset. All gated by `prefers-reduced-motion`.

## Responsive

Below ~860px: single-column workspace and stacked masthead. Below ~560px: tighter page padding.

## Figma

Capture of the running Operate UI: [DarsAmooz Operate UI](https://www.figma.com/design/P0g8Z6AJpD7ZGXv19mVgcQ?node-id=1-2).

## Out of scope visually

Exam-taking chrome, leaderboards, student avatars, dark neon dashboards, identical decorative card stacks.
