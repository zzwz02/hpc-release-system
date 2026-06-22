# Follow-ups: wiki→Beijing (F1) + expose origin "同步联动" (F3) + docs rewrite

**Team:** `p4-fu`. Lead = `team-lead`. 3 sonnet implementers + 1 opus reviewer. Branch `rewrite/fastapi-react`.
(team-lead is separately authoring a dev-standards skill — not your concern.)

Ground rules unchanged from Phase 4: implement in the **new layers** (`app/`, `web/`, `tools/`, `tests/`);
**do NOT edit `release_system/core.py`, `server.py`, `index.html`** (frozen legacy reference). Keep gates
green: `python3 -m pytest -q`, `npm run build`, `npm run lint --max-warnings 0`, `npx vitest run`,
`npm run test:e2e`. R2 (no polling except QA 1s job) + sole Markdown sink (only Markdown.tsx) unchanged.
**Golden discipline:** re-baseline ONLY for the intentional behavior change, with the new body verified
correct — never delete/skip to hide a regression. Env caveats: http_proxy hijacks localhost → set
`no_proxy=localhost,127.0.0.1`; Gerrit unreachable → mock app_info; migration runs on a COPY, never live DB.

## F1 — wiki timestamps full-site Beijing
Today every displayed timestamp is naive Beijing EXCEPT `wiki_articles.created_at`/`updated_at` (still UTC),
because the wiki write path stamps UTC. Goal: make wiki timestamps naive Beijing too, **as a matched pair**
so the column never becomes mixed-format:
- **New-app write path (impl-1, in `app/`):** make the new app's wiki save (`app/services/wiki_service.py`)
  write `created_at`/`updated_at` as **naive Beijing** (`app.timeutil.beijing_timestamp()`), NOT UTC.
  PREFER doing this at the app layer WITHOUT editing the frozen `release_system/wiki/core.py` (e.g. set/override
  the timestamp in the service, or pass it in). If that's genuinely impossible cleanly, STOP and flag it to
  team-lead rather than editing frozen core.
- **Migration (impl-1, `tools/migrate_db.py`):** add `wiki_articles.created_at`/`updated_at` to the UTC→Beijing
  per-column conversion list, so existing wiki rows convert too (paired with the write change → uniform column).
- **Goldens (impl-2):** the wiki goldens scrub timestamps (SCRUBBED_TIMESTAMP), so the parity body likely does
  NOT change — VERIFY that's true (if the scrubber masks it, no re-baseline needed; if not, re-baseline to the
  Beijing value). Add a test asserting new wiki writes are naive Beijing (no `+00:00`/`Z`/`T`).

## F3 — expose `origin` + "同步联动" label
`cicd_task_requests.origin` distinguishes `cicd_workbench` (build-config) from `release_decision_sync`
(decision→status auto requests). It's currently stripped from the API (`_REQUEST_STRIP={"origin"}` in
`app/services/cicd_service.py`).
- **Backend (impl-1):** remove `origin` from `_REQUEST_STRIP` so request objects returned by
  `/api/cicd/requests` (and the other request read/return paths) include `origin`. (additive field)
- **Goldens (impl-2):** the cicd-requests goldens (get_cicd_requests_*, history, etc.) now gain an `origin`
  field → **re-baseline them** to include it (verify the value is correct: existing parity-seed requests are
  `cicd_workbench`). This is a legitimate additive re-baseline. Add a test that a decision-sync request carries
  `origin="release_decision_sync"` through the API.
- **Frontend (impl-3):** in the CICD requests list (RecentPane/PendingPane/DetailDialog as appropriate), show a
  small **"同步联动"** badge on requests with `origin === "release_decision_sync"` (vs build-config requests).
  Add a vitest for the badge.

## Docs rewrite (impl-3)
The top-level `README.md` still describes the OLD single-file system. Rewrite the docs to reflect the finished
FastAPI + React system through Phase 4:
- **`README.md`** (top-level): new architecture (FastAPI `app/` + repositories + React/Vite/TS `web/`, SQLite
  WAL, single-process uvicorn serving `web_dist` + `/api`), how to run (dev + single-process; the `no_proxy`
  caveat; `--workers 1` requirement), the R1–R3 + rulings A/B/C/D behavior, roles, and pointers to
  `CUTOVER.md` + `web/README-web.md`.
- **Flow diagrams**: produce **mermaid** diagrams (renderable in markdown) for (a) overall architecture, (b) the
  release lifecycle / phase machine (before_app_freeze → after_app_freeze → after_doc_deadline → locked), and
  (c) the **R3 CICD↔App flow** (App decision → pending CICD modify request → RM approve → task status;
  CICD-first create; abandon). The legacy `release_system_state_machine.svg` is stale — either supersede it with
  the mermaid lifecycle diagram (and note the SVG is legacy) or regenerate it. Keep diagrams accurate to the code.

## Process
File ownership: impl-1 = `app/` + `tools/`; impl-2 = `tests/` (incl. goldens); impl-3 = `web/` + docs. Coordinate
shapes. When done: run your gates (+ FE screenshots for the badge), SendMessage team-lead with files, what
re-baselined + why, tests added. team-lead → reviewer (verify wiki-Beijing no-mixed-format, origin exposure +
label, golden legitimacy, docs accuracy, gates, frozen guard). ultrathink.
