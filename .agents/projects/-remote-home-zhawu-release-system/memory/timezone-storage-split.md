---
name: timezone-storage-split
description: How timestamps are stored (UTC vs naive-Beijing) across the release-system DB and how the frontend renders them
metadata:
  type: project
---

The release-system DB stores timestamps in TWO incompatible formats, which the Beijing-unification rewrite and its migration MUST reconcile carefully:

- **UTC ISO with `+00:00` offset** (from `core.now()` core.py:94 and `wiki/core.py:now()`): apps.created_at, audit.ts, releases.created_at/released_locked_at, release_schedule.created_at/updated_at, artifacts.generated_at, ALL cicd_task_requests.* timestamps, cicd_tasks.created_at/updated_at, sessions.created_at, wiki_articles.created_at/updated_at, wiki_images.created_at, AND embedded `synced_at` inside snapshots.data_json blob (core.py:1986).
- **Naive Beijing / date-only** (from `beijing_timestamp()` / `normalize_deadline()`): releases.app_freeze_deadline + doc_deadline (`YYYY-MM-DD HH:MM`), release_schedule.branch_cut_at + release_at (`YYYY-MM-DD` date only), qa_logs.uploaded_at (`YYYY-MM-DD HH:MM:SS`).

Frontend rendering today (index.html): `toBeijing()` (line 2001) parses UTC ISO and ADDS 8h; `qaLogUploadedAt()` (line 2010) DETECTS offset — if naive, passes through unchanged. So a "+8 formatter" only works on UTC-with-offset input; feeding it migrated naive-Beijing values double-shifts.

`current_phase` (core.py:161) depends ONLY on deadline columns (naive Beijing) vs `beijing_now()` — NOT on created_at — so phase derivation is robust to how created_at columns get rewritten. `_csv_filename_timestamp` (core.py:3374) already treats naive as Beijing, so it tolerates a UTC→naive migration. See [[identity-seam-not-injective]].
