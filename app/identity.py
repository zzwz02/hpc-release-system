"""Git identity seam — canonical mapping from repo info to (git_url, git_branch).

Algorithm per plan §4.2 (mirrors normalize_git_url + resolve_manifest_url from
test_data/get_release_report_test_cmd.py):

  - Short repo name (e.g. 'hpc_hpl') → prepend HPC Gerrit prefix
  - Absolute URL (starts with '://' or 'git@') or .xml path → pass through
  - '.xml' manifest path (Google-repo) → fetch from MANIFEST_REPO_URL@master,
    parse <project> with <linkfile src="app_info.json"/>, return (url, revision)

NOTE: manifest resolution involves network I/O via `git archive --remote`.
Call repo_to_git_identity() OUTSIDE any DB transaction to avoid holding write
locks during slow or failing fetches.

In-process manifest cache (_manifest_cache) avoids redundant fetches within
a single migrate run.  Call clear_manifest_cache() to reset between runs.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants — mirror test_data/get_release_report_test_cmd.py exactly
# ---------------------------------------------------------------------------

RESOLVED_REPO_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC"
MANIFEST_REPO_URL = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/manifest"
MANIFEST_BRANCH = "master"
MANIFEST_FETCH_TIMEOUT_SECONDS = 10

# In-process resolution cache.
# Key:   xml_path (stripped leading '/')
# Value: (resolved_url, resolved_branch) | (None, None) on failure
_manifest_cache: dict[str, tuple[str | None, str | None]] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_absolute_git_url(value: str) -> bool:
    """Return True if *value* is already an absolute URL, git@ remote, or .xml path."""
    return "://" in value or value.startswith("git@") or value.endswith(".xml")


def _git_archive_extract(remote: str, branch: str, path: str, dest_dir: str) -> bool:
    """Extract a single path from a remote git repo via `git archive | tar -x`.

    Returns True on success, False (with a printed warning) on failure.
    """
    cmd = (
        f"git archive --remote={shlex.quote(remote)} "
        f"{shlex.quote(branch)} {shlex.quote(path)} | "
        f"tar -x -C {shlex.quote(dest_dir)}"
    )
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        timeout=MANIFEST_FETCH_TIMEOUT_SECONDS,
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
    """Expand a short repo name to the full Gerrit SSH URL.

    Short names (e.g. 'hpc_hpl') are prefixed with RESOLVED_REPO_BASE.
    Absolute URLs, git@ remotes and .xml paths are returned unchanged.
    Empty strings are returned as-is.

    Mirrors normalize_git_url() in test_data/get_release_report_test_cmd.py.
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
) -> tuple[str | None, str | None]:
    """Resolve a .xml Google-repo manifest path to (repo_url, branch).

    If *git_url* does not end with '.xml' the inputs are returned unchanged.

    On success returns the (url, revision) of the project that contains
    <linkfile src="app_info.json"/>.  On failure returns (None, None) and
    prints a warning; callers must treat this as an unresolvable entry.

    Results are cached in _manifest_cache for the process lifetime.

    Mirrors resolve_manifest_url() in test_data/get_release_report_test_cmd.py.

    Requires network access to MANIFEST_REPO_URL (sw-gerrit-devops:29418).
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
            if raw_name != name or (raw_rev or "") != revision:
                print(
                    f"  [debug] manifest {xml_path}: stripped whitespace "
                    f"name {raw_name!r}->{name!r}, revision {raw_rev!r}->{revision!r}"
                )

            resolved_url = f"{RESOLVED_REPO_BASE}/{name}"
            print(f"  [debug] resolved {xml_path} -> url={resolved_url!r} branch={revision!r}")
            result: tuple[str | None, str | None] = (resolved_url, revision)
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
) -> tuple[str | None, str | None]:
    """Map (repo_type, repo_name, branch) → (git_url, git_branch).

    This is the ONLY place that converts repo info to a natural identity key.

    Dispatch is driven by repo_name SHAPE, NOT repo_type — mirroring the original
    test_data/get_release_report_test_cmd.py, which never branches on repo_type:
      - short name (e.g. 'hpc_hpl') → normalize_git_url → full Gerrit URL
      - '.xml' manifest path        → resolve_manifest_url fetches (url, revision)
      - absolute URL                → passthrough
    repo_type ('git' | 'repo' | 'manifest') is ADVISORY ONLY.  IMPORTANT: the real
    DB labels Google-repo manifest rows as repo_type='repo' (NOT 'manifest'), so
    gating manifest resolution on repo_type would orphan those 8 rows — dispatch
    must key on the '.xml' suffix instead (plan §4.2).

    Returns (None, None) only for an empty repo_name or a failed manifest
    resolution (caller treats as orphan).

    Must be called OUTSIDE an open write transaction — manifest fetches involve
    network I/O (git archive --remote) that can take several seconds.
    """
    if not (repo_name or "").strip():
        return None, None
    # normalize_git_url expands short names (and passes .xml/absolute through);
    # resolve_manifest_url resolves .xml manifests and is a no-op otherwise.
    return resolve_manifest_url(normalize_git_url(repo_name), branch)


def same_identity(
    a_url: str,
    a_branch: str,
    b_url: str,
    b_branch: str,
) -> bool:
    """Return True iff (a_url, a_branch) and (b_url, b_branch) refer to the same repo+branch.

    Both URLs are normalised through normalize_git_url() before comparison so
    that a stored short name ('hpc_hpl') matches a full URL as per plan §4.2
    '规范化对齐'.

    Branch comparison is case-sensitive (core.py:1618 uniqueness invariant;
    confirmed by test_add_new_app_request_git_location_is_case_sensitive).

    Returns False if either URL is None or empty.
    """
    if not a_url or not b_url:
        return False
    return normalize_git_url(a_url) == normalize_git_url(b_url) and a_branch == b_branch


def clear_manifest_cache() -> None:
    """Clear the in-process manifest resolution cache.

    Useful for testing or retrying with fresh network state.
    """
    _manifest_cache.clear()
