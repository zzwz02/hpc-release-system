"""Exercise the shipped unified loader with local files and a loopback Jira."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/load_issue.py"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        if self.headers.get("Authorization") != "Bearer offline-only":
            self.send_error(401)
            return
        route = urlsplit(self.path)
        if route.path == "/rest/api/2/field":
            data = []
        elif route.path.endswith("/comment"):
            offset = int(parse_qs(route.query)["startAt"][0])
            data = {"startAt": offset, "total": 1, "comments": [{
                "id": "c1", "author": {"displayName": "API User"},
                "body": "api comment", "created": "2026-09-20T02:00:00Z",
            }]}
        else:
            data = {"key": "TEST-1", "fields": {
                "summary": "API summary", "description": "API description",
                "issuetype": {"name": "Bug"}, "status": {"name": "Open"},
                "priority": {"name": "Major"}, "project": {"key": "TEST"},
                "components": [{"name": "HPC"}], "labels": ["api"],
                "assignee": {"displayName": "Owner", "name": "owner"},
                "reporter": {"displayName": "Reporter"},
                "created": "2026-09-19T01:00:00Z", "updated": "2026-09-20T02:00:00Z",
                "attachment": [{"id": "a1", "filename": "trace.log", "size": 5,
                                "content": self.server.origin + "/attachment/a1"}],
                "comment": {"comments": []},
            }}
        raw = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LoadIssue(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        (self.bundle / "attachments").mkdir(parents=True)
        (self.bundle / "uploads").mkdir()
        (self.bundle / "attachments/trace.log").write_text("trace")
        (self.bundle / "uploads/note.txt").write_text("note")
        (self.bundle / "issue.md").write_text(
            """# TEST-1 Local summary
- 链接：http://jira.invalid/browse/TEST-1
- 类型：Bug　状态：Open　优先级：Major
- 项目：TEST　组件：HPC　标签：local, regression
- assignee：Owner（owner）　reporter：Reporter
- 创建：2026-09-19T01:00:00Z　更新：2026-09-19T02:00:00Z

## 描述
Local description

## 附件
- trace.log（5 字节）→ `attachments/trace.log`

