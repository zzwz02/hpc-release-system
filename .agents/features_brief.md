# App-workbench features brief (R3 decision-sync + copy-from-version + unsaved guard)

**Team:** `p3-feat`. Lead = `team-lead`. `designer` (opus) implements, `reviewer` (opus) reviews.
Branch `rewrite/fastapi-react`. Three features (F1–F3) on the App 工作台, one of which changes
backend behavior.

## Ground rules
- **Do NOT modify `release_system/core.py`, `server.py`, or `index.html`** — the legacy system must stay
  runnable & frozen. Implement new R3 behavior in the **new `app/` layer** (services/domain/routers) and
  the React app under `web/`. (You MAY read core.py to mirror logic.)
- Keep gates green: backend `python3 -m pytest -q` (currently 572 passed / 4 skipped — the golden parity
  gate must stay green; add tests for new endpoints/logic), frontend `npm run build` + `npm run lint`
  (--max-warnings 0) + `npx vitest run` (currently 334) + `npm run test:e2e` (20). Update tests to match
  new DOM/behavior; don't gut coverage.
- R2 unchanged (no polling except QA 1s job). Markdown sole sink unchanged. Backend `app/` change must not
  break golden parity — verify no golden replays `sync_decision:true` (it shouldn't; decision_sync only
  appears when the request body sets it). Run the full suite to confirm.
- Server runs at http://127.0.0.1:8000 (single process, serves web_dist). Rebuild web (`cd web && npm run
  build`) to see frontend changes; restart uvicorn only for backend changes (kill by PID — `pkill -f`
  self-matches, see note). Login rm/rm. Render screenshots (headless Chromium, NO_PROXY=localhost,127.0.0.1).

---

## F1 — Owner-choice dialog for syncing release_decision to later releases (the image.png feature)

**Backend (new `app/` layer; keep core.py frozen):**
1. **New gating rule.** Reimplement the "sync decision to later releases" logic in the new layer (e.g. a
   function in `app/services/app_service.py` or a new `app/domain/decision_sync.py`), based on
   `core.sync_decision_to_later_releases` BUT with this change:
   - For each LATER release (by created_at) that has this app and is NOT locked:
     - If target decision is **`release`** AND that release is **past app-freeze OR past doc-deadline**
       → apply **`cicd_only`** to it (NOT skip, NOT release). Rationale: never add QA/test scope to a
       frozen release; cicd_only keeps it running without expanding scope.
     - Otherwise → apply the target decision verbatim (full sync — non-release changes never expand scope).
   - Locked releases → skipped (reason "已最终锁定").
   - App not present in a release → skipped (reason "本 release 无此 app").
   Have `app_service.update_snapshot` call THIS (not `core.sync_decision_to_later_releases`) when
   `body.sync_decision` is set. Preserve the audit entry + the `decision_sync` response shape
   ({applied:[...], skipped:[...]}), extended with the resulting decision per applied release.
2. **Preview endpoint** so the dialog can show the table before applying — add
   `POST /api/apps/decision-sync/preview` (router `apps.py`, service fn). Body: `{release_id, app_id,
   decision}`. Returns per later-release: `{release_id, release_name, phase_label, resulting_decision,
   skipped, reason?}` where phase_label ∈ {App 冻结前, App 冻结后, Doc deadline 后, 已最终锁定} (derive from
   the release's phase like the legacy phase machine). Dry-run only (no writes). Add service unit tests +
   a router/parity-style test.

**Frontend (`web/src/features/appWorkbench`):**
- When the user saves an app edit where `release_decision` changed AND there are later unlocked releases
  containing this app: show the **dialog from image.png** instead of the current sync checkbox:
  - Title: 同步 release 决策到后续 release?
  - Body: 你把 release 决策改为「{newDecision}」。是否把该决策同步到下列 {N} 个后续 release?
  - Table (from the preview endpoint): RELEASE | 阶段 | RELEASE 决策 (show "调整为 {resulting_decision}";
    show skipped rows greyed with their reason). Rows where resulting≠target (the gated→cicd_only ones)
    should be visually distinguishable so the owner sees the downgrade.
  - Buttons: **取消** (abort — do not change the decision at all) · **不同步，仅本 release** (save with
    sync_decision=false) · **同步到后续 release** (save with sync_decision=true).
- Remove the old sync_decision checkbox in favor of this dialog. If `release_decision` did NOT change, or
  there are no eligible later releases, save normally with no dialog.
- Keep it wired through the existing apps/update call + the shared selectedReleaseId.

## F2 — "从其他版本复制" (copy this app's info from another release) — edit mode

**Frontend only (reuse existing endpoints):**
- In edit mode, add a **从其他版本复制** button. Opens a small picker of OTHER releases (from the releases
  list). On pick: fetch that release's state (`GET /api/state?release_id=X`), read `snapshots[app_id]`, and
  copy the editable fields into the current edit form (description / doc target / doc fields / test_docs /
  community / sanity etc. — the owner-editable snapshot fields), marking the form dirty. If the target
  release lacks this app, show a friendly message. Confirm before overwriting if the form already has edits.
- No backend change (reuse /api/state). Don't copy identity/version/owner-confirmed/QA fields that
  shouldn't transfer — copy the doc/test content fields an owner would re-enter. Use judgment; mirror what
  fields the edit form exposes.

## F3 — Unsaved-changes guard in edit mode

**Frontend only:**
- When `dirty` (unsaved edits in edit mode):
  - **Browser refresh / close / navigate-away**: add a `beforeunload` handler so the browser shows its
    native "Leave site? Changes you made may not be saved" prompt. Remove the handler when not dirty.
  - **Switching app (clicking another app row)**: intercept and `window.confirm("有未保存的修改，确认放弃
    并切换 app?")`; only switch if confirmed.
  - **Switching tab (TabNav)**: intercept and confirm similarly ("有未保存的修改，确认放弃并离开?") before
    leaving the App 工作台; stay if cancelled. (Use react-router navigation blocking / a guard — don't break
    other tabs' navigation when not dirty.)
- The existing passive footnote can stay or be folded in. Keep the existing app_info-fetch dirty-confirm.

---

## Process & coordination
- `designer` implements all of F1–F3 (backend in `app/`, frontend in `web/`), self-verifies with the full
  test suites + screenshots (dialog showing the gated cicd_only case; copy-from-version; the unsaved
  guard prompts). Report per-feature status + screenshot paths + confirmation that pytest 572/vitest/e2e
  stay green and golden parity holds.
- `reviewer` independently verifies F1 rule correctness (esp. the upgrade-to-release-past-freeze →
  cicd_only gating, and that locked/absent releases are handled), the preview endpoint matches the apply
  logic, golden parity intact, no core.py/server.py/index.html edits, screenshots of all three flows,
  gates green. PASS or CHANGES NEEDED with specifics.
- team-lead verifies + commits per feature or as a batch. ultrathink; the decision-sync rule has edge
  cases — get them right.
