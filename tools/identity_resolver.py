"""Identity resolver — shared seam for CICD task ↔ App matching.

This module mirrors the algorithm defined in
``test_data/get_release_report_test_cmd.py`` (``normalize_git_url`` +
``resolve_manifest_url``) so that both ``tools/migrate_db.py`` and the
future ``app/domain/identity.py`` (owned by impl-backend-core) use the
same canonical logic.

**Coordination note (impl-backend-core)**: once ``app/domain/identity.py``
lands, the plan is to keep ONE canonical implementation.  The two options:
  (a) impl-backend-core imports from here, or
  (b) this module delegates to ``app.domain.identity``.
Recommend option (a) for Phase 1 (migration), then replace during Phase 2
(FastAPI backend) once the package is stable.  Please confirm the final
import path so ``migrate_db.py`` can be updated.

**Network requirement**: ``resolve_manifest_url`` shells out to
``git archive --remote=<MANIFEST_REPO_URL> master <path>.xml`` to parse the
manifest.  Migration must run on a machine that can reach
``sw-gerrit-devops.metax-internal.com:29418``.  The ``_manifest_cache``
dict provides an in-process cache (key = xml_path) to avoid redundant
fetches within a single migration run.

Usage::

    from tools.identity_resolver import repo_to_git_identity, same_identity

    url, branch = repo_to_git_identity("git", "hpc_hpl", "maca")
    # -> ("ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_hpl", "maca")

    url, branch = repo_to_git_identity("manifest", "APP/lammps/master/hpc_lammps.xml", "master")
    # -> resolved url + revision from manifest, or (None, None) if offline/error
"""

from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# Constants — mirror get_release_report_test_cmd.py exactly
# ---------------------------------------------------------------------------

RESOLVED_REPO_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC"
MANIFEST_REPO_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/manifest"
MANIFEST_BRANCH = "master"

# In-process cache for manifest resolutions.
# Key:   xml_path (the .xml path relative to the manifest repo root)
# Value: (resolved_url, resolved_branch) or (None, None) on failure
#
# TODO: for repeated migration runs across processes, consider a simple JSON
# file cache passed via --manifest-cache flag so network fetches are not
# repeated on re-runs.
_manifest_cache: dict[str, Tuple[Optional[str], Optional[str]]] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_absolute_git_url(value: str) -> bool:
    """Return True if *value* is already an absolute git URL or .xml manifest path."""
    return "://" in value or value.startswith("git@") or value.endswith(".xml")


def _git_archive_extract(remote: str, branch: str, path: str, dest_dir: str) -> bool:
    """Run ``git archive --remote=<remote> <branch> <path> | tar -x -C <dest_dir>``.

    Returns True on success, False (with a warning to stderr) on failure.

    TODO: expose a timeout parameter once wired into migrate_db --timeout flag.
    """
    cmd = (
        f"git archive --remote={shlex.quote(remote)} "
        f"{shlex.quote(branch)} {shlex.quote(path)} | "
        f"tar -x -C {shlex.quote(dest_dir)}"
    )
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print(f"  [warn] git archive failed: {result.stderr.strip()}")
        print(f"    remote={remote!r} branch={branch!r} path={path!r}")
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_git_url(git_url: str) -> str:
    """Convert a short repo name into the full Gerrit SSH URL.

    Short names (e.g. ``hpc_hpl``) are prefixed with ``RESOLVED_REPO_BASE``.
    Already-absolute URLs (``://``, ``git@``) and ``.xml`` paths are
    returned unchanged.

    Mirrors ``normalize_git_url`` in
    ``test_data/get_release_report_test_cmd.py``.
    """
    value = str(git_url or "").strip()
    if not value:
        return value
    if _is_absolute_git_url(value):
        return value
    return f"{RESOLVED_REPO_BASE}/{value.lstrip('/')}"


