# Phase 2 — FastAPI backend: shared team brief

**Team:** `p2-fastapi`. Lead = `team-lead`. Implementers = `impl-1`, `impl-2`, `impl-3` (sonnet).
Reviewer = `reviewer` (opus). Branch: `rewrite/fastapi-react`.

Read this whole file before writing code. Then read the source files named for YOUR slice.

---

## 0. The one rule: Phase 2 is a FAITHFUL 1:1 PORT — byte-parity, not a redesign

The 38 golden files in `tests/golden/responses/*.json` were captured from the **current old
server** (`server.py` + `release_system/core.py`). Phase 2 reimplements those same 58 endpoints
on FastAPI so that **every replayed request scrub-equals its golden**. That is the acceptance gate.

Therefore:
- **Match `server.py` + `release_system/core.py` behavior EXACTLY**, including response shapes,
  status codes, error messages (Chinese text verbatim), envelope keys, ordering, and any quirks.
- **DO NOT implement the R3 ruling changes (A/B/C/D), decision↔status sync, the new endpoints
  (`/api/cicd/apps/new`, `/api/cicd/tasks/abandon`, `GET /api/releases`), or the V1/V3/C5 bug
  fixes.** Those are **Phase 4** and would break golden parity. Leave these stubs raising
  `NotImplementedError`: `cicd_service.sync_decision_to_cicd`, `abandon_task`,
  `cicd_first_new_app`. Port the *existing* CICD submit/approve logic (incl. current auto-approve)
  as-is from core.py.
- **DO NOT touch `server.py`, `release_system/`, `index.html`, or the repositories** — they are
  done/frozen. You implement only under `app/` (services, routers, integrations, deps, main) and
  add fixtures to `tests/conftest.py`. The repositories layer (`app/repositories/*`) is COMPLETE
  (Phase 1) — call it, don't rewrite it. The old system must still run unchanged.

When old behavior and the plan disagree → **old behavior wins** in Phase 2. Note any such conflict
in your completion message to team-lead; it's a Phase 4 item.

---

## 1. Established conventions (already in the skeleton — copy them)

- **Services** = module-level functions taking `conn: sqlite3.Connection` first, pure (no HTTP).
  They own orchestration + transaction boundaries + a single `ts` per operation
  (use `app.timeutil.beijing_timestamp()`). They call repositories + `release_system.core` helpers
  where a faithful port is easiest, but prefer the repositories already built in `app/repositories`.
- **Routers** = thin: parse body/query, `Depends(...)` auth, call ONE service fn, return its dict.
  Keep the exact path + method from `server.py` (see the route table in §3).
