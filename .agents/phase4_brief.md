# Phase 4 — R3 联动 + 切换 (rulings A/B/C/D) — shared team brief

**Team:** `p4-r3`. Lead = `team-lead`. Implementers = `impl-1`, `impl-2`, `impl-3` (sonnet, ultrathink).
Reviewer = `reviewer` (opus, ultrathink). Branch: `rewrite/fastapi-react`.

**Authoritative spec = the plan** at `/remote_home/zhawu/.claude/plans/clever-swimming-quiche.md` —
read §3.5 (R3 orchestration), §3.7 (role audit / ruling C), §5.3 (frontend R3 UX), §4.3 (migration),
§6 (DA findings V1/V2/V3), §7 (Phase 4 + the e2e checklist). This brief organizes the work into waves;
where this brief and the plan agree, follow them; surface any conflict to team-lead.

---

## 0. THE BIG DIFFERENCE FROM PHASES 2–3: this phase CHANGES behavior on purpose

Phases 2–3 preserved byte-parity with the old server. **Phase 4 deliberately changes CICD/role/approval
behavior (rulings A/B/C/D).** Consequences:
- The golden replay gate (`tests/golden/test_fastapi_parity`) will now FAIL on the changed endpoints
  (e.g. cicd submit was auto-approved → now pending; admin had cicd access → now 403). **These goldens
  must be RE-BASELINED to the new, correct R3 output — NOT deleted, NOT skipped to hide a regression.**
  Process: confirm the new app's response is the intended R3 behavior, then update that golden's `body`
  (and `status`) to the new expected value, with a one-line note in the test/commit explaining the R3
  change. Endpoints R3 does NOT touch must stay at parity. The reviewer will reject lazy golden deletion.
- Add NEW unit/integration tests for every new rule (decision→status sync, abandon, status-lock,
  cicd-first, ruling-B pending, ruling-C role gating).

## 1. Ground rules (unchanged from prior phases)

- Implement in the **new layers only**: `app/` (services/routers/domain), `web/`, `tools/`, `tests/`.
  **Do NOT edit `release_system/core.py`, `server.py`, or `index.html`** — the legacy system stays a
  frozen reference (you MAY read it). All ruling changes go in `app/services/cicd_service.py`,
  `app/services/app_service.py`, `app/services/admin_service.py`, routers, `app/domain/`.
- Gates per wave: backend `python3 -m pytest -q` green (parity goldens re-baselined as above, new tests
  added); frontend `npm run build` + `npm run lint` (--max-warnings 0) + `npx vitest run` +
  `npm run test:e2e`. R2 unchanged (no polling except QA 1s job). Markdown sole sink unchanged.
- **DB SAFETY (Wave 4):** do NOT migrate or replace the live `release_system.db`. The migration runs on a
  COPY and produces a *candidate* migrated DB + a report; cutover is the user's decision.
- Server: http://127.0.0.1:8000 (single process, serves web_dist). Rebuild web to see FE changes; restart
  uvicorn for backend changes by **PID** (`pkill -f` self-matches — use `pgrep -f 'uvicorn [a]pp.main'`
  then `kill <pid>`). Screenshot FE with headless Chromium, `NO_PROXY=localhost,127.0.0.1`, login rm/rm.

## 2. Current stub state (verified)

`app/services/cicd_service.py`: `sync_decision_to_cicd`, `abandon_task`, `cicd_first_new_app` all raise
`NotImplementedError`; `submit_request`/`approve_request` still auto-approve (`is_auto = role in
CICD_APPROVER_ROLES`); `CICD_APPROVER_ROLES={RM,Admin}`, `CICD_CREATE_ROLES={Owner,RM,Admin}`. Routers
`cicd.py` already document `POST /api/cicd/apps/new` + `/api/cicd/tasks/abandon` (not wired). Frontend:
"新增 app" lives in App 工作台; no CicdLinkCard; CICD status editable.

---

## 3. WAVES (each wave: 3 impl in parallel by FILE OWNERSHIP + opus review). `cicd_service.py` is the
backend bottleneck, so ONE impl owns it per wave; the others do tests/goldens + frontend in parallel.

### WAVE 1 — Rulings B (no auto-approve) + C (Admin out of CICD/release)
- **impl-1 — backend cicd_service B+C** (sole owner of cicd_service.py this wave):
  - **B**: remove auto-approve. ALL submit → `status="pending"`, no reviewer/reviewed_at set at submit.
    Approval happens only via `approve_request` by an RM (RM may approve own request → set
    `is_self_approved=1`, keep audit). Delete the `is_auto` path (DA finding V1). Keep approval_mode
    (immediate / dispatch_spd) on APPROVE. (plan §3.5 deliveries, §6 V1; memory cicd-role-model-rulings-bc.)
  - **C**: `CICD_CREATE_ROLES={Owner,RM}`, `CICD_APPROVER_ROLES={RM}`, deliver set `{SPD,RM}`, deliveries
    filter `{SPD,RM,Owner}`; replace ~35 "RM/Admin" error strings with the ruling-C wording; Admin has NO
    CICD create/approve/deliver. Admin RETAINS: user/role mgmt, clear-db, global delete app, app-audit
    read-only (those are admin_service/admin router — leave). (plan §3.7, §6 V2.)
