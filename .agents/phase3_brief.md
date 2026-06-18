# Phase 3 — React/Vite/TS frontend: shared team brief

**Team:** `p3-frontend`. Lead = `team-lead`. Implementers = `impl-1`, `impl-2`, `impl-3` (sonnet).
Reviewer = `reviewer` (opus). Branch: `rewrite/fastapi-react`.

Read this whole file before writing code. Then read the legacy source for YOUR slice.

---

## 0. Goal & scope: faithfully reproduce today's `index.html` UI on React, against the FROZEN Phase-2 API

The legacy frontend is a single 5,240-line `index.html` (vanilla JS, ~25 globals, `marked`+`DOMPurify`
vendored). Phase 3 reimplements it as **React + Vite + TypeScript** under a new `web/` directory,
**reproducing current behavior** across the 8 tabs, talking to the **already-built, frozen Phase-2
FastAPI backend** (58 endpoints, byte-parity with the old server, committed at 74aeeac).

**IN scope (Phase 3):**
- All 8 tabs reproduced faithfully (see §3). Same data, same actions, same role-gating as `index.html`.
- **R2 — manual refresh, no polling**: each section refreshes only on (a) an explicit RefreshBar
  click, (b) navigation mount, (c) post-mutation targeted invalidation. Each section shows the
  **content's own data-fetch time** (not page-load time). The ONLY allowed interval is the QA
  AI-analysis job poll (1 s) while a job is running.
- Vitest + React Testing Library unit tests written ALONGSIDE each slice.
- The app must actually run: `npm run build` (tsc + vite) clean, lint clean, and the dev server
  works end-to-end against the live FastAPI backend.

**OUT of scope — deferred to Phase 4 (do NOT implement):**
- R3 ruling UX:裁定 A/B/C/D, moving "新增 app" entry into CICD, `CicdLinkCard`, decision↔status
  pending-approval flow, two-axis read-only CICD status, abandon. **Keep the CURRENT behavior**:
  "新增 app" stays in App 工作台; CICD tab reproduces today's submit/approve/etc. exactly.
- The new R2 per-section endpoints (`GET /api/releases`, `GET /api/releases/{id}`) are NOT built in
  the backend — use `GET /api/state` (optionally `?release_id=`) like the legacy app does. R2 still
  holds: manual refetch of `/api/state`, content-time display, no polling.
- Single-process StaticFiles deployment (FastAPI serving `web_dist`) is Phase 4 cutover. For Phase 3
  we run Vite dev server with an `/api` proxy to the backend.

When the legacy UI and the plan's R3 design disagree → **legacy behavior wins** in Phase 3. Note any
such case to team-lead as a Phase-4 item.

**Do NOT touch** `index.html`, `server.py`, `release_system/`, or `app/` (backend) — all frozen.
You work only under the new `web/` directory (+ may add a root-level note if needed). The old
`index.html` stays runnable.

---

## 1. Target stack & project layout (`web/`)

- **Vite + React 18 + TypeScript**, **TanStack Query** (server state), **zustand** (UI state — replaces
  the ~25 globals), **react-router** (tab routing). `marked` + `dompurify` as npm deps (not vendored).
  Keep deps lean; no UI-component framework unless trivially justified (match the plain look).
- Layout (plan §5.1):
  ```
  web/
    package.json  vite.config.ts  tsconfig.json  .eslintrc  vitest.config.ts  index.html
    src/
      main.tsx  App.tsx
      api/        # http client + typed endpoint wrappers + query keys
      types/      # TS interfaces mirroring every API response shape
      lib/        # time, csv, markdown, labels, roles, phase, identity
      hooks/      # useQuery wrappers per resource
      store/      # zustand uiStore (selectedApp, edit modes, filters, etc.)
      components/  # RefreshBar, DataTable, Markdown (sole sanitized-HTML sink), shared UI
      features/    # dashboard, releaseCycle(init), appWorkbench(apps), qa, artifacts, cicd, wiki, admin
      routes/      # routeConfig + Router + TabNav + RequireRole
  ```
