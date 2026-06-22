# Phase 3 — UI LAYOUT REDESIGN brief

**Team:** `p3-redesign`. Lead = `team-lead`. `designer` (opus) implements, `reviewer` (opus) reviews.
Branch: `rewrite/fastapi-react`. Work only under `web/`.

This is a **layout + visual redesign** of an already-working React/Vite/TS app (8 tabs, all
behavior done, backend frozen). The previous pass only recolored CSS; the USER rejected it. Read this
whole brief, then look at the real app before designing.

---

## 0. The user's actual complaints — these are the acceptance bar

1. **"信息量太少 / 不好看"** — too sparse, low information density, looks weak. The user now wants a
   **denser, information-rich** layout (NOT the airy "comfortable/friendly-cards" look). Think a clean,
   professional **data console**: compact rows, real tables, multi-column, lots of useful info per screen.
2. **"没有充分利用整页宽度"** — the layout wastes horizontal space. **Use the full viewport width**
   (drop the centered ~1380px cap; full-bleed with modest side padding, e.g. 20–24px). Lay data out in
   columns/tables that fill the width.
3. **App 工作台 is broken UX (TOP PRIORITY):** "我点一个 app 标签，结果要翻过 100+ 的 app 标签才能看到信息."
   Today the 107-app list is a single full-width column and the detail renders BELOW it, so selecting an
   app forces scrolling past all 107 rows. **Fix: a real master–detail layout** — a left list pane and a
   right detail pane side by side, **each scrolling independently**, so clicking an app shows its detail
   immediately on the right with the list still in view. The page itself must NOT be one giant scroll.
4. Design for: **信息易获取** (info easy to find), **易用性** (usability), **明了性** (clarity/hierarchy),
   **容易跳转** (easy navigation & cross-linking between related things).

If the redesign doesn't visibly fix all four, it fails review. Judge with your EYES (screenshots), not
just code — see §4.

---

## 1. Hard constraints (don't break these)

- **Layout/markup/CSS only.** You may restructure component JSX for layout (wrap list+detail in a grid,
  turn card-lists into tables, add sticky panes, add cross-link buttons) and rewrite `web/src/App.css`.
  Do **NOT** change data fetching, API calls, query keys, mutations, **R2 behavior** (no polling except
  the QA 1s job), the **sole Markdown sink** (only `Markdown.tsx` may `dangerouslySetInnerHTML`),
  role-gating, or any business logic. No R3 features. Backend (`app/`, `server.py`, `release_system/`,
  `index.html`) stays frozen — `git diff` outside `web/` must be empty.
- **Keep the app green:** `npm run build` + `npm run lint` (`--max-warnings 0`) clean. Vitest currently
  328 passing — if you restructure markup, UPDATE the affected RTL tests to match the new DOM while
  preserving their behavioral intent (don't delete coverage). The Playwright e2e (`web/e2e/smoke.spec.ts`,
  20 tests) must still pass — update selectors if you change structure, keep the flows.
- Cross-tab `selectedReleaseId` (uiStore) stays shared. Use `uiStore` for new cross-tab UI state
  (e.g. a `selectedAppId` for jump-to-app) rather than inventing parallel state.

## 2. Design direction (revised)

Clean, modern, **dense professional dashboard**. Blue accent is fine. Full-width. Prioritize legibility
of dense data: real tables with sticky headers, zebra/hover, compact row height; status as small colored
pills; clear section headers; sticky toolbars/filters; sensible empty states. Tasteful, not noisy.
You have design latitude on the visual details — but density, full-width, and the master–detail fix are
non-negotiable.

## 3. Per-tab layout requirements

- **Global shell:** full-width content; keep sticky top bar + tabs. Make long pages navigable (sticky
  section sub-nav / anchors / back-to-top where a page is long).
- **App 工作台 (priority):** master–detail. Left pane ~360–420px: sticky search + filters (own-only,
  decision/QA filters) + a **compact scrollable list** of the 107 apps (each row: name · version ·
  owner · decision pill · QA dot — info-dense, one glance). Right pane: the selected app's full detail,
  **independently scrollable**, visible without scrolling the list. No selection → a useful summary/empty
  state on the right. Both panes fit within the viewport height (independent `overflow:auto`).
- **总览 (dashboard):** keep a compact stat-tile row, but render the 107-app overview as a **dense
  sortable table** filling the width (not sparse cards). Schedule as a table. **每行可点击跳转**到 App
  工作台并选中该 app (set `uiStore.selectedAppId` + navigate) — this is a key 容易跳转 win.
- **CICD / QA / 发布文档 / 周期管理 / WIKI / 系统管理:** full width, dense tables, sticky toolbars/filters.
  Where a list+detail pattern helps (CICD task → history/detail; WIKI list → article), prefer
  master–detail or a side panel over making the user scroll. Artifacts/WIKI readers can use a content +
  sticky outline two-column layout that fills the width.
- **Cross-linking (容易跳转):** add obvious jumps between related entities — dashboard row → app detail;
  app detail → its CICD task (if any) on the CICD tab; keep the selected release in sync. Don't overbuild;
  hit the high-value links.

## 4. PROCESS — judge with screenshots, iterate (mandatory for visual work)

The backend is running at `http://127.0.0.1:8000` (single process serving `web_dist`). To SEE your work:
```
cd web && npm run build          # rebuilds web_dist that uvicorn :8000 serves
# (if backend not up:) python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```
Then render with headless Chromium (already installed) via a small node script using `@playwright/test`'s
`chromium`, with env `NO_PROXY=localhost,127.0.0.1` (this box's http_proxy hijacks localhost). Log in at
`http://127.0.0.1:8000` as **rm / rm**, set a wide viewport (e.g. 1600×1000), click each tab, and
`screenshot` to `/tmp/*.png`. On App 工作台, also click an app and screenshot to PROVE the detail shows
without scrolling past the list. Delete the scratch script when done (don't commit it).

- **designer:** implement → build → screenshot every tab → self-critique against §0 → iterate until it
  genuinely looks dense, uses full width, and the App-workbench master–detail works. Then report to
  team-lead with: files changed, the screenshot paths, and how each of the 4 complaints is addressed.
- **reviewer:** independently build + screenshot every tab and critique against §0 (density, full-width
  use, the App-workbench master–detail, 易获取/易用/明了/跳转) AND verify gates (build/lint/vitest/e2e),
  no behavior/scope/frozen-file regressions, R2 + sole-sink intact. Reply PASS or CHANGES NEEDED with
  specific, screenshot-referenced findings. Do not edit code; designer fixes.

## 5. Coordination
- `designer` owns all `web/` edits this round. `reviewer` reviews only.
- Report to `team-lead` (me). I verify (build/lint/test + my own screenshots) and commit on PASS.
- Keep going until it's genuinely good — the user has rejected one attempt already. ultrathink, use taste.