- **impl-2 — goldens + tests for B/C**: re-baseline the cicd submit/approve/notifications/requests goldens
  to the new pending/role behavior (verify correctness with impl-1), add pytest for: submit→pending (no
  auto-approve), RM approves (incl. self-approve flag), Owner/RM create only, Admin 403 on cicd
  create/approve/deliver. Coordinate expected shapes with impl-1.
- **impl-3 — frontend B/C role-gating**: all request rows show "等待 RM 审批" (no "提交即生效"); ApproveDialog
  keeps immediate/dispatch_spd + shows RM self-approve ("本人提交"); remove Admin from any CICD
  create/approve UI; Admin user sees ONLY 系统管理 tab and is redirected to /admin after login. (plan §5.3 C.)

### WAVE 2 — Ruling D (decision↔CICD status) + Ruling A (abandon) + two-axis read-only (V3)
- **impl-1 — backend cicd_service + app_service**:
  - **D**: implement `sync_decision_to_cicd(conn, app_id, new_decision, ...)` — locate the app's single
    task via `tasks_for_app`; map release/cicd_only→`Running`, stopped→`Stopped` (uppercase, align
    CICD_STATUSES); if target == current status → no-op; else create a **pending** `modify {status:{old,new}}`
    request with `origin="release_decision_sync"`. Wire it into `app_service.update_snapshot` INSIDE the
    existing txn after the snapshot save, when release_decision changed (alongside the F1 later-release
    sync). Phase-gated: if the decision write was blocked by phase gating, no sync request. (plan §3.5 b.)
  - **A — abandon**: implement `abandon_task` (RM-only direct action, ONLY on `Stopped` tasks → `Abandoned`,
    terminal) + wire `POST /api/cicd/tasks/abandon`. (plan §3.5 c, §6.)
  - **status-lock / two-axis (V3)**: CICD modify requests must REJECT a `status` field
    (`CICD_TASK_MUTABLE_FIELDS` excludes status); only decision-sync + abandon write status. (plan §3.5,
    §6 V3.)
- **impl-2 — goldens + tests for D/A/status-lock**: tests for decision change → exactly ONE pending modify
  request (origin sync) with correct status mapping + idempotency; abandon only-on-Stopped → Abandoned,
  RM-only; modify with status field → rejected.