def resolve_manifest_url(
    git_url: str,
    git_branch: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a ``.xml`` manifest path to ``(repo_url, branch)``.

    If *git_url* does not end with ``.xml`` the inputs are returned as-is.

    On success returns the resolved ``(url, branch)`` of the project that
    contains ``<linkfile src="app_info.json"/>``.
    On failure (network error, missing project) returns ``(None, None)``
    and logs a warning; callers should skip/report this entry.

    Results are cached in ``_manifest_cache`` for the process lifetime.

    Mirrors ``resolve_manifest_url`` in
    ``test_data/get_release_report_test_cmd.py``.

    **Network requirement**: must reach ``sw-gerrit-devops.metax-internal.com:29418``.
    """
    if not git_url or not git_url.endswith(".xml"):
        return git_url, git_branch

    xml_path = git_url.lstrip("/")

    if xml_path in _manifest_cache:
        return _manifest_cache[xml_path]

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            if not _git_archive_extract(MANIFEST_REPO_URL, MANIFEST_BRANCH, xml_path, tmpdir):
                _manifest_cache[xml_path] = (None, None)
                return None, None

            xml_full = os.path.join(tmpdir, xml_path)
            if not os.path.exists(xml_full):
                print(
                    f"  [warn] manifest xml missing after extract: {xml_path} "
                    f"(not found in {MANIFEST_REPO_URL}@{MANIFEST_BRANCH})"
                )
                _manifest_cache[xml_path] = (None, None)
                return None, None

            root = ET.parse(xml_full).getroot()
            default_rev = ""
            default_elem = root.find("default")
            if default_elem is not None:
                default_rev = default_elem.get("revision", "")

            target_project = None
            for proj in root.findall("project"):
                for link in proj.findall("linkfile"):
                    if link.get("src") == "app_info.json":
                        target_project = proj
                        break
                if target_project is not None:
                    break

            if target_project is None:
                print(f"  [warn] no project with app_info.json linkfile in {xml_path}")
                _manifest_cache[xml_path] = (None, None)
                return None, None

            raw_name = target_project.get("name")
            raw_rev = target_project.get("revision")
            name = (raw_name or "").strip()
            revision = (raw_rev or default_rev or "").strip()

            resolved_url = f"{RESOLVED_REPO_BASE}/{name}"
            print(f"  [debug] resolved {xml_path} -> url={resolved_url!r} branch={revision!r}")
            result = (resolved_url, revision)
            _manifest_cache[xml_path] = result
            return result

    except Exception as exc:
        print(f"  [warn] error resolving manifest {git_url}: {exc}")
        _manifest_cache[xml_path] = (None, None)
        return None, None


def repo_to_git_identity(
    repo_type: str,
    repo_name: str,
    branch: str,
) -> Tuple[Optional[str], Optional[str]]:
    """Map ``(repo_type, repo_name, branch)`` → ``(git_url, git_branch)``.

    This is the single identity seam described in plan §4.2.

    repo_type == "git":
        ``repo_name`` is treated as a short name or full URL and normalised
        via ``normalize_git_url``.  branch is returned unchanged.

    repo_type == "manifest":
        ``repo_name`` must end with ``.xml`` (the Google-repo manifest path).
        The manifest is fetched and parsed to obtain ``(url, branch)``.
        Returns ``(None, None)`` if resolution fails.

    Any other repo_type:
        Falls through to normalize + pass-through (best-effort).

    **Must be called OUTSIDE an open DB write transaction** — manifest
    fetches can take several seconds and would hold the write lock.

    Coordinates with ``app/domain/identity.py`` (impl-backend-core): once
    that module lands, this function should delegate to it (or vice-versa)
    so there is exactly one canonical implementation.
    """
    if repo_type == "manifest" or (repo_name or "").endswith(".xml"):
        # Normalise the xml_path first, then resolve
        normalised = normalize_git_url(repo_name)  # .xml → pass-through by _is_absolute_git_url
        return resolve_manifest_url(normalised, branch)

    # git (or fallback for unknown types)
    return normalize_git_url(repo_name), branch


def same_identity(
    a_url: str,
    a_branch: str,
    b_url: str,
    b_branch: str,
) -> bool:
    """Return True iff (a_url, a_branch) and (b_url, b_branch) refer to the same repo+branch.

    Both sides are normalised through ``normalize_git_url`` before
    comparison so that a stored short name (``hpc_hpl``) matches a full
    URL (``ssh://...``) as described in plan §4.2 "规范化对齐".

    Branch comparison is case-sensitive (plan §4.2 / test_core assertion
    test_add_new_app_request_git_location_is_case_sensitive).

    Neither side should be ``None``; if either is, returns False.
    """
    if not a_url or not b_url:
        return False
    return (
        normalize_git_url(a_url) == normalize_git_url(b_url)
        and a_branch == b_branch
    )


def clear_manifest_cache() -> None:
    """Clear the in-process manifest cache.

    Useful for testing or when retrying with fresh network state.
    """
    _manifest_cache.clear()
