---
name: release-system-dev
description: Development standards for the HPC App 发布信息协作系统 (FastAPI + React rewrite). Use whenever extending/changing this repo — new endpoints, UI, migrations, or fixes — so work follows the same architecture, quality gates, and review discipline. Triggers when the task touches app/, web/, tools/, tests/, or asks to "add/change/fix a feature" in this release system.
---

# HPC 发布系统 — Development Standards

This repo is a **FastAPI + React/Vite/TS rewrite** of a legacy single-file system. Follow these project
conventions for every change unless the user explicitly overrides them.

## 0. Read the repo docs first
- Before changing code in this repo, read `README.md` for the current architecture, roles, release lifecycle,
  CICD/App business rules, API map, test commands, and frozen-file policy. Treat `README.md` as the compact
  product/system orientation and keep this skill focused on execution discipline.
- For frontend work, also read `web/README-web.md`. For golden/parity work, also read `tests/golden/README.md`.
- If a behavior change modifies a rule documented in `README.md`, update the README in the same change so future
  agents load the correct context through this skill.

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
- **Identity mapping** = `app/identity.py` (`repo_to_git_identity`, short repo name → full ssh URL;
  `.xml` manifest → networked resolve).
- **Deploy** = single process: `python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1`
  (serves both API and the built SPA). `--workers 1` is REQUIRED (in-process QA job registry + LDAP state).

## 2. Legacy reference files — read-only
`release_system/core.py`, `server.py`, `index.html`. Implement ALL new behavior in `app/` + `web/` + `tools/`.
A change is only done if `git diff` shows ZERO modifications to those three. You MAY read them as a runnable
parity reference, but do not edit them.

## 3. Optional multi-agent workflow
Use a team only when the user explicitly asks for a multi-agent workflow. For normal tasks, one agent should
work directly.
- Split parallel work by file ownership to avoid collisions: backend, tests, frontend/docs, or another clear
  ownership boundary.
- Use one review gate per batch: implementers report, a lead verifies quality gates/frozen-file/no-junk checks,
  reviewers inspect correctness, and fixes land before any checkpoint commit.
- Reviewer reviews; implementers fix. Review must check behavior, tests, and rendered UI when frontend output is
  affected.

## 4. Quality gates (must be green before any commit)
- Backend: `python3 -m pytest -q` (run from repo root). Frontend (in `web/`): `npm run build` (tsc strict +
  vite), `npm run lint` (`--max-warnings 0`), `npx vitest run`, `npm run test:e2e` (Playwright).
- **Frozen-file guard**: `git diff --name-only -- server.py release_system/ index.html` must be empty.
- **No junk committed**: never stage `node_modules/`, `web_dist/`, `*.db*`, `*.bak`, `web/e2e/screenshots/`,
  `playwright-report/`, the `/tmp` candidate DBs, or scratch `*.mjs`/`ss_*.spec.ts`. Stage explicit paths.

## 5. Hard invariants
- **Refresh/data-fetch policy**: no polling. TanStack Query uses `staleTime: Infinity` with automatic
  refetch disabled; refresh only through explicit refetch/invalidate actions. The only allowed interval is
  the QA AI-analysis 1s poll. Each section's `RefreshBar` shows its OWN content fetch time
  (`dataUpdatedAt`), not page-load time.
- **Sole Markdown sink**: only `web/src/components/Markdown.tsx` may use `dangerouslySetInnerHTML`
  (DOMPurify pipeline). `grep -r dangerouslySetInnerHTML web/src` must show exactly one real hit.
- **Timezone**: stored + displayed times are **naive Beijing** `"%Y-%m-%d %H:%M:%S"`, zero offset. No `+8`
  math, no UTC `+00:00` in the (migrated) DB. (One documented exception historically: wiki — keep columns
  uniform if you touch them.)
- **Date inputs**: app-facing date-only fields (deadlines, release schedule dates, etc.) display and submit
  `YYYY-MM-DD`. Use shared `DateInput` + `formatDateValue`; never expose a visible raw browser
  `input[type=date]`.
- **CICD/App lifecycle rules**: CICD cutover is complete and **app-backed**. `cicd_task_requests.app_id`
  links directly to `apps.id`; `task_id` stores the same app id for the existing API field. Do not generate
  `CICD-xxxx` ids. FastAPI runtime must not read/write `cicd_tasks`; that legacy table may exist only so old
  DBs and frozen reference tests open cleanly. New code must use app id for identity; `(git_url, git_branch)`
  is only for historical display/compatibility matching, and never match by Gerrit URL alone because branches
  may share one URL. All CICD requests require pending→RM approval (RM may self-approve,
  `is_self_approved`); user modify requests may NOT set `status`. Admin is out of CICD/release business. App
  `release_decision` drives CICD Running/Stopped via pending modify requests
  (`origin="release_decision_sync"`). Running-boundary decision changes must sync to every unlocked release,
  not just later releases. `stopped -> release/cicd_only` is a running upgrade: the current release decision is
  deferred until CICD delivery and synced release decisions roll back if the request is rejected/cancelled.
  `release/cicd_only -> stopped` is a stop downgrade: the release decision takes effect immediately and the
  CICD request cannot be rejected or cancelled. CICD-first create starts snapshots as `stopped`; rejected or
  cancelled create requests leave the app visible with the reason, block duplicate `(git_url, branch)` creates,
  and only allow same-name retry. New CICD modify requests are blocked while the same app has an unfinished
  CICD-first create request, or an unfinished Jira-backed modify delivery (`delivery_status` pending/returned);
  no Jira issue is auto-cancelled. No-Jira pending modify requests may be replaced only with explicit
  `replace_open=true` after the UI warns that old requests will be cancelled. Any Running/Stopped boundary
  sync uses the same blockers and must not create `release_decision_sync` or change the snapshot when blocked.
  RM can reject a returned delivery through the `reject-returned` endpoint only with a reason, preserving Jira
  and return history and without applying the payload. CICD has no Abandoned/delete flow; retire/delete is
  handled through App lifecycle. CICD 工作台 is read-only; CICD config changes enter from App 工作台 → CICD tab.

## 6. Regression fixtures / golden responses
- `tests/golden/` contains captured expected API responses replayed by `test_fastapi_parity`. Behavior-preserving
  changes should keep these fixtures unchanged.
- For an intentional behavior change: **re-baseline the affected fixture to the new, verified-correct body —
  NEVER delete or `@skip` a fixture to hide a regression.** Add new fixtures for new endpoints. Reviewers must
  reject lazy fixture changes. Timestamps are scrubbed to `SCRUBBED_TIMESTAMP`; fields like `origin`/`app_id`
  are not scrubbed and do change response shapes.

## 7. Frontend/backend contract drift
Unit tests pass on each side while the frontend sends or reads the wrong keys. Catch this by verifying the
contract across the API boundary:
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
## 9. Commits
Branch `rewrite/fastapi-react`. Conventional, descriptive (Chinese summaries are fine, matching the repo).
End commit messages with:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## 10. Key files to read first
Start with `README.md`. For frontend work read `web/README-web.md`; for golden response work read
`tests/golden/README.md`. Historical design reference:
`/remote_home/zhawu/.claude/plans/clever-swimming-quiche.md`.
