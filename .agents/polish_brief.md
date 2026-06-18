# Phase 3 — UI refinement punch-list (round 3)

**Team:** `p3-polish`. Lead = `team-lead`. `designer` (opus) implements, `reviewer` (opus) reviews via screenshots.
Branch `rewrite/fastapi-react`. Work only under `web/`. Builds on the committed redesign (6151bc2).

Same hard constraints as before: **layout / markup / CSS + `lib/time.ts` formatting only** — NO changes to
data fetching, API, query keys, mutations, R2 (no polling except QA 1s job), role-gating, or business
logic. **Markdown only via the shared `<Markdown>` component** (the SOLE `dangerouslySetInnerHTML` sink —
do not add new sinks). Backend frozen (`git diff` outside `web/` empty). Keep `npm run build` +
`npm run lint --max-warnings 0` clean and **vitest (328) + e2e (20) green** — update tests/selectors to
match new DOM while preserving intent. RENDER screenshots (headless Chromium, `@playwright/test`,
`NO_PROXY=localhost,127.0.0.1`, login rm/rm at http://127.0.0.1:8000 after `npm run build`) and iterate;
delete scratch scripts (don't commit them; don't leave them in `web/` — they break lint).

## The punch-list (each item must be visibly fixed; verify with screenshots)

**A. App 工作台 — edit ("修改") mode: taller input fields.** The app-info text fields/textareas
(description, notes, test commands, doc fields, etc.) are too short for owners to write comfortably.
Make textareas comfortably taller (e.g. min-height ~120–160px, or auto-grow), inputs a touch taller.

**B. App 工作台 — browse mode: render fields as Markdown, with capped height.** In read/browse mode,
render the free-text fields (description / notes / docs / test results) as **Markdown** via the shared
`<Markdown>` component (NOT raw text, NOT a new sink). Wrap long content in a container with a sensible
**max-height + internal scroll** so one long field can't blow up the page height.

**C. App 工作台 — 测试命令 & 文档 fields: two-column (左右分栏).** Where it reads well, lay the test-command
and doc fields out in a left/right two-column layout (e.g. test commands beside docs, or label-left /
content-right) instead of one tall stack. Use your judgment for the cleanest split; keep it responsive.

**D. App 工作台 — left list rows: show blocking status at a glance.** Each app row in the left list must
make blockers obvious WITHOUT opening the app: show **doc status** (文档 OK / 文档待补 N) and **QA status**
(通过 / 存在问题 / 待测试 / 不可发布) as clear compact pills/indicators. e.g. one should immediately see
"lammps 存在问题" or "文档待补 10". Keep rows compact but informative.

**E. App 工作台 — left list rows: drop the redundant small version.** Line 1 currently shows the app name
AND a small version chip that duplicates it (e.g. "Amber 22" + small "v22"). Remove the redundant
small-text version when the name already carries it.

**F. 总览 (dashboard) — 编辑 / 删除 buttons side by side.** In the release-timeline table's 操作 column the
编辑 and 删除 buttons should sit **inline (并排)**, not stacked.

**G. 发布文档 (artifacts) — cards are the picker; remove the redundant button row; add outline nav.**
The 5 doc-kind cards are enough as the picker — **remove the redundant per-kind "查看 X" toolbar buttons.**
Keep genuinely-needed actions (刷新, 下载 test-scope.csv, Manager Review export) but relocate them tidily
(page-header actions / contextual to the open doc), not as a dense redundant row. AND: when a doc is
previewed there is **no outline/ToC nav** — add one (content + sticky outline sidebar, using the
`<Markdown onOutline>` you added earlier), with the right ratio (see H).

**H. 开发 WIKI — fix reader grid ratio.** The outline sidebar is too WIDE and the article content too
narrow. Content must be the wide column, outline a narrow sidebar (e.g. `grid-template-columns:
minmax(0,1fr) 240px`, content first). Apply the same sensible ratio to the artifacts reader (G).

**I. Spacing/margins audit (global).** Many elements are packed too tightly. Fix gaps/padding consistently.
Known offenders: the artifacts 5 cards touch each other (need grid `gap`); WIKI list header "共 7 篇" sits
right against "+ 新建文章" (need gap). Sweep buttons/cards/toolbars/headers for cramped gaps and give
comfortable, consistent spacing.

**J. Date/time format — global yyyy-mm-dd / hh:mm:ss, zero-padded.** All displayed dates →
`yyyy-mm-dd` (2-digit month & day), times → `hh:mm:ss` (2-digit). Centralize in `lib/time.ts`
(`formatServerTime` / `formatClientFetchTime`) and audit every timestamp display so nothing shows a
different or non-padded format. Keep the zero-offset rule (no +8). **Caveat:** native `<input type="date">`
widgets render their display per browser locale (the "mm/dd/yyyy" pickers) and can't be forced to
yyyy-mm-dd by CSS — normalize all TEXT timestamps; for the date inputs, leave them native (and note it) OR
only change if it doesn't break the existing value handlers. Flag what you did.

## Process & coordination
- `designer` owns all `web/` edits; build + screenshot every affected tab (App workbench edit AND browse,
  dashboard, artifacts landing + an opened doc, wiki reader) and iterate until each punch-list item is
  visibly right. Report to `team-lead` with screenshot paths + per-item (A–J) status.
- `reviewer` independently screenshots + checks each A–J item is done and looks good, plus gates
  (build/lint/vitest/e2e), no behavior/scope/frozen-file regressions, R2 + sole-Markdown-sink intact
  (grep dangerouslySetInnerHTML == 1). PASS or CHANGES NEEDED with screenshot-referenced findings.
- team-lead verifies with own screenshots and commits on PASS. ultrathink; the user is detail-sensitive.
