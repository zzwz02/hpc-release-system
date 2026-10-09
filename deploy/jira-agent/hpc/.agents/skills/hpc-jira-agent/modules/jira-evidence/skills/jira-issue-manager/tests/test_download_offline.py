"""Run the shipped downloader against a loopback HTTP server; no Jira credentials."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_attachment.py"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        if self.headers.get("Authorization") != "Bearer offline-fixture-only":
            self.send_error(401)
            return
        route = urlsplit(self.path)
        if route.path.startswith("/rest/api/2/issue/"):
            body = json.dumps({"fields": {"attachment": [self.server.meta]}}).encode()
            code, mime = 200, "application/json"
        else:
            body, code, mime = self.server.body, self.server.code, self.server.mime
            if self.server.mode.startswith("login"):
                code = self.server.after if "os_authType" in parse_qs(route.query) else 302
            if self.server.mode == "external":
                code = 302
        self.send_response(code)
        self.send_header("Content-Type", mime)
        if code == 302:
            self.send_header("Location", "http://127.0.0.1:1/login.jsp?secret=MUST-NOT-LEAK"
                             if self.server.mode == "external" else "/login.jsp?secret=MUST-NOT-LEAK")
        length = len(body) + (5 if self.server.mode == "truncated" and not route.path.startswith("/rest/") else 0)
        self.send_header("Content-Length", str(length))
        self.end_headers()
        self.wfile.write(body)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jira-download-test-")
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.server.body = b"source line\n"
        self.server.code, self.server.mime, self.server.mode, self.server.after = 200, "text/plain", "normal", 404
        self.server.calls = []
        self.server.meta = {"id": "7", "filename": "file.cu", "size": len(self.server.body),
                            "mimeType": "application/cu-seeme", "content": self.origin + "/secure/attachment/7/file.cu"}
        token = self.root / "token"
        token.write_text("offline-fixture-only")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        self.env.update(JIRA_TOKEN_FILE=str(token), JIRA_BASE_URL=self.origin, JIRA_ALLOWED_ORIGIN=self.origin,
                        NO_PROXY="127.0.0.1", no_proxy="127.0.0.1", PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def runcli(self, *args):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--issue-id", "TEST-1",
                                 "--url", self.server.meta["content"], "--output-dir", str(self.root / "downloads"),
                                 "--timeout", "2", *args], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotIn("offline-fixture-only", result.stdout + result.stderr)
        self.assertNotIn("MUST-NOT-LEAK", result.stdout + result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if packet["success"] else 1)
        return packet

    def rejected(self, code, *args):
        packet = self.runcli(*args)
        self.assertFalse(packet["success"])
        self.assertEqual(packet["error_code"], code)
        self.assertEqual(packet["download"]["status"], "not_fetched")
        self.assertFalse(list((self.root / "downloads").rglob("*.*")))
        return packet

    def test_original_url_success_receipt_and_private_file(self):
        packet = self.runcli()
        data = packet["data"]
        path = Path(data["saved_path"])
        self.assertEqual(path.read_bytes(), self.server.body)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(data["attachment_id"], "7")
        self.assertEqual(data["sha256"], hashlib.sha256(self.server.body).hexdigest())
        self.assertTrue(data["size_matches_metadata"])
        self.assertEqual(len(self.server.calls), 2)
        self.assertEqual(self.server.calls[-1], "/secure/attachment/7/file.cu")

    def test_diagnostic_flag_does_not_change_normal_download(self):
        self.assertTrue(self.runcli("--diagnose-auth-redirect")["success"])
        self.assertEqual(len(self.server.calls), 2)

    def test_default_login_redirect_is_not_global_auth_failure(self):
        self.server.mode = "login"
        packet = self.rejected("login_redirect")
        self.assertEqual(len(packet["download"]["attempts"]), 1)
        self.assertEqual(len(self.server.calls), 2)

    def test_diagnostic_single_retry_exposes_not_found(self):
        self.server.mode = "login"
        packet = self.rejected("attachment_unavailable", "--diagnose-auth-redirect")
        self.assertEqual([item["http_status"] for item in packet["download"]["attempts"]], [302, 404])
        self.assertEqual(self.server.calls[-1], "/secure/attachment/7/file.cu?os_authType=basic")
        self.assertEqual(len(self.server.calls), 3)

    def test_diagnostic_stops_on_repeated_redirect(self):
        self.server.mode, self.server.after = "login", 302
        packet = self.rejected("login_redirect", "--diagnose-auth-redirect")
        self.assertEqual(len(packet["download"]["attempts"]), 2)
        self.assertEqual(len(self.server.calls), 3)

    def test_diagnostic_can_download_after_challenge(self):
        self.server.mode, self.server.after = "login", 200
        data = self.runcli("--diagnose-auth-redirect")["data"]
        self.assertEqual(Path(data["saved_path"]).read_bytes(), self.server.body)
        self.assertEqual(len(data["attempts"]), 2)

    def test_external_redirect_never_followed_or_retried(self):
        self.server.mode = "external"
        self.rejected("redirect_blocked", "--diagnose-auth-redirect")
        self.assertEqual(len(self.server.calls), 2)

    def test_distinct_http_failures_and_non_200_success_codes(self):
        for status, code in ((401, "authentication_required"), (403, "access_denied"),
                             (404, "attachment_unavailable"), (500, "http_error"),
                             (204, "http_error"), (206, "http_error")):
            with self.subTest(status=status):
                self.server.code = status
                self.rejected(code)

    def test_rejects_html_by_header_or_bytes(self):
        for mime in ("text/html", "application/octet-stream"):
            with self.subTest(mime=mime):
                self.server.mime = mime
                self.server.body = b"<html><form>login</form></html>"
                self.server.meta["size"] = len(self.server.body)
                self.rejected("unexpected_html")

    def test_real_html_attachment_is_allowed(self):
        self.server.meta["filename"] = "report.html"
        self.server.meta["content"] = self.origin + "/secure/attachment/7/report.html"
        self.server.meta["mimeType"] = "text/html"
        self.server.mime, self.server.body = "text/html", b"<html>report</html>"
        self.server.meta["size"] = len(self.server.body)
        self.assertTrue(self.runcli()["success"])

    def test_size_mismatch_short_and_long(self):
        for size in (1, 100):
            with self.subTest(size=size):
                self.server.meta["size"] = size
                self.rejected("size_mismatch")

    def test_interrupted_transfer_never_publishes_file(self):
        self.server.mode = "truncated"
        self.rejected("transfer_incomplete")

    def test_budget_stops_before_content_request(self):
        self.assertFalse(self.runcli("--max-bytes", "1")["success"])
        self.assertEqual(len(self.server.calls), 1)

    def test_no_overwrite_and_explicit_disambiguation(self):
        first = self.runcli()["data"]
        self.server.body = b"changeddata\n"
        self.server.meta["size"] = len(self.server.body)
        self.assertFalse(self.runcli()["success"])
        self.assertEqual(Path(first["saved_path"]).read_bytes(), b"source line\n")
        self.assertTrue(self.runcli("--filename", "7_file.cu")["success"])

    def test_url_must_belong_to_issue(self):
        self.assertFalse(self.runcli("--url", self.origin + "/secure/attachment/8/other.txt")["success"])
        self.assertEqual(len(self.server.calls), 1)

    def test_unicode_name_and_empty_file(self):
        name = "构建 日志.txt"
        self.server.meta["filename"] = name
        self.server.meta["content"] = self.origin + "/secure/attachment/7/" + quote(name)
        self.server.meta["size"], self.server.body = 0, b""
        data = self.runcli()["data"]
        self.assertEqual(data["filename"], name)
        self.assertEqual(Path(data["saved_path"]).read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