- **R2 query defaults (critical):** configure the QueryClient with `staleTime: Infinity`,
  `refetchInterval: false`, `refetchOnWindowFocus: false`, `refetchOnReconnect: false`,
  `refetchOnMount: false` overridden per-mount as needed. Data moves ONLY via explicit `refetch()`,
  nav mount, or `invalidateQueries` after a mutation. NEVER add a polling interval except the QA job.
- **Auth:** cookie-based (`hpc_session`, HttpOnly — set by backend). The http client uses
  `credentials: 'include'`. A 401 response → clear user + show login (legacy `showLoggedOut`, index.html:1481).
- **Dev proxy + the environment proxy trap:** this box has `http_proxy`/`https_proxy` set that hijack
  localhost calls (returns 503 `Proxy-Connection: close`). Configure Vite's dev `server.proxy` for
  `/api` → `http://127.0.0.1:8000`, and ensure tests/dev bypass the system proxy for 127.0.0.1
  (set `no_proxy=localhost,127.0.0.1`, or use undici/axios with proxy disabled). The backend boots with:
  `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`.

## 2. Timezone (plan §5.4 — single contract, NO double offset)

Stored times are **naive Beijing** (`"YYYY-MM-DD HH:MM:SS"`); the backend sends them as-is. The frontend
displays them with **ZERO offset**. `lib/time.ts`:
- `formatServerTime(s)` = passthrough/normalize only (e.g. swap a `T` for a space) — **NO +8 math**.
- `formatClientFetchTime(epochMs)` = format a client-side `Date` (used ONLY by RefreshBar for the
  fetch moment).
- **DELETE the legacy `toBeijing()` +8 hack** (index.html:2001) — do not port it. (Legacy also had it.)
- Dev caveat: the current un-migrated `release_system.db` still has some UTC-ISO timestamps in a few
  columns, so a few values may look 8h off in dev. That is EXPECTED — production runs the migrated DB
  (all naive Beijing). Do not add offset logic to "fix" it. Flag to team-lead, don't work around.

## 3. The 8 tabs → owner map (legacy refs in `index.html`)

Tab nav: index.html:570-577. Role predicates: 1657-1665. Role chrome: 1769-1784. API wrapper: 1477-1489.
Refresh orchestration: 1589-1651. Render entry `render()`: 1751.

### WAVE 1 — Foundation + shell + shared libs + dashboard
**impl-1 — Project + shell + auth (BLOCKER; land scaffold early + signal peers)**
- `web/` scaffold (vite/ts/eslint/vitest config, index.html, main.tsx, App.tsx), QueryClient with R2
  defaults, `api/http` client (credentials include, 401→logout, JSON + error envelope handling matching
  backend `{"ok":false,"error":...}`), `routes/` (routeConfig, Router, TabNav with role-gated tabs,
  `RequireRole`), auth: login page (local + LDAP via `/api/ldap/status`, `/api/login`, `/api/login/ldap`),
  `/api/me` bootstrap, `/api/logout`, a `useAuth`/user context. Mirror legacy auth (index.html:5110-5237).
**impl-2 — types + lib + store**
- `types/` for every response shape (state payload `{apps,releases,release,artifacts,qa_log,
  qa_audit_logs,release_schedule,user_display_names,user}`, cicd tasks/requests/deliveries, qa reports,
  wiki, admin users, artifacts). `lib/`: time (§2), csv (BOM + `reportToCsv`, index.html:3447/2729),
  markdown (marked+DOMPurify pipeline incl. link target/rel + img loading, index.html:3607-3645),
  labels, roles (isRM/isOwner/isQA/isGuest/canEdit…, 1657-1665), phase (beforeAppFreeze/locked…,
  1671-1675), identity. `store/uiStore` (zustand) for the UI-only globals (selectedApp, edit/dirty
  modes, filters, sort) — list in §state map. Pure-TS, unit-tested.
