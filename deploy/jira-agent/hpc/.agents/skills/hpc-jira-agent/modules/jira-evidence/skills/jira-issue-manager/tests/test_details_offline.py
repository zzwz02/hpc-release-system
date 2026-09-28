"""Real shipped CLI against a loopback server; no production Jira operations."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/get_issue_details.py"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        route = urlsplit(self.path)
        code = 200
        if self.headers.get("Authorization") != "Bearer offline-only":
            self.send_error(401)
            return
        if route.path == "/rest/api/2/field":
            code = self.server.field_status
            data = [{"id": "customfield_1", "name": "SDK"}]
        elif route.path.endswith("/comment"):
            offset = int(parse_qs(route.query)["startAt"][0])
            code = self.server.comment_status if offset >= self.server.fail_at else 200
            data = {"startAt": offset, "total": 2, "comments": [{"id": str(offset + 1), "body": "original page " + str(offset)}]}
            if self.server.mode == "duplicate" and offset:
                data["comments"][0]["id"] = "1"
            if self.server.mode == "missing_total":
                data.pop("total")
        else:
            code = self.server.issue_status
            data = {"key": "TEST-1", "fields": {"summary": "raw summary", "description": "raw command\r\n\targ",
                    "customfield_1": "sdk", "attachment": [], "comment": {"comments": [{"id": "9", "body": "embedded partial"}]}}}
        raw = (json.dumps(data) if code == 200 else "MUST-NOT-LEAK").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Details(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.calls = []
        self.server.issue_status = self.server.field_status = self.server.comment_status = 200
        self.server.fail_at, self.server.mode = 0, "normal"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        origin = "http://127.0.0.1:" + str(self.server.server_port)
        (self.root / "token").write_text("offline-only")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        self.env.update(JIRA_TOKEN_FILE=str(self.root / "token"), JIRA_BASE_URL=origin, JIRA_ALLOWED_ORIGIN=origin,
                        NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def runcli(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--issue-id", "TEST-1"],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotIn("offline-only", result.stdout + result.stderr)
        self.assertNotIn("MUST-NOT-LEAK", result.stdout + result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if packet["success"] else 1)
        return packet

    def test_complete_paging_and_raw_fields(self):
        packet = self.runcli()
        self.assertEqual(packet["completeness"], "complete")
        self.assertEqual(packet["data"]["raw_fields"]["description"], "raw command\r\n\targ")
        self.assertEqual([item["id"] for item in packet["data"]["comments"]], ["1", "2"])
        self.assertEqual(len(packet["data"]["acquisition"]["parts"]["comments"]["pages"]), 2)

    def test_field_denied_does_not_lose_issue(self):
        self.server.field_status = 403
        packet = self.runcli()
        self.assertEqual(packet["completeness"], "partial")
        self.assertEqual(packet["data"]["summary"], "raw summary")
        self.assertEqual(packet["data"]["acquisition"]["parts"]["field_definitions"]["error_code"], "access_denied")

    def test_comment_denied_retains_embedded_page(self):
        self.server.comment_status = 403
        packet = self.runcli()
        self.assertEqual(packet["completeness"], "partial")
        self.assertEqual(packet["data"]["comments"][0]["body_raw"], "embedded partial")
        coverage = packet["data"]["acquisition"]["parts"]["comments"]
        self.assertEqual((coverage["status"], coverage["error_code"]), ("partial", "access_denied"))

    def test_failure_after_page_retains_successful_page(self):
        self.server.comment_status, self.server.fail_at = 500, 1
        packet = self.runcli()
        self.assertEqual([item["id"] for item in packet["data"]["comments"]], ["1"])
        self.assertEqual(packet["data"]["acquisition"]["parts"]["comments"]["reported_total"], 2)
        self.assertEqual(packet["completeness"], "partial")

    def test_duplicate_or_missing_total_not_complete(self):
        for mode in ("duplicate", "missing_total"):
            self.server.mode = mode
            self.assertEqual(self.runcli()["completeness"], "partial")

    def test_issue_unavailable_is_failure_not_empty_success(self):
        self.server.issue_status = 404
        packet = self.runcli()
        self.assertFalse(packet["success"])
        self.assertEqual(packet["error_code"], "not_found")
        self.assertNotIn("data", packet)
        self.assertEqual(len(self.server.calls), 1)


if __name__ == "__main__":
    unittest.main()
