# Phase 0 — Golden Fixture Harness

This directory contains the golden fixture harness for the HPC Release System rewrite.

## Purpose

Phase 0's sole goal is **additive safety-net**: capture the CURRENT `server.py`'s `/api/*`
responses as golden files so Phase 2's new FastAPI backend can be diffed against them for
behaviour parity. Nothing in `server.py`, `release_system/`, or `index.html` is modified.

## Directory layout

```
tests/golden/
├── capture.py              # Standalone capture script (no pytest)
├── scrub.py                # Shared scrubber (non-deterministic field normaliser)
├── test_golden_replay.py   # pytest: Phase 0 validation + Phase 2 parity skeleton
├── README.md               # This file
└── responses/              # Written by capture.py, committed to git
    ├── get_ldap_status.json
    ├── get_me_rm.json
    ├── post_login_admin.json
    ├── ... (one file per captured endpoint)
```

## How to (re)capture

Run from the **repo root**:

```bash
python tests/golden/capture.py
```

What it does:
1. Creates an isolated SQLite DB in a temp directory (never touches `release_system.db`).
2. Seeds the DB with a small, deterministic dataset:
   - 3 apps (GoldenAmber, GoldenLammps, GoldenHpl), 1 release
   - 1 approved CICD task + 1 pending modify request
   - 1 wiki article
   - QA status on the first app
   - Artifacts generated (release_note, manual, ai4sci, data)
   - 1 release schedule entry
3. Launches `server.py` as a subprocess on an ephemeral port.
4. Logs in as admin, rm, owner_test, qa — captures session cookies.
5. Fires ~30 representative GET + POST `/api/*` requests.
6. Scrubs non-deterministic fields (timestamps, tokens) via `scrub.py`.
7. Writes each response as `tests/golden/responses/<name>.json`.

The resulting files are **committed to git** so the parity gate can run in CI
without a live server.

## Golden file format

Each `.json` file has this envelope:

```json
{
  "_golden_name": "get_state_rm",
  "_note": "Human-readable description of what this captures",
  "status": 200,
  "body": { ... scrubbed response body ... }
}
```

`status` is the HTTP status code. `body` is the parsed JSON (or a string
preview for CSV/text endpoints). All timestamps, tokens, and password hashes
are replaced by stable placeholder strings (`SCRUBBED_TIMESTAMP`,
`SCRUBBED_TOKEN`, `SCRUBBED_HASH`).

## Captured endpoints (Phase 0)

### Authentication
| File | Method | Path | Role |
|------|--------|------|------|
| `post_login_admin` | POST | `/api/login` | admin |
| `post_login_rm` | POST | `/api/login` | rm |
| `post_login_owner` | POST | `/api/login` | owner_test |
| `post_login_qa` | POST | `/api/login` | qa |
| `post_logout` | POST | `/api/logout` | rm |

### User / Session
| File | Method | Path | Role |
|------|--------|------|------|
| `get_me_unauthenticated` | GET | `/api/me` | — |
| `get_me_rm` | GET | `/api/me` | rm |
| `get_ldap_status` | GET | `/api/ldap/status` | — |

### Core state
| File | Method | Path | Role |
|------|--------|------|------|
| `get_state_rm` | GET | `/api/state?release_id=...` | rm |
| `get_state_owner` | GET | `/api/state?release_id=...` | owner_test |

### CICD workbench
| File | Method | Path | Role |
|------|--------|------|------|
| `get_cicd_tasks` | GET | `/api/cicd/tasks` | rm |
| `get_cicd_tasks_running` | GET | `/api/cicd/tasks?status=Running` | rm |
| `get_cicd_task_history` | GET | `/api/cicd/tasks/{id}/history` | rm |
| `get_cicd_requests_rm` | GET | `/api/cicd/requests` | rm |
| `get_cicd_requests_pending` | GET | `/api/cicd/requests?status=pending` | rm |
| `get_cicd_requests_owner_mine` | GET | `/api/cicd/requests?only_mine=1` | owner_test |
| `get_cicd_notifications_rm` | GET | `/api/cicd/notifications` | rm |
| `get_cicd_notifications_owner` | GET | `/api/cicd/notifications` | owner_test |
| `get_cicd_deliveries_rm` | GET | `/api/cicd/deliveries` | rm |
| `post_cicd_request_submit_owner` | POST | `/api/cicd/requests/submit` | owner_test |
| `post_cicd_mark_visited` | POST | `/api/cicd/notifications/mark-visited` | rm |

