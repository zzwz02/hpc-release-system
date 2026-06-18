---
name: release-system-dev
description: Standards and multi-agent workflow for developing the HPC App 发布信息协作系统 (FastAPI + React rewrite). Use whenever extending/changing this repo — new endpoints, UI, rulings, migrations, or fixes — so work follows the same architecture, quality gates, and review discipline established across Phases 0–4. Triggers when the task touches app/, web/, tools/, tests/, or asks to "add/change/fix a feature" in this release system.
---

# HPC 发布系统 — Development Standards

This repo is a **FastAPI + React/Vite/TS rewrite** of a legacy single-file system. Follow these standards
for every change. They were established across Phases 0–4 and are non-negotiable unless the user overrides.

## 1. Architecture & where code goes
- **Backend** = `app/`: thin routers (`app/api/routers/`) → services (`app/services/`, module-level functions
  taking `conn` first, owning orchestration + transaction boundaries + a single `ts` per op via
  `app.timeutil.beijing_timestamp()`) → repositories (`app/repositories/`, SQL only) → `app/db/connection.py`
  (`ManagedConnection`, WAL, nested-savepoint `transaction()`). Pure logic in `app/domain/`. `app/main.py`
  wires routers + lifespan + a guarded `StaticFiles` SPA mount (serves `web_dist`, deep-link fallback to
  index.html for non-`/api` paths).
- **Frontend** = `web/` (React 18 + Vite + TS + TanStack Query + zustand + react-router): `api/` (typed http
  client), `types/`, `lib/` (time/csv/markdown/roles/phase/identity), `store/uiStore` (zustand), `components/`
  (incl. `RefreshBar`, `DataTable`, and `Markdown` — THE single sanitized-HTML sink), `features/` (8 tabs),
  `routes/` (routeConfig + RequireRole + AppRouter).
- **DB** = single `release_system.db` (SQLite WAL). Backup = stop server + copy, or `sqlite3 … ".backup"`.
- **Migration tool** = `tools/migrate_db.py` (R1). Identity seam = `app/identity.py` (`repo_to_git_identity`,
  short repo name → full ssh URL; `.xml` manifest → networked resolve).
- **Deploy** = single process: `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`
  (serves both API and the built SPA). `--workers 1` is REQUIRED (in-process QA job registry + LDAP state).

## 2. FROZEN — never edit (legacy reference, kept runnable + the golden-parity oracle)
`release_system/core.py`, `server.py`, `index.html`. Implement ALL new behavior in `app/` + `web/` + `tools/`.
A change is only done if `git diff` shows ZERO modifications to those three. (You MAY read them to mirror logic.)

