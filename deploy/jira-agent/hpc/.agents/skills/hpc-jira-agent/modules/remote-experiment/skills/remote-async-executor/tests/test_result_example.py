from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from executor_common import UserFacingError, validate_artifact_paths

spec = importlib.util.spec_from_file_location("result_example", SCRIPTS / "result-example.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArtifactSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="artifact-safety-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.job = self.root / "job"
        self.job.mkdir()
        self.source = self.root / "payload"
        self.source.write_bytes(b"original evidence\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()

    def test_literal_relative_paths(self):
        self.assertEqual(validate_artifact_paths(["reports/out.json", "数据/结果-1.txt"]), ["reports/out.json", "数据/结果-1.txt"])
        self.assertEqual(validate_artifact_paths(None), [])

    def test_rejects_ambiguous_paths(self):
        for value in ("/etc/passwd", "../outside", "a/../../outside", ".", "a/./b", "./file", "a//b", "a/", "", "a\\b", "C:/file", "*.txt", "x[0]", "x?", "a\nb", "a\x00b", "two words", "$(id)", "a;id"):
            with self.subTest(value=value), self.assertRaises(UserFacingError):
                validate_artifact_paths([value])

    def test_rejects_duplicates_types_and_file_directory_conflicts(self):
        for value in ("out.txt", {}, [None], [1], [True], ["x", "x"], ["x", "x/y"], ["x/y", "x"]):
            with self.subTest(value=value), self.assertRaises(UserFacingError):
                validate_artifact_paths(value)

    def test_publishes_nested_file_and_reuses_identical_bytes(self):
        for _ in range(2):
            digest = module.publish_file(self.job, "artifacts/nested/result.txt", self.source)
            self.assertEqual(digest, hashlib.sha256(self.source.read_bytes()).hexdigest())
        target = self.job / "artifacts/nested/result.txt"
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.job.rglob(".fetch-*")), [])

    def test_rejects_changed_existing_evidence(self):
        target = self.job / "stdout.log"
        target.write_bytes(b"keep old evidence")
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "stdout.log", self.source)
        self.assertEqual(target.read_bytes(), b"keep old evidence")
        self.assertEqual(list(self.job.glob(".fetch-*")), [])

    def test_rejects_root_symlink(self):
        alias = self.root / "alias"
        alias.symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(UserFacingError):
            module.publish_file(alias, "out.txt", self.source)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_rejects_parent_symlink(self):
        (self.job / "artifacts").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "artifacts/out.txt", self.source)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_rejects_final_symlinks_including_dangling(self):
        for exists in (False, True):
            with self.subTest(exists=exists):
                target = self.outside / str(exists)
                if exists:
                    target.write_bytes(self.source.read_bytes())
                (self.job / str(exists)).symlink_to(target)
                with self.assertRaises(UserFacingError):
                    module.publish_file(self.job, str(exists), self.source)
                self.assertEqual(target.exists(), exists)

    def test_cache_replacement_is_atomic_and_does_not_follow_symlink(self):
        module.publish_file(self.job, "result.cache.json", self.source, replace=True)
        self.source.write_bytes(b"new metadata")
        module.publish_file(self.job, "result.cache.json", self.source, replace=True)
        self.assertEqual((self.job / "result.cache.json").read_bytes(), b"new metadata")
        (self.job / "other.cache.json").symlink_to(self.source)
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "other.cache.json", self.source, replace=True)
        self.assertEqual(self.source.read_bytes(), b"new metadata")

    def test_rejects_non_regular_sources_and_targets_without_blocking(self):
        os.mkfifo(self.job / "pipe")
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "pipe", self.source)
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "out", self.job / "pipe")
        (self.job / "directory").mkdir()
        with self.assertRaises(UserFacingError):
            module.publish_file(self.job, "directory", self.source)

    def test_result_requires_matching_job_and_approved_paths(self):
        valid = {"job_id": "job", "artifacts": [{"path": "out.txt", "exists": True}]}
        self.assertEqual(module.result_artifacts(valid, "job", ["out.txt"]), valid["artifacts"])
        for payload in ([], {"job_id": "other"}, {"job_id": "job", "artifacts": {}}, {"job_id": "job", "artifacts": [1]}, {"job_id": "job", "artifacts": [{"path": "other", "exists": True}]}, {"job_id": "job", "artifacts": [{"path": "out.txt", "exists": "true"}]}, {"job_id": "job", "artifacts": []}):
            with self.subTest(payload=payload), self.assertRaises(UserFacingError):
                module.result_artifacts(payload, "job", ["out.txt"])