### QA
| File | Method | Path | Role |
|------|--------|------|------|
| `get_qa_reports` | GET | `/api/qa-reports?release_id=...` | rm |
| `post_qa_status_batch` | POST | `/api/qa/status-batch` | qa |

### Wiki
| File | Method | Path | Role |
|------|--------|------|------|
| `get_wiki_articles` | GET | `/api/wiki/articles` | rm |
| `get_wiki_article_by_id` | GET | `/api/wiki/articles/{id}` | rm |
| `get_wiki_article_404` | GET | `/api/wiki/articles/wiki_doesnotexist` | rm |

### Artifacts
| File | Method | Path | Role |
|------|--------|------|------|
| `get_artifact_release_note` | GET | `/api/artifacts/release_note?release_id=...` | rm |
| `get_artifact_manual` | GET | `/api/artifacts/manual?release_id=...` | rm |
| `get_artifact_ai4sci` | GET | `/api/artifacts/ai4sci?release_id=...` | rm |
| `get_artifact_data` | GET | `/api/artifacts/data?release_id=...` | rm |

### CSV exports
| File | Method | Path | Role |
|------|--------|------|------|
| `get_test_scope_csv` | GET | `/api/test-scope.csv?release_id=...` | rm |

### App workbench
| File | Method | Path | Role |
|------|--------|------|------|
| `get_app_audit` | GET | `/api/app-audit?app_id=...&release_id=...` | rm |
| `post_apps_update_decision` | POST | `/api/apps/update` | rm |
| `post_apps_update_doc` | POST | `/api/apps/update` | rm |

### Admin
| File | Method | Path | Role |
|------|--------|------|------|
| `get_admin_users` | GET | `/api/admin/users` | admin |

## How Phase 2 will use these goldens (parity gate)

In Phase 2, `test_golden_replay.py` will be extended with parametrised tests that:

1. Boot the new **FastAPI** backend against the **same seed DB** (via a `conftest.py`
   fixture from impl-testbase / impl-backend-core).
2. Re-issue each captured request using the request metadata stored in the golden
   (`_method`, `_path`, `_params`, `_role`, `_body` — fields to be added in Phase 2).
3. Scrub the live response with the same `scrub.py`.
4. Assert `live_scrubbed == golden["body"]`.

A test failure means a behavioural regression in the new backend. The parity gate
runs in CI after every Phase 2 commit.

## Endpoints that need special handling in Phase 2

| Endpoint | Reason |
|----------|--------|
| `/api/artifacts/{kind}` | Returns plain text/CSV, not JSON. Golden stores a truncated preview string; Phase 2 must compare the full text body, not JSON-parse it. |
| `/api/test-scope.csv` | Same as above: CSV response. |
| `/api/qa-log/download` | Binary download; requires a QA log file to be present. Not captured in Phase 0 (no log uploaded in seed). |
| `/api/app-info/fetch` | Requires live Gerrit SSH access; cannot be captured in an isolated test environment. |
| `/api/qa/analyze-log/start` | Starts a background LLM job; polling endpoint `/api/qa/analyze-log/status` is async. Requires LLM config. Not captured in Phase 0. |
| `/api/wiki/images/*` | Binary blob responses. Golden stores metadata only. |

## Running Phase 0 tests

```bash
# From repo root — tests only validate golden files on disk (no server needed)
pytest tests/golden/test_golden_replay.py -v

# Or with full test suite
pytest tests/ -v
```
