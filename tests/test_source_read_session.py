"""Request-owned connections, source freshness, failure isolation and bounded reads."""

import io
import json
import ssl
import subprocess
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app import identity
from app.config import settings
from app.integrations import gerrit, harbor, manifest_archive
from app.integrations.read_session import current_session, read_session


def test_duplicate_reads_share_success_or_failure_only_within_the_batch():
    calls = []

    def read():
        calls.append(len(calls) + 1)
        return calls[-1]

    def failed():
        calls.append("error")
        raise ValueError("missing source")

    with read_session() as batch, ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: batch.run(batch.memo, ("same",), read), range(8))) == [1] * 8
        for _ in range(2):
            with pytest.raises(ValueError, match="missing source"):
                batch.memo(("missing",), failed)
        assert batch.run(current_session) is batch
    assert current_session() is None
    with read_session() as batch:
        assert batch.memo(("same",), read) == 3
    assert calls == [1, "error", 3]


def test_harbor_and_archive_reuse_http_but_observe_new_content_next_batch(tmp_path, monkeypatch):
    config = tmp_path / "runtime.conf"
    config.write_text("[hpc_manifest_archive]\nBASE_URL=https://archive.test/bucket\n")
    monkeypatch.setattr(settings, "runtime_conf_path", config)
    digest = "sha256:" + "ab" * 32
    sha = "cd" * 20
    state = {"tag": "old", "missing": True}
    requests, clients = [], []
    real_client = httpx.Client

    def handler(request):
        assert "authorization" not in request.headers
        requests.append(request.url.host)
        if request.url.host == harbor.HARBOR_HOST:
            return httpx.Response(200, json={"digest": digest, "tags": [{"name": state["tag"]}]})
        assert request.url.host == "archive.test"
        return httpx.Response(
            404 if state["missing"] else 200,
            text=(
                f'<manifest><project name="hpc_demo" revision="{sha}">'
                '<linkfile src="app_info.json"/></project></manifest>'
            ),
        )

    def client(**kwargs):
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        result = real_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(result)
        return result

    monkeypatch.setattr(httpx, "Client", client)

    def archive():
        return manifest_archive.app_info_commit(
            "hpc_manifest_master/APP/demo/release/v_1.xml", gerrit.gerrit_remote_url("hpc_demo")
        )

    with read_session():
        assert harbor.get_artifact("hpc-release", "demo", digest)["tags"][0]["name"] == "old"
        for _ in range(2):
            with pytest.raises(manifest_archive.ArchiveNotFound):
                archive()
    state.update(tag="new", missing=False)
    with read_session():
        assert harbor.get_artifact("hpc-release", "demo", digest)["tags"][0]["name"] == "new"
        assert archive()["commit_id"] == sha
        assert archive()["commit_id"] == sha
    assert len(clients) == 2 and all(c.is_closed for c in clients)
    assert requests.count("archive.test") == 2
    assert requests.count(harbor.HARBOR_HOST) == 2


def _tar_file(content):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        payload = content.encode()
        info = tarfile.TarInfo("app_info.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


@pytest.fixture()
def ssh_starts(monkeypatch):
    starts, closes = [], []

    def start(self, path):
        starts.append((self.target, tuple(self.port_args), path))
        path.touch()

    def close(self):
        closes.append(self.target)
        for path in self.paths:
            path.unlink(missing_ok=True)

    monkeypatch.setattr(gerrit._ReadOrigin, "_start", start)
    monkeypatch.setattr(gerrit._ReadOrigin, "close", close)
    return starts, closes


def test_ssh_reuses_head_and_file_connections_without_mixing_accounts(
    tmp_path, monkeypatch, ssh_starts
):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        payload = (
            _tar_file('{"version":1}')
            if "archive" in args
            else ("a" * 40 + "\trefs/heads/main\n").encode()
        )
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr=b"")

    monkeypatch.setattr(gerrit, "_run_git", run)
    for _ in range(2):
        with read_session():
            for account in ["alice", "bob"]:
                remote = f"ssh://{account}@gerrit.example:29418/project"
                head = gerrit.head_commit(remote, "main", project_root=tmp_path)
                assert json.loads(
                    gerrit.fetch_app_info_at(remote, head, project_root=tmp_path)
                ) == {"version": 1}
                gerrit.fetch_app_info_at(remote, head, project_root=tmp_path)
    starts, closes = ssh_starts
    assert len(starts) == 8 and len(closes) == 4
    assert len(calls) == 8  # two sources, two operations, two fresh batches
    assert all(not path.exists() for _, _, path in starts)
    assert {target for target, _, _ in starts} == {"alice@gerrit.example", "bob@gerrit.example"}


def test_channels_are_bounded_and_different_commits_can_progress_together(
    tmp_path, monkeypatch, ssh_starts
):
    monkeypatch.setattr(gerrit, "READ_CHANNELS", 1)
    lock = threading.Lock()
    both_started, release = threading.Event(), threading.Event()
    active, maximum = [0], [0]

    def run(args, **kwargs):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            if active[0] == 2:
                both_started.set()
        try:
            assert release.wait(5)
            return subprocess.CompletedProcess(args, 0, stdout=_tar_file("{}"), stderr=b"")
        finally:
            with lock:
                active[0] -= 1

    monkeypatch.setattr(gerrit, "_run_git", run)
    with read_session() as batch, ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(
                batch.run,
                gerrit.fetch_app_info_at,
                "ssh://gerrit.example:29418/project",
                str(i),
                project_root=tmp_path,
            )
            for i in range(4)
        ]
        try:
            assert both_started.wait(5)
            assert maximum[0] == 2
        finally:
            release.set()
        assert all(f.result() == "{}" for f in futures)
    assert maximum[0] == 2


def test_manifest_batch_shares_one_snapshot_and_does_not_cache_between_batches(monkeypatch):
    paths = ["APP/a/default.xml", "APP/b/default.xml"]
    state, calls = {"revision": "old"}, []

    def fetch(url, branch, selected, **kwargs):
        calls.append(tuple(selected))
        return {
            path: (
                f'<manifest><project name="hpc_{path.split("/")[1]}" revision="{state["revision"]}"><linkfile src="app_info.json"/></project></manifest>'
            ).encode()
            for path in selected
        }

    monkeypatch.setattr(gerrit, "fetch_files_at", fetch)
    for revision in ["old", "new"]:
        state["revision"] = revision
        with read_session():
            identity.prefetch_manifests(paths)
            for path in paths:
                assert identity.resolve_manifest_url(path, "master", refresh=True)[1] == revision
    assert calls == [tuple(paths), tuple(paths)]


def test_missing_manifest_does_not_hide_the_other_app(monkeypatch):
    paths = ["APP/a/default.xml", "APP/missing/default.xml"]

    def fetch(url, branch, selected, **kwargs):
        if paths[1] in selected:
            raise subprocess.CalledProcessError(1, "git", stderr=b"pathspec did not match")
        return {
            paths[
                0
            ]: b'<manifest><project name="hpc_a" revision="main"><linkfile src="app_info.json"/></project></manifest>'
        }

    monkeypatch.setattr(gerrit, "fetch_files_at", fetch)
    with read_session():
        identity.prefetch_manifests(paths)
        assert identity.resolve_manifest_url(paths[0], "master", refresh=True)[1] == "main"
        assert identity.resolve_manifest_url(paths[1], "master", refresh=True) == (None, None)