- **impl-3 — frontend A/D**: **CicdLinkCard** on AppDetail top (read-only run/stop Pill, identity
  `{git_url}@{git_branch}`, "查看 CICD 任务 #id", pending-approval banner, note "运行/停止由本 app 决策决定;
  构建配置在 CICD 工作台改"); decision change shows "待审批: 将变为 停止/运行"; CICD workbench run/stop status is
  READ-ONLY; the only status action is RM "废弃/退役" on Stopped tasks. (plan §5.3 CicdLinkCard / A / D.)

### WAVE 3 — CICD-first build app (§3.5 a) + 1:1 + frontend new-app-in-CICD
- **impl-1 — backend**: `cicd_first_new_app` + `POST /api/cicd/apps/new` (body has NO git_url/branch —
  derive identity via `app/identity.py` repo seam, OUTSIDE the write txn). Reuse the single dedup gate
  (`find_by_identity` = unique (git_url,git_branch) + id alloc + current/future forward-sync). One outer
  `transaction` wrapping app + snapshot(initial `cicd_only`) + **pending** create request; the cicd_task
  row lands only on RM approval (ruling B). 1:1: derived identity hits an existing app → associate if that
  app is a CICD-less orphan, else reject "该 app 已有 CICD 任务". (plan §3.5 a, §4.2 identity, 基数裁定.)
- **impl-2 — tests + full golden re-baseline pass**: cicd-first happy path (create→approve→app+task
  exist, app starts cicd_only/Running), 1:1 collision (associate vs reject), identity derivation; plus a
  full pass to confirm every golden is either at parity or a justified R3 re-baseline.
- **impl-3 — frontend MERGE (per the user's revised direction — overrides the plan's §5.3 "move new-app
  to CICD")**: since App↔CICD is now 1:1, surface CICD inside the App workbench AND restrict the standalone
  CICD tab to RM/SPD.
  1. **App 工作台 detail → two sub-tabs**: `文档信息` (the existing detail editor, unchanged) + `CICD` (this
     app's single linked task: status read-only/decision-driven, repo/build config, its requests + history;
     owner-readable). Reuse the existing CICD components; match app↔task by repo_name+branch pre-migration
     (same heuristic as the Wave-2 CicdLinkCard). The CicdLinkCard summary can fold into this sub-tab.
  2. **Top-level CICD 工作台 tab → RM/SPD ONLY** (hide from Owner & Guest — `routeConfig.ts` roles, plus the
     AppRouter runtime guard). It stays the cross-app processing view (pending approvals / deliveries /
     all-tasks overview) for RM/SPD.
  3. **App creation stays in App 工作台** (Owner/RM visible — NOT in the RM/SPD-only CICD tab), wired to the
     **CICD-first** endpoint `POST /api/cicd/apps/new` (derive identity, pending create request → task on RM
     approval). Keep a direct `/api/apps/new` path as the RM escape hatch if needed.

### WAVE 4 — Migration (R1, on a COPY) + end-to-end R3 verification (fake app_info) + derived-identity display + cutover readiness
USER SCOPE ADJUSTMENTS (this wave): (i) **No Gerrit network here — fabricate fake app_info JSON** to exercise
the fetch/create/decision-sync chain in tests/e2e (injectable shim, NOT a default production path). (ii)
**Skip the manifest→gerrit network resolution** — the 8 `.xml` manifest apps stay reported-as-pending; don't
invest in making manifest resolution robust. (iii) **Surface the derived Gerrit URL + branch in the new-app
wizard** so the user can debug the repo→gerrit mapping at real deployment.

- **impl-1 — migration + backend identity surfacing (on a COPY, never the live DB)**:
  - Run `tools/migrate_db.py` against a COPY of `release_system.db` (do a `--dry-run` first for the report,
    then the real run on the copy) → candidate migrated DB + a report (link/derive/D-1/1:1/orphan counts per
    §4.3; expected offline: 96 link / 2 orphan / 12 derive / 5 D-1). The 8 `.xml` manifests need
    `sw-gerrit-devops:29418` which is unreachable — they stay unlinked + DOCUMENTED as pending-network (per
    user: manifest resolution skipped for now). Beijing-time per-column conversion + all the §4.3 validations
    (row counts, `PRAGMA foreign_key_check` empty, JSON parseable, app_id linked/derived/orphan self-consistent
    + no UNIQUE conflict, reopen + read paths don't throw). Do NOT touch the live DB.
  - **fetch-preview: return the derived identity even when the gerrit CONTENT fetch fails.** Restructure so
    `(git_url, git_branch)` is derived FIRST and returned regardless of whether the app_info content fetch
    succeeds. For **git-type** the derivation is offline (short name → full ssh URL) → always returned here.
    For **repo-type** the manifest resolution needs network → return what it can + a "needs-network" flag.
    So the response always carries the derived identity (when derivable) + the app_info fields only when the
    content fetch worked. Provide an **injectable fake-app_info fetcher** (e.g. `_fetch_fn`) so tests/e2e can
    feed realistic fabricated app_info.
- **impl-2 — end-to-end R3 verification on the MIGRATED DB, using fabricated app_info** (plan §7): boot a
  test server against a COPY of the migrated candidate DB and drive the full chain with a fake-gerrit shim:
  CICD-first 建 app (fetch fake app_info → confirm 7 fields → create with app_info + owner_confirmed) →
  改决策 release/stopped → 生成 pending 审批项 → RM 审批 → **任务状态随动** (this now works because migration
  backfilled app_id); 停止只能经 App; R2 无轮询; 权限(Admin 仅系统管理 / RM 唯一审批可自批 / SPD 链路);
  时区北京无双偏移. Add tests for fetch-preview returning identity-on-content-failure + the fake-app_info path.
- **impl-3 — new-app wizard: show derived Gerrit URL + branch + cutover readiness**: at the fetch step,
  prominently DISPLAY the derived `git_url` @ `git_branch` (git-type: the direct full URL; repo-type: the
  converted result, or "需联网解析" when unresolved) — so the mapping is debuggable at deploy even when the
  content fetch 502s. Keep the graceful error/skip path. Final gates green (build/lint/vitest/e2e); write the
  **cutover runbook** (backup → dry-run → run migration → validate → switch DB → start single-process).

## 4. Coordination
- Per wave: impl-1 owns `app/services/cicd_service.py` (+ app_service/routers as noted); impl-2 owns
  `tests/` (goldens + pytest); impl-3 owns `web/`. This avoids same-file collisions. If you need a shared
  signature changed, SendMessage the team first.
- When your wave slice is done, run the relevant gates + (frontend) screenshots, then SendMessage
  `team-lead` with: files changed, what ruling/endpoint is done, which goldens you re-baselined and why,
  new tests added, and any plan conflicts. team-lead pings `reviewer` per wave; reviewer verifies rule
  correctness + golden re-baseline legitimacy + gates + screenshots, replies PASS / CHANGES NEEDED.
- Keep going until your slice is genuinely complete. ultrathink — rulings have edge cases; get them right.