## 3. The multi-agent wave workflow (how to execute non-trivial work)
ONLY with explicit user opt-in to a team. Decompose the work into **waves**; per wave run **3 sonnet
implementers in parallel + 1 opus reviewer** (the user's standard team shape). Key rules:
- **Split each wave by FILE OWNERSHIP to avoid collisions** — typically: impl-1 = backend (`app/`, the
  bottleneck file `cicd_service.py` etc., one owner per wave) · impl-2 = `tests/` (incl. goldens) · impl-3 =
  `web/` + docs. Sequence work on a shared bottleneck file across waves rather than splitting it within a wave.
- **One review gate per wave**: implementers report → team-lead verifies gates itself → pings the opus
  reviewer → route fixes → **commit a checkpoint per wave** (with the frozen-file guard + no-junk check).
- Reviewer **reviews; implementers fix.** Reviewer must verify rules + golden legitimacy + gates + (frontend)
  rendered screenshots — NOT code-only.
- Use `ultrathink` in prompts (max reasoning). Retire the team (shutdown_request → TeamDelete) when done.

## 4. Quality gates (must be green before any commit)
- Backend: `python3 -m pytest -q` (run from repo root). Frontend (in `web/`): `npm run build` (tsc strict +
  vite), `npm run lint` (`--max-warnings 0`), `npx vitest run`, `npm run test:e2e` (Playwright).
- **Frozen-file guard**: `git diff --name-only -- server.py release_system/ index.html` must be empty.
- **No junk committed**: never stage `node_modules/`, `web_dist/`, `*.db*`, `*.bak`, `web/e2e/screenshots/`,
  `playwright-report/`, the `/tmp` candidate DBs, or scratch `*.mjs`/`ss_*.spec.ts`. Stage explicit paths.

## 5. Hard invariants
- **R2 — no polling.** TanStack Query `staleTime: Infinity`, all refetch flags false. The ONLY allowed
  interval is the QA AI-analysis 1s poll (cancels on unmount + release change). Each section's `RefreshBar`
  shows its OWN content fetch time (`dataUpdatedAt`), not page-load time.
- **Sole Markdown sink**: only `web/src/components/Markdown.tsx` may use `dangerouslySetInnerHTML`
  (DOMPurify pipeline). `grep -r dangerouslySetInnerHTML web/src` must show exactly one real hit.
- **Timezone**: stored + displayed times are **naive Beijing** `"%Y-%m-%d %H:%M:%S"`, zero offset. No `+8`
  math, no UTC `+00:00` in the (migrated) DB. (One documented exception historically: wiki — keep columns
  uniform if you touch them.)
- **R3 rulings** (CICD↔App): CICD is now **app-backed**. `cicd_task_requests.app_id` links directly to
  `apps.id`; `task_id` is only a compatibility alias and should resolve to the app id.  Prefer app id for
  identity, and only fall back to `(git_url, git_branch)` for legacy matching — never match by Gerrit URL
  alone because different branches may share one URL. **B** all CICD requests are pending→RM approval (no
  auto-approve; RM may self-approve, `is_self_approved`); **C** Admin is out of all CICD/release business
  (only user/role mgmt, clear-db, global delete-app, audit-read; CICD tab is RM/SPD-only); **D** App
  `release_decision` drives CICD task status (release/cicd_only→Running, stopped→Stopped) via a pending
  modify request (`origin="release_decision_sync"`); **A** status-lock: user modify requests may NOT set
  `status`. CICD has no `Abandoned` state and no CICD-side abandon/delete/retire operation; retire/delete is
  handled through App lifecycle. CICD 工作台 is for read-only CICD info, recent requests, approval, and delivery;
  CICD config changes enter from App 工作台 → CICD tab.

## 6. Golden / test discipline
- `tests/golden/` replays captured responses against the new app (`test_fastapi_parity`). The parity seed uses
  the OLD frozen core, so behavior-preserving work keeps goldens unchanged.
- For an INTENTIONAL behavior change: **re-baseline the affected golden to the new, verified-correct body —
  NEVER delete or `@skip` a golden to hide a regression.** Add NEW goldens for new endpoints. The reviewer's
  #1 job is rejecting lazy golden changes. (Note: timestamps are scrubbed to `SCRUBBED_TIMESTAMP`, so tz
  changes often need no golden change; fields like `origin`/`app_id` are NOT scrubbed and DO change shapes.)

## 7. The recurring bug class: FE↔BE seam drift
Unit tests pass on each side while the FE sends/reads the wrong keys. Caught only by crossing the seam:
- Verify the **live** round-trip (boot the server, actually submit), not just dialog-open / mocked shapes.
- Keep the Vitest mock shaped EXACTLY like the real backend response; assert the real payload keys.
- Examples that bit us: FE sent `app_name` while BE required `official_name`; FE read `version`/`app_info`
  while BE returned `app_version`/`parsed`/`app_info_parsed`. Always reconcile field names against the BE.

## 8. Environment caveats (this dev box)
- `http_proxy`/`https_proxy` hijack localhost → set `no_proxy=localhost,127.0.0.1` (npm scripts already do;
  for curl use `--noproxy '*'`). `pkill -f 'uvicorn app.main'` self-matches the shell — kill by PID (use
  `pgrep -f 'uvicorn [a]pp.main'`) and don't put the start command in the same shell line.
- **No Gerrit network** (`sw-gerrit-devops:29418` unreachable): mock app_info for tests
  (`make_fake_app_info_fetch`); the `.xml` manifest resolution + new-app Gerrit fetch only work on a networked
  deploy (surface the derived identity in the UI for debugging).
- **Migrations run on a COPY** — never mutate the live `release_system.db`; produce a candidate DB + report;
  the real cutover is the user's action (see `CUTOVER.md`).

## 9. Commits
Branch `rewrite/fastapi-react`. Conventional, descriptive (Chinese summaries are fine, matching the repo).
End commit messages with:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## 10. Key files to read first
Plan of record: `/remote_home/zhawu/.claude/plans/clever-swimming-quiche.md` (§3.5 R3, §3.7 rulings, §4.3
migration, §5 frontend, §7 phasing). Run/deploy: `README.md`, `web/README-web.md`, `CUTOVER.md`.