## 评论（按时间顺序）
### Local User @ 2026-09-19T02:00:00Z
local comment
""",
            encoding="utf-8",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.calls = []
        self.server.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        (self.root / "token").write_text("offline-only")
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        self.env.update(
            JIRA_TOKEN_FILE=str(self.root / "token"),
            JIRA_BASE_URL=self.server.origin,
            JIRA_ALLOWED_ORIGIN=self.server.origin,
            NO_PROXY="127.0.0.1",
            no_proxy="127.0.0.1",
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def runcli(self, source, bundle=None, env=None, base=None):
        command = [sys.executable, "-B", str(SCRIPT), "--issue-id", "TEST-1", "--source", source,
                   "--bundle-dir", str(bundle or self.bundle), "--timeout", "2"]
        if base:
            command.extend(["--base-url", base])
        result = subprocess.run(command, cwd=self.root, env=env or self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertNotIn("offline-only", result.stdout + result.stderr)
        packet = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if packet["success"] else 1)
        return packet

    def test_local_normalizes_snapshot_and_files_without_token(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("JIRA_")}
        packet = self.runcli("local", env=env)
        data = packet["data"]
        self.assertEqual((data["key"], data["summary"], data["status"]),
                         ("TEST-1", "Local summary", "Open"))
        self.assertEqual(data["comments"][0]["body_raw"], "local comment")
        self.assertEqual(data["attachments"][0]["local_path"], "attachments/trace.log")
        self.assertEqual(data["uploads"][0]["local_path"], "uploads/note.txt")
        self.assertEqual(data["acquisition"]["source"], "local_bundle")

    def test_local_preserves_description_and_comment_headings(self):
        description = "Original command\n## Reproduction\npython test.py\n## Acceptance\nerror < 0.001"
        bodies = ["First note\n## Details\nFull logs\n### Extra context\nKeep this too",
                  "Second note\n## Updated requirements\nUse the original input"]
        path = self.bundle / "issue.md"
        text = path.read_text().replace("Local description", description)
        text = text.replace("local comment\n", bodies[0] + "\n\n### Reviewer @ 2026-09-20T02:00:00Z\n" + bodies[1] + "\n")
        path.write_text(text)
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "complete")
        self.assertEqual(packet["data"]["description"], description)
        self.assertEqual(packet["data"]["raw_fields"]["description"], description)
        self.assertEqual([item["body_raw"] for item in packet["data"]["comments"]], bodies)
        self.assertEqual(packet["data"]["attachments"][0]["local_path"], "attachments/trace.log")

    def test_local_ignores_structural_looking_lines_inside_code_blocks(self):
        path = self.bundle / "issue.md"
        original = path.read_text()
        fences = [("```markdown", "```"), ("~~~~", "~~~~"),
                  ("````markdown", "````"), ("{code:bash}", "{code}"),
                  ("{noformat}", "{noformat}")]
        for opening, closing in fences:
            with self.subTest(opening=opening):
                code = opening + "\n## 附件\n## 评论（按时间顺序）\n### Fake @ 2026-01-01\nexample\n"
                if opening == "````markdown":
                    code += "```\n## Another code heading\n"
                code += closing
                description = "Original description\n" + code + "\n## Acceptance\nKeep the end"
                body = "Real comment\n" + code + "\n## More details\nKeep this end too"
                text = original.replace("Local description", description).replace("local comment\n", body + "\n")
                text += "\n### Reviewer @ 2026-09-20T02:00:00Z\nNext real comment\n"
                path.write_text(text)
                packet = self.runcli("local")
                self.assertEqual(packet["completeness"], "complete")
                self.assertEqual(packet["data"]["description"], description)
                self.assertEqual([item["body_raw"] for item in packet["data"]["comments"]],
                                 [body, "Next real comment"])

    def test_local_reports_ambiguous_section_boundaries_and_keeps_full_source(self):
        path = self.bundle / "issue.md"
        text = path.read_text().replace("Local description", "Description\n## 附件\nThis heading is part of the description")
        path.write_text(text)
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "partial")
        data = packet["data"]
        self.assertEqual(data["acquisition"]["parts"]["description"]["status"], "partial")
        self.assertIn("ambiguous_markdown_sections", [item["kind"] for item in data["acquisition"]["conflicts"]])
        self.assertEqual(data["source_documents"][0]["text"], text)

    def test_local_keeps_unparsed_comment_text_and_reports_partial(self):
        path = self.bundle / "issue.md"
        text = path.read_text().replace("### Local User @ 2026-09-19T02:00:00Z\n", "")
        path.write_text(text)
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "partial")
        self.assertEqual(packet["data"]["acquisition"]["parts"]["comments"]["status"], "partial")
        self.assertEqual(packet["data"]["source_documents"][0]["text"], text)

    def test_local_does_not_claim_complete_when_a_code_block_is_unclosed(self):
        path = self.bundle / "issue.md"
        text = path.read_text().replace("local comment\n", "local comment\n```markdown\n## Details\nUnclosed code\n")
        text += "\n### Reviewer @ 2026-09-20T02:00:00Z\nNext real comment\n"
        path.write_text(text)
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "partial")
        self.assertIn("unclosed_markup_block", [item["kind"] for item in packet["data"]["acquisition"]["conflicts"]])
        self.assertEqual(packet["data"]["source_documents"][0]["text"], text)

    def test_local_preserves_english_section_format(self):
        path = self.bundle / "issue.md"
        text = path.read_text().replace("## 描述", "## Description").replace("## 附件", "## Attachments").replace("## 评论（按时间顺序）", "## Comments")
        description = "Original\n## Reproduction\nRun this\n## Expected\nKeep this"
        path.write_text(text.replace("Local description", description))
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "complete")
        self.assertEqual(packet["data"]["description"], description)
        self.assertEqual(packet["data"]["comments"][0]["body"], "local comment")

    def write_json_snapshot(self, partial=False):
        markdown = (self.bundle / "issue.md").read_text()
        description = "Local description\n## 附件\nThis heading belongs to the description"
        markdown = markdown.replace("Local description", description)
        (self.bundle / "issue.md").write_text(markdown)
        data = {
            "key": "TEST-1", "summary": "Local summary", "description": description,
            "raw_fields": {"description": description, "customfield_1": None},
            "custom_fields": {"customfield_1": {"name": "SDK", "value": None, "raw": None, "schema": None}},
            "field_definitions": [],
            "comments": [{"id": "original-id", "author": "Owner", "body": "First\n## Details\nFull body", "created": "2026-09-20"}],
            "attachments": [{"id": "a1", "filename": "trace.log", "size": 5, "local_path": "attachments/trace.log"}],
            "acquisition": {"status": "partial" if partial else "complete", "source": "website_jira_api", "conflicts": [], "parts": {
                "issue": {"status": "complete"}, "description": {"status": "complete"},
                "custom_fields": {"status": "complete", "collected": 1}, "field_definitions": {"status": "complete"},
                "attachment_metadata": {"status": "complete"},
                "comments": {"status": "partial" if partial else "complete", "collected": 1, "reported_total": 5 if partial else 1},
            }},
        }
        packet = {"schema_version": 1, "issue_markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(), "issue": data}
        (self.bundle / "issue.json").write_text(json.dumps(packet, ensure_ascii=False))
        return data

    def test_json_preserves_fields_and_comments_without_markdown_parsing(self):
        expected = self.write_json_snapshot()
        before = len(self.server.calls)
        packet = self.runcli("auto")
        self.assertEqual(packet["completeness"], "complete")
        self.assertEqual(packet["data"]["description"], expected["description"])
        self.assertEqual(packet["data"]["custom_fields"], expected["custom_fields"])
        self.assertEqual(packet["data"]["comments"][0]["id"], "original-id")
        self.assertEqual(packet["data"]["comments"][0]["body_raw"], expected["comments"][0]["body"])
        self.assertEqual(len(self.server.calls), before)

    def test_json_does_not_upgrade_partial_collection(self):
        self.write_json_snapshot(partial=True)
        packet = self.runcli("local")
        self.assertEqual(packet["completeness"], "partial")
        self.assertEqual(packet["data"]["acquisition"]["parts"]["comments"]["reported_total"], 5)

    def test_bad_json_never_silently_falls_back_to_markdown_or_rest(self):
        self.write_json_snapshot()
        (self.bundle / "issue.json").write_text("{unfinished")
        before = len(self.server.calls)
        packet = self.runcli("auto")
        self.assertFalse(packet["success"])
        self.assertEqual(packet["error_code"], "local_json_unreadable")
        self.assertEqual(len(self.server.calls), before)

    def test_auto_prefers_local_without_contacting_jira(self):
        before = len(self.server.calls)
        packet = self.runcli("auto")
        self.assertEqual(packet["data"]["summary"], "Local summary")
        self.assertEqual(len(self.server.calls), before)

    def test_auto_uses_jira_when_issue_markdown_is_absent(self):
        empty = self.root / "empty"
        empty.mkdir()
        packet = self.runcli("auto", bundle=empty)
        self.assertEqual(packet["data"]["summary"], "API summary")
        self.assertEqual(packet["data"]["acquisition"]["source"], "jira_api")

    def test_local_rejects_a_different_issue_identity(self):
        value = (self.bundle / "issue.md").read_text().replace("# TEST-1", "# OTHER-2", 1)
        (self.bundle / "issue.md").write_text(value)
        packet = self.runcli("local")
        self.assertFalse(packet["success"])
        self.assertEqual(packet["error_code"], "local_issue_identity_mismatch")

    def test_hybrid_keeps_api_primary_and_records_differences(self):
        packet = self.runcli("hybrid")
        data = packet["data"]
        self.assertEqual(data["summary"], "API summary")
        self.assertEqual(data["attachments"][0]["local_path"], "attachments/trace.log")
        self.assertEqual(data["uploads"][0]["local_path"], "uploads/note.txt")
        self.assertEqual(data["acquisition"]["source"], "hybrid")
        fields = [item.get("field") for item in data["acquisition"]["conflicts"]]
        self.assertIn("summary", fields)
        self.assertIn("description", fields)
        self.assertIn("updated", fields)

    def test_hybrid_falls_back_to_local_when_api_is_unavailable(self):
        packet = self.runcli("hybrid", base="http://127.0.0.1:1")
        self.assertTrue(packet["success"])
        self.assertEqual(packet["completeness"], "partial")
        self.assertEqual(packet["data"]["summary"], "Local summary")
        kinds = [item["kind"] for item in packet["data"]["acquisition"]["conflicts"]]
        self.assertIn("jira_api_unavailable", kinds)


if __name__ == "__main__":
    unittest.main()