- **Errors** (`app/api/errors.py`, already written + `register_error_handlers`): raise
  `AuthzError`→403, `PermissionError`→401, `ValueError`/`RuntimeError`→400, else 500. The old
  server returns JSON `{"ok": false, "error": "..."}` on failure and various shapes on success —
  **match the old server's exact success/error JSON**, including HTTP status. If the old server
  returns 200 with `{"ok": false}` for a domain error, replicate THAT (don't convert to 4xx).
  Check `server.py` per-endpoint to see whether it uses status codes or in-body ok flags.
- **Stub signatures are contracts.** Honor the existing function signatures in
  `app/services/*` and `app/deps.py`. If you must change one, message the team FIRST so others
  aren't broken.
- **Timezone:** DB stores naive Beijing strings; pass through unchanged. New timestamps via
  `app.timeutil.beijing_timestamp()` → `"%Y-%m-%d %H:%M:%S"`. No UTC, no offset math.
- **Connections:** one per request via `get_db` (wraps `app.db.connection.connect(settings.db_path)`,
  closed in `finally`). Background QA threads open their OWN connection (see `server.py:229`), never
  reuse a request connection.
- Style: `from __future__ import annotations`, type hints, ruff-clean. Run
  `ruff check app/ tests/` before reporting done.

## 2. Auth / session model (port verbatim — see `server.py:1348-1430`)

- Cookie `hpc_session`; `HttpOnly; SameSite=Strict; Path=/`. PBKDF2-SHA256 120k.
  `sessions(token, username, created_at)`, no expiry. `secrets.token_urlsafe(32)`.
- `require_login`: read cookie → `sessions_repo` lookup → user dict `{username, role, ...}` or 401.
- `require_roles(*roles)`: 403 if role not allowed. `services/authz.py`:
  `require_owner_or_rm`, `require_app_audit_access` (need DB, so not plain Depends).
- LDAP two-step bind ported into `app/integrations/ldap.py`; `_LDAP_CONFIG` loaded once in
  `main.py` lifespan into `app.state`. `/api/login/ldap` + `/api/ldap/status` mirror old.
- **Do NOT add CSRF / Secure / session expiry** (out of scope).

---

## 3. Endpoint → owner map (58 endpoints). Paths are EXACT (from `server.py` do_GET@323 / do_POST@536)

### WAVE 1
**impl-1 — Foundation + Auth (do FIRST; others' routers depend on `deps.py`)**
- Infra: `app/deps.py` (get_db, require_login, require_roles), `app/main.py` (create_app:
  lifespan loads ldap conf + `init_db`, include ALL routers, `register_error_handlers`,
  mount `StaticFiles` LAST guarded so a missing `web_dist` doesn't crash startup),
  `app/integrations/ldap.py`, `app/services/auth_service.py`, `app/services/authz.py`.
- Routers: `POST /api/login`, `POST /api/login/ldap`, `POST /api/logout`, `GET /api/me`,
  `GET /api/ldap/status`.
- **conftest parity fixtures (the linchpin):** add `fastapi_base_url` + `fastapi_session_cookies`
  to `tests/conftest.py`. Boot the new app (uvicorn in a background thread, or httpx
  `ASGITransport` if you adapt the replay test) against a DB seeded **identically to
  `tests/golden/capture.py`** (read it — same users/CSV/CICD/wiki seed). `fastapi_session_cookies`
  = `{role: cookie_str}` for rm/owner/qa/admin via `/api/login`. Do NOT un-skip `test_fastapi_parity`
  yet (that's Wave 3) — just make the fixtures importable & green when used.

**impl-2 — Apps + State**
- `GET /api/state` (state service+router), `GET /api/app-audit`,
  `POST /api/apps/new`, `POST /api/apps/update` (snapshot + `release_decision`; keep the
  `cicd_sync` response key but its value can mirror old / be `{"created": false, ...}` no-op since
  sync is Phase 4 — match what the OLD server returns for this body today),
  `POST /api/app-info`, `POST /api/app-info/fetch`, `POST /api/app-info/fetch-all`.
- Files: `app/services/app_service.py`, `app/api/routers/apps.py`, `app/api/routers/state.py`.
  Keep snapshots a loose `dict` (do NOT add strict Pydantic field validation — DA finding P1).

**impl-3 — CICD (heaviest)**
- GET: `/api/cicd/tasks`, `/api/cicd/tasks/<id>/history`, `/api/cicd/requests`,
  `/api/cicd/notifications`, `/api/cicd/deliveries`.
- POST: `/api/cicd/requests/{submit,approve,reject,cancel,deliver,return-delivery,re-dispatch,
  apply-returned}`, `/api/cicd/tasks/{transfer-owner,delete}`, `/api/cicd/notifications/mark-visited`.
- Integrations: `app/integrations/jira.py`, `app/integrations/gerrit.py` (port from
  `release_system/jira_client.py` + gerrit subprocess in core; call via plain `def` route =
  threadpool; Jira after-commit, failure must NOT roll back approval).
- Files: `app/services/cicd_service.py`, `app/api/routers/cicd.py`.
- **Port the EXISTING approve/auto-approve flow from core.py as-is.** Do not enforce ruling A/B/C/D
  status-lock yet. Leave `sync_decision_to_cicd`/`abandon_task`/`cicd_first_new_app` =
  `NotImplementedError`.

### WAVE 2 (assigned after Wave 1 review passes)
- **impl-1 — Release + Schedule:** `POST /api/import-initial`, `/api/releases/create`,
  `/api/releases/deadlines`, `/api/releases/final-lock`, `/api/releases/final-unlock`,
  `/api/release-schedule/upsert`, `/api/release-schedule/delete`.
  (`release_service.py`, `releases.py` router, schedule via `schedule_repo`.)
- **impl-2 — QA:** `GET /api/qa/analyze-log/status`, `GET /api/qa-log/download`,
  `GET /api/qa-reports`, `POST /api/qa/{status-batch,upload-log,analyze-log,analyze-log/start}`.
  `QaJobRegistry` on `app.state`, `threading.Thread`, background thread opens its own conn.
  (`qa_service.py`, `qa_jobs.py`, `qa.py` router, `integrations/llm.py`.)
- **impl-3 — Artifacts + Wiki + Admin:** `GET /api/artifacts/<kind>`, `/api/test-scope.csv`,
  `POST /api/artifacts/{generate,manager-review}`, `/api/gerrit/plan`; wiki articles
  list/get/save/pin/delete + images upload/get; admin users / set-role / clear-db / apps/delete.
  (`artifact_service.py`, `wiki_service.py`, `admin_service.py` + their routers.)
  Artifacts/CSV return **plain text** (not JSON) — set the right media type; the parity test
  truncates these to 500 chars.

### WAVE 3 — Fidelity (all 3 + reviewer)
Un-skip `test_fastapi_parity` (remove the `@pytest.mark.skip`), run the full golden replay against
the new app, fix every diff to byte-parity, then `pytest` full suite green + `ruff check`.

---

## 4. The acceptance oracle (read `tests/golden/test_golden_replay.py`)

- 38 goldens carry replay metadata `_method/_path/_params/_role/_body`. `test_fastapi_parity`
  replays each via httpx against `fastapi_base_url`, scrubs (`tests/golden/scrub.py`), and asserts
  `scrub(live) == golden["body"]` AND `status == golden["status"]`.
- Login goldens are for cookie setup only (skipped in parity). Artifact/CSV goldens compare a
  500-char text preview, not JSON.
- **Self-check while building:** boot your endpoint, hit it with the same `_role` cookie + `_body`,
  scrub, diff vs the matching golden. Parity-relevant goldens for your slice — grep
  `tests/golden/responses/` by name (e.g. `get_state_*`, `get_cicd_*`, `post_apps_update_*`,
  `get_admin_users`, `get_wiki_*`, `get_qa_reports`, `get_artifact_*`).

## 5. Coordination

- Honor stub signatures; if you change a shared one (`deps.py`, a service fn another agent calls),
  SendMessage the team first.
- `impl-1` owns `deps.py` + `main.py` + `tests/conftest.py` — others: do NOT edit those; ask impl-1.
- Each agent: when your wave slice is done, run `ruff check app/` + the relevant tests, then
  SendMessage `team-lead` with: files changed, endpoints done, parity goldens you verified,
  anything you couldn't match (with the server.py line ref) and any Phase-4 conflicts you noticed.
- Keep going until your slice is complete and self-verified; don't stop half-done. ultrathink.