@unittest.skipUnless(all(shutil.which(tool) for tool in ("sshd", "ssh", "scp", "ssh-keygen", "realpath")), "Loopback integration requires OpenSSH server/client and realpath")
class ResultIntegrationTests(unittest.TestCase):
    """Actual CLI + SSH/SFTP against a temporary loopback-only daemon; no mocks."""

    @classmethod
    def setUpClass(cls):
        if os.geteuid() == 0 and not Path("/run/sshd").is_dir():
            raise unittest.SkipTest("sshd privilege-separation directory is absent; do not modify host configuration")
        cls.temporary = tempfile.TemporaryDirectory(prefix="result-loopback-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.user = pwd.getpwuid(os.getuid()).pw_name
        for name in ("host", "identity"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(cls.root / name)], check=True, capture_output=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            cls.port = listener.getsockname()[1]
        public = (cls.root / "host.pub").read_text().split()
        (cls.root / "known_hosts").write_text(f"[127.0.0.1]:{cls.port} {public[0]} {public[1]}\n")
        config = cls.root / "sshd_config"
        config.write_text("\n".join([
            f"Port {cls.port}", "ListenAddress 127.0.0.1", f"HostKey {cls.root / 'host'}",
            f"AuthorizedKeysFile {cls.root / 'identity.pub'}", f"PidFile {cls.root / 'pid'}",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "UsePAM no",
            # This temporary daemon uses a private 0700 directory under /tmp.
            # Do not change the host sshd or disable client host-key checking.
            "PermitRootLogin prohibit-password", "StrictModes no", "LogLevel VERBOSE",
            "Subsystem sftp internal-sftp", "AllowTcpForwarding no", "X11Forwarding no", "PermitTunnel no", "",
        ]))
        output = (cls.root / "sshd.log").open("wb")
        cls.addClassCleanup(output.close)
        cls.server = subprocess.Popen([shutil.which("sshd"), "-D", "-e", "-f", str(config)], stdout=output, stderr=output)
        cls.addClassCleanup(cls.stop)
        for _ in range(100):
            if cls.server.poll() is not None:
                raise RuntimeError("Temporary loopback sshd could not start")
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Temporary loopback sshd did not become ready")

    @classmethod
    def stop(cls):
        if cls.server.poll() is None:
            cls.server.terminate()
            cls.server.wait(timeout=5)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=self.root)
        self.addCleanup(self.temporary.cleanup)
        self.case = Path(self.temporary.name)
        self.workspace = self.case / "remote"
        self.workspace.mkdir()
        self.job_id = "20260911-120000-case-abc123"
        self.remote = self.workspace / ".remote-test-jobs/jobs" / self.job_id
        self.remote.mkdir(parents=True)
        self.local = self.case / "local"
        self.local.mkdir()
        for name in ("stdout.log", "stderr.log"):
            (self.remote / name).write_text(name + " evidence\n")

    def prepare(self, paths):
        config = {
            "execution": {"host": "127.0.0.1", "port": self.port, "user": self.user, "target": "host", "auth_mode": "ssh_key", "private_key_path": str(self.root / "identity"), "known_hosts_path": str(self.root / "known_hosts")},
            "sync": {"remote_workspace": str(self.workspace)}, "artifacts": paths,
        }
        path = self.case / "config.yaml"
        path.write_text(json.dumps(config))
        submit = {"job_id": self.job_id, "config_path": str(path), "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "host": "127.0.0.1", "port": self.port, "user": self.user, "execution_target": "host", "remote_workspace": str(self.workspace), "remote_job_dir": str(self.remote), "tmux_session": "rjob-" + self.job_id}
        (self.local / "submit.json").write_text(json.dumps(submit))
        payload = {"job_id": self.job_id, "state": "succeeded", "exit_code": 0, "summary": "fixture", "artifacts": [{"path": name, "exists": True} for name in paths]}
        (self.remote / "result.json").write_text(json.dumps(payload))
        return payload

    def collect(self, *flags):
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / "result-example.py"), "--job-dir", str(self.local), *flags], capture_output=True, text=True, timeout=30, env={**os.environ, "SSH_AUTH_SOCK": "", "PYTHONDONTWRITEBYTECODE": "1"})

    def cache(self):
        return json.loads((self.local / "result.cache.json").read_text())

    def test_downloads_system_logs_without_yaml_artifacts(self):
        self.prepare([])
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.cache()["collection"]["complete"])
        self.assertEqual(len(self.cache()["system_logs"]), 2)
        self.assertEqual((self.local / "stdout.log").read_bytes(), (self.remote / "stdout.log").read_bytes())

    def test_downloads_artifacts_and_retries_without_overwrite(self):
        self.prepare(["reports/out.txt"])
        (self.workspace / "reports").mkdir()
        (self.workspace / "reports/out.txt").write_bytes(b"verified report")
        for _ in range(2):
            result = self.collect()
            self.assertEqual(result.returncode, 0, result.stderr)
        artifact = self.cache()["artifacts"][0]
        self.assertTrue(artifact["downloaded"])
        self.assertEqual(artifact["sha256"], hashlib.sha256(b"verified report").hexdigest())
        (self.workspace / "reports/out.txt").write_bytes(b"changed")
        self.assertEqual(self.collect().returncode, 1)
        self.assertFalse(self.cache()["collection"]["complete"])
        self.assertEqual((self.local / "artifacts/reports/out.txt").read_bytes(), b"verified report")

    def test_rejects_unapproved_and_traversal_artifacts_before_downloading(self):
        for name in ("unapproved.txt", "../outside", "/tmp/escape"):
            with self.subTest(name=name):
                payload = self.prepare([])
                payload["artifacts"] = [{"path": name, "exists": True}]
                (self.remote / "result.json").write_text(json.dumps(payload))
                self.assertNotEqual(self.collect().returncode, 0)
                self.assertFalse((self.local / "stdout.log").exists())
                self.assertFalse((self.local / "artifacts").exists())

    def test_rejects_wrong_job_id(self):
        payload = self.prepare([])
        payload["job_id"] = "other-job"
        (self.remote / "result.json").write_text(json.dumps(payload))
        self.assertNotEqual(self.collect().returncode, 0)
        self.assertFalse((self.local / "result.cache.json").exists())

    def test_remote_symlink_cannot_escape_workspace(self):
        self.prepare(["out.txt"])
        (self.case / "outside.txt").write_bytes(b"not task evidence")
        (self.workspace / "out.txt").symlink_to(self.case / "outside.txt")
        self.assertEqual(self.collect().returncode, 1)
        self.assertFalse((self.local / "artifacts/out.txt").exists())
        self.assertFalse(self.cache()["collection"]["complete"])

    def test_remote_internal_symlink_is_resolved_within_workspace(self):
        self.prepare(["out.txt"])
        (self.workspace / "real.txt").write_bytes(b"task evidence")
        (self.workspace / "out.txt").symlink_to("real.txt")
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.local / "artifacts/out.txt").read_bytes(), b"task evidence")

    def test_local_artifacts_symlink_cannot_escape(self):
        self.prepare(["out.txt"])
        (self.workspace / "out.txt").write_bytes(b"task evidence")
        outside = self.case / "outside"
        outside.mkdir()
        (self.local / "artifacts").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.collect().returncode, 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_missing_system_log_reports_incomplete_collection(self):
        self.prepare([])
        (self.remote / "stderr.log").unlink()
        self.assertEqual(self.collect().returncode, 1)
        self.assertFalse(self.cache()["collection"]["complete"])

    def test_cache_symlink_does_not_overwrite_outside_file(self):
        self.prepare([])
        outside = self.case / "protected.txt"
        outside.write_bytes(b"keep")
        (self.local / "result.cache.json").symlink_to(outside)
        self.assertNotEqual(self.collect().returncode, 0)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_remote_result_symlink_cannot_read_outside_job(self):
        self.prepare([])
        result = self.remote / "result.json"
        outside = self.case / "other-result.json"
        result.rename(outside)
        result.symlink_to(outside)
        self.assertNotEqual(self.collect().returncode, 0)
        self.assertFalse((self.local / "result.cache.json").exists())

    def test_skip_artifacts_collects_logs_without_claiming_artifacts_downloaded(self):
        payload = self.prepare(["missing.txt"])
        payload["artifacts"][0].update({"downloaded": True, "sha256": "untrusted"})
        (self.remote / "result.json").write_text(json.dumps(payload))
        result = self.collect("--skip-artifacts")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.cache()["collection"]["artifacts_skipped"])
        self.assertFalse(self.cache()["artifacts"][0]["downloaded"])
        self.assertNotIn("sha256", self.cache()["artifacts"][0])
        self.assertFalse((self.local / "artifacts").exists())


if __name__ == "__main__":
    unittest.main()
