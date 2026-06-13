"""Git identity seam — replaceable mapping from repo info to (git_url, git_branch).

Algorithm per plan §4.2 (= normalize_git_url + resolve_manifest_url from
test_data/get_release_report_test_cmd.py):

  - Short repo name (e.g. 'hpc_hpl') → prepend HPC_GERRIT_PREFIX
  - Absolute URL (starts with '://' or 'git@') → pass through
  - '.xml' manifest path (Google-repo) → resolve via 'git archive --remote'
    from MANIFEST_REPO_URL@master, parse <project> with <linkfile src="app_info.json">

NOTE: manifest resolution involves network I/O.  Call this function OUTSIDE
any DB transaction to avoid holding write locks during slow/failing fetches.

# TODO Phase 1/2 — implement fully per §4.2
"""
from __future__ import annotations


def repo_to_git_identity(
    repo_type: str,
    repo_name: str,
    branch: str,
) -> tuple[str, str]:
    """Map (repo_type, repo_name, branch) to (git_url, git_branch).

    This is the ONLY place that converts repo info to a natural key.

    # TODO Phase 1 — implement normalize_git_url + resolve_manifest_url logic
    """
    raise NotImplementedError("repo_to_git_identity not yet implemented (Phase 1)")