**impl-3 — shared components + dashboard(总览) tab**
- `components/`: ⭐`RefreshBar` (shows "刷新于 <time>" from the query's `dataUpdatedAt`; explicit
  refetch button — content-level, per section), `DataTable`, `Markdown` (THE only sanitized-HTML sink).
  Then the **dashboard** tab (index.html:1802+): summary stats (app counts/doc/QA breakdown), schedule
  timeline (release-schedule upsert/delete), owner-scoped view. Data via `GET /api/state`.

### WAVE 2 — the 3 heaviest tabs (assigned after W1 review)
- **impl-1 — appWorkbench (App 工作台)** index.html:2038+: app list (search/owner-only), detail editor,
  `release_decision`, doc/test info, app-info upload/fetch (`/api/apps/new|update`, `/api/app-info[/fetch]`),
  app-audit (`/api/app-audit`). Keep snapshot edits faithful; "新增 app" stays HERE (not CICD).
- **impl-2 — cicd (CICD 工作台)** index.html:3889+/4723+: tasks overview (status filter), my requests,
  pending approvals, recent requests (since_days/only_mine), deliveries, actions submit/approve/reject/
  cancel/deliver/return-delivery/re-dispatch/apply-returned, task history, notifications badge +
  mark-visited. Reproduce TODAY's flow (incl. current auto-approve) — no R3 rulings.
- **impl-3 — qa (QA)** index.html:2575+: upload-log, AI analyze job + **1 s poll while running**
  (`/api/qa/analyze-log/start|status`, the ONLY allowed interval; cancel on unmount/release change),
  status-batch, qa-reports (filters/sort/column-pick/CSV export with BOM), qa-log download.

### WAVE 3 — remaining tabs
- **impl-1 — init (周期管理, RM only)** index.html:1983+/4989+: create release, import-initial CSV,
  deadlines, final-lock/unlock.
- **impl-2 — artifacts (发布文档)** index.html:3512+: 5 artifact kinds viewer (source/render toggle via
  the Markdown component), generate, manager-review save, test-scope.csv + manager_review CSV download.
- **impl-3 — wiki + admin** index.html:3692+/2017+: wiki article list/search, editor (markdown + image
  paste-upload), pin/delete; admin users/set-role, app delete, clear-db (confirm text + password), ldap status.

### WAVE 4 — integration, e2e, polish, cutover-readiness
All 3 + reviewer: cross-tab integration, Playwright key flows (login → each tab loads; app decision
save; cicd submit→approve; wiki save) — best-effort if browser install works, else document; live-backend
smoke (boot uvicorn, run dev server, exercise each tab); `npm run build`+lint+vitest all green.

## 4. Acceptance / how we verify (no golden oracle this phase)

1. `npm run build` (tsc strict + vite) succeeds; eslint clean; `vitest run` green.
2. Faithful behavior vs `index.html` per tab (reviewer cross-checks against the legacy refs above).
3. **Real smoke against the live backend** — boot `uvicorn app.main:app --port 8000 --workers 1`,
   run the dev server (proxy + no_proxy), log in, confirm every tab loads its data and key actions work.
   This is the strongest oracle — do it for your slice before reporting done.
4. R2 honored: no polling anywhere except the QA job; each section's RefreshBar shows its own fetch time.
5. No Phase-4/R3 behavior leaked in; timezone has zero double-offset.

## 5. Coordination

- `impl-1` owns the scaffold (package.json, vite/ts config, routes/, api/http, auth). Others: do NOT
  edit those; if you need a dep added or a client change, SendMessage impl-1.
- Honor the shared `types/` and `lib/` signatures from impl-2; if you need a new type/util, add it
  there (or ask) rather than duplicating.
- Each agent: write Vitest tests for your slice. When your wave slice is done, run
  `npm run build && npm run lint && npx vitest run` + a live-backend smoke of your tab, then SendMessage
  `team-lead`: files added, tabs/components done, tests added, smoke result, any legacy-vs-plan conflicts
  (Phase-4 notes), and anything you couldn't reproduce (with index.html line refs).
- Keep going until your slice is complete and self-verified; don't stop half-done. ultrathink.
