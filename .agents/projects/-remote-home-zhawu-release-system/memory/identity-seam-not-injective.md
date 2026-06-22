---
name: identity-seam-not-injective
description: The repo->git identity seam is NOT injective on real data; one app maps to two CICD tasks; CICD tasks are global (no release_id) but release_decision is per-release
metadata:
  type: project
---

Verified on the real release_system.db (106 cicd_tasks, 107 apps) for the App↔CICD identity seam `(git_url, git_branch)`:

- For `repo_type='git'` (98 tasks) the seam is pass-through: git_url=repo_name, git_branch=branch. For `repo_type='repo'` (8 tasks) repo_name is a manifest path (e.g. `APP/lammps/master/hpc_22Jul2025.xml`) needing the real (TBD) algorithm.
- **NOT injective**: CICD-0061 and CICD-0105 BOTH derive `(hpc_neuralgcm, maca)` → same app `neuralgcm`. Two active "Running" tasks, one app. `cicd_tasks.app_id` has no UNIQUE, so both backfill the same app — contradicts the implied 1:1 natural key.
- **2 git-type orphans, not 1**: CICD-0002 (hpc_abacus, maca-spin) and CICD-0068 (hpc_paddlecfd, maca) match no app. The data-access designer claimed only "paddlecfd" — undercounted.
- 96/106 exact seam hits (10 misses = 8 manifest + 2 git orphans). 12 apps have no matching CICD task.

**Consequence for R3**: `cicd_tasks` has NO release_id (it's global, one per app — verified). But `release_decision` lives per-snapshot/per-release (in data_json). So when release_decision drives the cicd_task status, "which release's decision wins for the one global task?" is unresolved. Today 0 apps diverge across releases (cross-release sync keeps them aligned, core.py:1872) but the model permits divergence. See [[timezone-storage-split]].
