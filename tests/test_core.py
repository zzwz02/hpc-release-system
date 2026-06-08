from __future__ import annotations

import datetime as dt
import csv
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

import server
from release_system import core, llm
from release_system.wiki import core as wiki_core


INIT_CSV = """官方名称,类型,APP类型,Owner,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch
Amber,HPC,分子动力学,张三,22,"c500,x301",x201,x86,20260511-695,ssh://gerrit/PDE/HPC/hpc_amber,maca
"""


APP_INFO_V1 = {
    "app_version": "22",
    "app_name": "amber",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "supported_chip": ["c500", "x301"],
            "enabled": True,
        }
    },
    "app_test": {
        "run_make_test": {
            "test_cmd": "cd /root/amber22/test && bash test_amber.sh",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"], "x301": ["ubuntu20.04_amd64"]},
            "enabled": True,
        }
    },
}


APP_INFO_V2 = {
    "app_version": "23",
    "app_name": "amber",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "supported_chip": ["c500", "x301", "n300"],
            "enabled": True,
        }
    },
    "app_test": {
        "run_make_test": {
            "test_cmd": "cd /root/amber23/test && bash test_amber.sh",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"], "x301": ["ubuntu20.04_amd64"]},
            "enabled": True,
        },
        "sanity": {
            "test_cmd": "amber --version",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
            "enabled": True,
        },
    },
}

APP_INFO_ARM = {
    "app_version": "1.0",
    "app_name": "amber",
    "app_build": {
        "ubuntu20.04_aarch64": {
            "build_target": "release",
            "arch": "aarch64",
            "supported_chip": ["c500"],
            "enabled": True,
        }
    },
    "app_test": {
        "arm_only": {
            "test_cmd": "run-arm-test",
            "supported_chip": {"c500": ["ubuntu20.04_aarch64"]},
            "enabled": True,
        }
    },
}

APP_INFO_WEEKLY = {
    "app_version": "22",
    "app_name": "amber",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "supported_chip": ["c500"],
            "enabled": True,
        }
    },
    "app_test": {
        "daily_test": {
            "test_cmd": "bash daily.sh",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
            "enabled": True,
            "test_period": "daily",
        },
        "weekly_test": {
            "test_cmd": "bash weekly.sh",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"]},
            "enabled": True,
            "test_period": "weekly",
        },
    },
}

APP_INFO_DISABLED = {
    "app_version": "22",
    "app_name": "amber",
    "app_build": {
        "ubuntu20.04_amd64": {
            "build_target": "release",
            "arch": "amd64",
            "supported_chip": ["c500", "x301"],
            "enabled": True,
        }
    },
    "app_test": {
        "run_make_test": {
            "test_cmd": "cd /root/amber22/test && bash test_amber.sh",
            "supported_chip": {"c500": ["ubuntu20.04_amd64"], "x301": ["ubuntu20.04_amd64"]},
            "enabled": True,
        },
        "disabled_test": {
            "test_cmd": "cd /root/amber22/test && bash disabled.sh",
            "supported_chip": {"n300": ["ubuntu20.04_amd64"]},
            "enabled": False,
        },
    },
}

DUP_CSV = """官方名称,类型,APP类型,Owner,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch
foo,HPC,solver,Alice,1,c500,,x86,3.7.0,ssh://gerrit/foo,main
foo,HPC,solver,Alice,1,c600,,arm,3.7.0,ssh://gerrit/foo,main
foo,HPC,solver,Bob,2,n300,,x86,3.7.0,ssh://gerrit/foo,release-2
"""


def _fill_ready(snapshot: dict) -> None:
    snapshot["owner_confirmed"] = True
    snapshot["description"] = "测试描述"
    snapshot["doc"].update({"intro": "i", "image_usage": "i", "binary_usage": "b", "env_setup": "e"})
    for doc in snapshot["test_docs"]:
        doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p"})


class CoreWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.init_csv = self.root / "init.csv"
        self.init_csv.write_text(INIT_CSV, encoding="utf-8")
        self.db_path = self.root / "test.db"
        self.conn = core.connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def import_initial(self, **kwargs) -> tuple[str, str]:
        release_id = core.import_initial(self.conn, self.init_csv, **kwargs)
        return release_id, core.normalize_name("amber")

    # --- CICD workbench ---

    def test_cicd_modify_request_includes_task_context(self) -> None:
        create_req = core.submit_cicd_request(
            self.conn,
            task_id=None,
            request_type="create",
            payload={
                "app_name": "hpc-demo",
                "app_version": "1.0",
                "repo_type": "git",
                "repo_name": "ssh://gerrit/demo",
                "branch": "main",
                "build_product": ["maca"],
                "community_artifact": ["image"],
                "build_image": "demo/base:latest",
                "test_timeout": 40,
                "owner_username": "owner",
                "status": "Running",
                "notes": "",
            },
            submitter="rm",
            submitter_role="RM",
            submitter_display="RM",
        )
        task_id = create_req["task_id"]

        core.submit_cicd_request(
            self.conn,
            task_id=task_id,
            request_type="modify",
            payload={"notes": {"old": "", "new": "update note"}},
            submitter="owner",
            submitter_role="Owner",
            submitter_display="Owner",
        )

        req = core.list_cicd_requests(self.conn, status_filter="pending", role="RM")[0]
        self.assertEqual(req["request_type"], "modify")
        self.assertEqual(req["task_app_name"], "hpc-demo")
        self.assertEqual(req["task_app_version"], "1.0")
        self.assertEqual(req["task_repo_name"], "ssh://gerrit/demo")
        self.assertEqual(req["task_branch"], "main")

    # --- deadline parsing ---

    def test_normalize_deadline_accepts_common_formats(self) -> None:
        self.assertEqual(core.normalize_deadline(""), "")
        self.assertEqual(core.normalize_deadline("2026-06-01"), "2026-06-01 23:59")
        self.assertEqual(core.normalize_deadline("2026-06-01T12:30"), "2026-06-01 12:30")
        self.assertEqual(core.normalize_deadline("2026-06-01 12:30:45"), "2026-06-01 12:30")
        with self.assertRaises(ValueError):
            core.normalize_deadline("not-a-date")

    def test_is_before_uses_beijing_time(self) -> None:
        fake_now = dt.datetime(2026, 5, 1, 12, 0)
        with mock.patch("release_system.core.beijing_now", return_value=fake_now):
            self.assertTrue(core.is_before("2026-06-01 00:00"))
            self.assertFalse(core.is_before("2026-04-01 00:00"))
            self.assertTrue(core.is_before(""))  # no deadline = always before
            self.assertTrue(core.is_before(None))

    def test_current_phase_derives_from_now_and_locks(self) -> None:
        fake_now = dt.datetime(2026, 5, 15, 12, 0)
        with mock.patch("release_system.core.beijing_now", return_value=fake_now):
            self.assertEqual(core.current_phase({"app_freeze_deadline": "2026-06-01 00:00", "doc_deadline": "2026-06-10 00:00", "released_locked": False}), "before_app_freeze")
            self.assertEqual(core.current_phase({"app_freeze_deadline": "2026-05-01 00:00", "doc_deadline": "2026-06-10 00:00", "released_locked": False}), "after_app_freeze")
            self.assertEqual(core.current_phase({"app_freeze_deadline": "2026-05-01 00:00", "doc_deadline": "2026-05-10 00:00", "released_locked": False}), "after_doc_deadline")
            self.assertEqual(core.current_phase({"app_freeze_deadline": "", "doc_deadline": "", "released_locked": True}), "released_locked")

    def test_can_policy_matches_phase_action_table(self) -> None:
        # Locked: nothing is allowed.
        for action in ("new_app_release", "edit_app_info", "lower_decision", "qa_set_status"):
            self.assertFalse(core.can("released_locked", action))
        # before_app_freeze: every action allowed (no late-phase restriction).
        for action in ("new_app_release", "new_app_non_release",
                       "raise_to_release", "lower_decision",
                       "edit_app_info", "expand_qa_scope",
                       "edit_snapshot", "qa_set_status", "qa_upload_log"):
            self.assertTrue(core.can("before_app_freeze", action), action)
        # after_app_freeze: no new release-decision apps; no raise-to-release;
        # no QA-scope expansion; doc edits still open.
        self.assertFalse(core.can("after_app_freeze", "new_app_release"))
        self.assertFalse(core.can("after_app_freeze", "raise_to_release"))
        self.assertFalse(core.can("after_app_freeze", "expand_qa_scope"))
        self.assertTrue(core.can("after_app_freeze", "edit_snapshot"))
        self.assertTrue(core.can("after_app_freeze", "edit_app_info"))
        self.assertTrue(core.can("after_app_freeze", "lower_decision"))
        # after_doc_deadline: docs frozen; only QA + non-release additions + downgrade.
        self.assertFalse(core.can("after_doc_deadline", "edit_snapshot"))
        self.assertFalse(core.can("after_doc_deadline", "edit_app_info"))
        self.assertTrue(core.can("after_doc_deadline", "qa_set_status"))
        self.assertTrue(core.can("after_doc_deadline", "lower_decision"))
        self.assertTrue(core.can("after_doc_deadline", "new_app_non_release"))
        # Unknown actions fail closed.
        self.assertFalse(core.can("before_app_freeze", "nope_not_an_action"))

    def test_update_release_settings_renames_and_normalizes_date_deadlines(self) -> None:
        release_id, _ = self.import_initial()
        release = core.update_release_deadlines(
            self.conn,
            release_id,
            name="3.8.0",
            app_freeze_deadline="2026-06-01",
            doc_deadline="2026-06-10",
            user="rm",
            role="RM",
        )
        self.assertEqual(release["name"], "3.8.0")
        self.assertEqual(release["app_freeze_deadline"], "2026-06-01 23:59")
        self.assertEqual(release["doc_deadline"], "2026-06-10 23:59")

    # --- import + initial schema ---

    def test_initial_import_creates_app_and_snapshot(self) -> None:
        release_id, app_id = self.import_initial()
        apps = core.list_apps(self.conn)
        self.assertEqual(len(apps), 1)
        release = core.get_release(self.conn, release_id)
        self.assertFalse(release["released_locked"])
        self.assertIn(app_id, release["snapshots"])
        snap = release["snapshots"][app_id]
        self.assertEqual(snap["qa_status"], "not_checked")
        self.assertEqual(snap["owners"], ["张三"])
        self.assertEqual(snap["official_name"], "Amber")
        self.assertEqual(snap["doc_target"], "manual")

    def test_initial_import_accepts_hpc_app_csv_shape(self) -> None:
        rows = core.parse_csv_text("""类别,id,名称,Owner,类型,描述,git_url,git_branch
HPC APP,1,HPL,孙跃,HPC基准测试,求解随机稠密线性方程组,hpc_hpl,maca
""")
        release_id = core.import_initial_rows(self.conn, rows)
        apps = core.list_apps(self.conn)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["id"], "hpl")
        self.assertEqual(apps[0]["git_url"], "hpc_hpl")
        snap = core.get_release(self.conn, release_id)["snapshots"]["hpl"]
        self.assertEqual(snap["official_name"], "HPL")
        self.assertEqual(snap["owners"], ["孙跃"])
        self.assertEqual(snap["type"], "HPC基准测试")
        self.assertEqual(snap["description"], "求解随机稠密线性方程组")
        self.assertEqual(snap["doc_target"], "manual")

    def test_hpc_app_csv_ai_for_science_model_maps_to_ai4sci(self) -> None:
        rows = core.parse_csv_text("""类别,id,名称,Owner,类型,描述,git_url,git_branch
AI for Science模型,1,ALIGNN,闫申申,材料科学模型,材料科学图神经网络,hpc_alignn,maca
""")
        release_id = core.import_initial_rows(self.conn, rows)
        snap = core.get_release(self.conn, release_id)["snapshots"]["alignn"]
        self.assertEqual(snap["type"], "材料科学模型")
        self.assertEqual(snap["doc_target"], "ai4sci")

    def test_hpc_app_csv_tool_and_framework_map_to_hpc(self) -> None:
        # 工具 and an HPC app_type that merely contains '框架' both stay HPC.
        rows = core.parse_csv_text("""类别,id,名称,Owner,类型,描述,git_url,git_branch
工具,1,Slurm,姜海洋,工具,集群作业调度系统,hpc_slurm,maca
HPC APP,2,AMReX,余多,HPC框架/工具,自适应网格框架,hpc_amrex,maca
""")
        release_id = core.import_initial_rows(self.conn, rows)
        snaps = core.get_release(self.conn, release_id)["snapshots"]
        self.assertEqual(snaps["slurm"]["doc_target"], "manual")
        self.assertEqual(snaps["amrex"]["doc_target"], "manual")

    def test_initial_import_skips_rows_without_repo(self) -> None:
        rows = core.parse_csv_text("""类别,id,名称,Owner,类型,描述,git_url,git_branch
HPC APP,1,HPL,孙跃,HPC基准测试,desc,hpc_hpl,maca
HPC APP,2,OpenLB,刘玉春,CFD,停止发布,,
""")
        release_id = core.import_initial_rows(self.conn, rows)
        self.assertEqual([a["id"] for a in core.list_apps(self.conn)], ["hpl"])
        self.assertIn("hpl", core.get_release(self.conn, release_id)["snapshots"])

    def test_default_users_include_qa_and_guest(self) -> None:
        self.assertIsNotNone(core.authenticate(self.conn, "qa", "qa"))
        guest_token = core.authenticate(self.conn, "guest", "guest")
        self.assertIsNotNone(guest_token)
        self.assertEqual(core.session_user(self.conn, guest_token)["role"], "Guest")

    # --- app_info upload ---

    def test_app_info_upload_uppercases_chips(self) -> None:
        release_id, app_id = self.import_initial()
        snapshot = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        self.assertEqual(snapshot["x86_chips"], "C500,X301")
        self.assertEqual(len(snapshot["test_docs"]), 1)

    def test_qa_release_report_includes_runtime_optional_columns(self) -> None:
        release_id, app_id = self.import_initial()
        app_info = json.loads(json.dumps(APP_INFO_V1))
        app_info["app_build"]["ubuntu20.04_amd64"].update({
            "os": "ubuntu20.04",
            "python_label": "py310",
            "pytorch_label": "torch2.1",
        })
        core.apply_app_info(self.conn, release_id, app_id, app_info, source="unit")
        report = core.build_qa_reports(self.conn, release_id)["release_report"]
        row = report["rows"][0]
        by_column = dict(zip(report["columns"], row))
        self.assertEqual(by_column["OS"], "ubuntu20.04")
        self.assertEqual(by_column["Python version"], "py310")
        self.assertEqual(by_column["PyTorch version"], "torch2.1")

    def test_qa_test_cmd_expands_release_image_placeholders_from_enabled_builds(self) -> None:
        release_id, app_id = self.import_initial()
        app_info = {
            "app_version": "3.7.0",
            "app_name": "abacus",
            "app_build": {
                "ubuntu20.04_amd64": {
                    "build_target": "dbg, release",
                    "os": "ubuntu20.04",
                    "arch": "amd64",
                    "enabled": False,
                },
                "kylin2309a_arm64": {
                    "build_target": "dbg, release",
                    "os": "kylin2309a",
                    "arch": "arm64",
                },
                "kylinv10_arm64": {
                    "build_target": "dbg, release",
                    "os": "kylinv10",
                    "arch": "arm64",
                },
                "centos9_amd64": {
                    "build_target": "dbg, release",
                    "os": "centos9",
                    "arch": "amd64",
                },
            },
            "app_test": {
                "aaa": {
                    "test_cmd": "echo aaa",
                    "supported_chip": {"C500": ["ubuntu20.04_amd64"]},
                    "enabled": True,
                    "img_target": "release",
                },
            },
        }
        core.apply_app_info(self.conn, release_id, app_id, app_info, source="unit")

        report = core.build_qa_reports(self.conn, release_id)["test_cmd"]
        rows = report["rows"]
        images = [row[6].split(" sh -c ", 1)[0].split()[-1] for row in rows]

        self.assertEqual(len(rows), 3)
        self.assertEqual([row[3] for row in rows], ["arm", "arm", "x86"])
        self.assertEqual(images, [
            "abacus-maca:3.7.0-<hpc_version>-kylin2309a-arm64",
            "abacus-maca:3.7.0-<hpc_version>-kylinv10-arm64",
            "abacus-maca:3.7.0-<hpc_version>-centos9-amd64",
        ])
        self.assertFalse(any("[docker_image_release]" in row[6] for row in rows))
        self.assertFalse(any("ubuntu20.04" in row[6] for row in rows))

    def test_qa_test_cmd_expands_dbg_image_placeholders_with_optional_fields(self) -> None:
        release_id, app_id = self.import_initial()
        app_info = json.loads(json.dumps(APP_INFO_V1))
        app_info["spec"] = "gpu"
        app_info["app_build"]["ubuntu20.04_amd64"].update({
            "build_target": "dbg",
            "os": "ubuntu20.04",
            "python_label": "py310",
            "pytorch_label": "torch2.1",
        })
        app_info["app_test"]["run_make_test"]["img_target"] = "dbg"
        core.apply_app_info(self.conn, release_id, app_id, app_info, source="unit")

        report = core.build_qa_reports(self.conn, release_id)["test_cmd"]
        rows = report["rows"]
        images = [row[6].split(" sh -c ", 1)[0].split()[-1] for row in rows]

        self.assertEqual(images, [
            "amber-gpu-maca-dbg:22-<hpc_version>-torch2.1-py310-ubuntu20.04-amd64"
        ])
        self.assertFalse(any("[docker_image_dbg]" in row[6] for row in rows))

    def test_app_info_reupload_same_content_preserves_owner_confirm(self) -> None:
        # Re-uploading identical app_info (e.g. an idempotent Gerrit re-fetch)
        # must not silently wipe out an Owner's prior confirmation — that
        # would force unnecessary re-confirmation churn.
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        self.assertTrue(core.get_release(self.conn, release_id)["snapshots"][app_id]["owner_confirmed"])
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit-2")
        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertTrue(snap["owner_confirmed"])
        events = [r["event"] for r in core.app_audit_log(self.conn, app_id, release_id)]
        self.assertNotIn("owner_confirm_invalidated", events)

    def test_app_info_reupload_different_content_clears_owner_confirm(self) -> None:
        # When the app_info content actually changes the Owner *should* be
        # forced to re-review and re-confirm.
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit-2")
        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertFalse(snap["owner_confirmed"])
        events = [r["event"] for r in core.app_audit_log(self.conn, app_id, release_id)]
        self.assertIn("owner_confirm_invalidated", events)

    def test_app_info_upload_blocked_after_doc_deadline(self) -> None:
        release_id, app_id = self.import_initial(doc_deadline="2026-01-01")
        fake_now = dt.datetime(2026, 5, 15)
        with mock.patch("release_system.core.beijing_now", return_value=fake_now):
            with self.assertRaisesRegex(RuntimeError, "doc deadline"):
                core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

    def test_app_info_upload_blocked_when_released_locked(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.final_lock_release(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")

    def test_weekly_tests_excluded_from_parse(self) -> None:
        parsed = core.parse_app_info(APP_INFO_WEEKLY)
        paths = [t["path"] for t in parsed["tests"]]
        self.assertIn("daily_test", paths)
        self.assertNotIn("weekly_test", paths)

    def test_disabled_tests_excluded_from_parse_and_chips(self) -> None:
        parsed = core.parse_app_info(APP_INFO_DISABLED)
        self.assertIn("C500", parsed["x86_chips"])
        self.assertIn("X301", parsed["x86_chips"])
        self.assertNotIn("N300", parsed["x86_chips"])

    def test_arm_supported_chip_is_not_reported_as_x86(self) -> None:
        parsed = core.parse_app_info(APP_INFO_ARM)
        self.assertIn("C500", parsed["arm_chips"])
        self.assertNotIn("C500", parsed["x86_chips"])

    # --- QA workflow ---

    def test_qa_set_status_requires_release_decision(self) -> None:
        release_id, app_id = self.import_initial()
        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"release_decision": "stopped"}))
        with self.assertRaisesRegex(RuntimeError, "release"):
            core.qa_set_status(self.conn, release_id, app_id, "qa_passed")

    def test_qa_has_issues_requires_note(self) -> None:
        release_id, app_id = self.import_initial()
        with self.assertRaisesRegex(ValueError, "问题说明"):
            core.qa_set_status(self.conn, release_id, app_id, "has_issues", issue_note="")

    def test_qa_has_issues_note_stored(self) -> None:
        release_id, app_id = self.import_initial()
        snap = core.qa_set_status(self.conn, release_id, app_id, "has_issues", issue_note="C500 上偶发失败")
        self.assertEqual(snap["qa_status"], "has_issues")
        self.assertEqual(snap["qa_issue_note"], "C500 上偶发失败")

    def test_qa_invalid_status_rejected(self) -> None:
        release_id, app_id = self.import_initial()
        with self.assertRaises(ValueError):
            core.qa_set_status(self.conn, release_id, app_id, "bogus")

    def test_qa_status_persists_in_snapshot(self) -> None:
        release_id, app_id = self.import_initial()
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        self.assertEqual(core.get_release(self.conn, release_id)["snapshots"][app_id]["qa_status"], "qa_passed")

    # --- QA log upload ---

    def test_qa_log_upload_creates_file_and_metadata(self) -> None:
        release_id, _ = self.import_initial()
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 6, 3, 11, 22, 33)):
            meta = core.qa_upload_log(self.conn, self.db_path, release_id, b"hello-log", "test.log", user="qa", role="QA")
        self.assertEqual(meta["filename"], "test.log")
        self.assertEqual(meta["uploaded_at"], "2026-06-03 11:22:33")
        self.assertTrue(Path(meta["storage_path"]).exists())
        self.assertEqual(Path(meta["storage_path"]).read_bytes(), b"hello-log")
        stored = core.get_qa_log(self.conn, release_id)
        self.assertEqual(stored["filename"], "test.log")
        self.assertEqual(stored["uploaded_at"], "2026-06-03 11:22:33")
        self.assertEqual(stored["uploaded_by"], "qa")

    def test_qa_log_upload_replaces_previous(self) -> None:
        release_id, _ = self.import_initial()
        core.qa_upload_log(self.conn, self.db_path, release_id, b"first", "a.log", user="qa", role="QA")
        core.qa_upload_log(self.conn, self.db_path, release_id, b"second", "b.log", user="qa", role="QA")
        meta = core.get_qa_log(self.conn, release_id)
        self.assertEqual(meta["filename"], "b.log")
        self.assertEqual(Path(meta["storage_path"]).read_bytes(), b"second")

    def test_qa_analyze_log_reports_progress_and_retries_llm(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.qa_upload_log(self.conn, self.db_path, release_id, b"amber run_make_test PASS", "qa.log", user="qa", role="QA")
        events: list[tuple[str, str]] = []
        calls = {"n": 0}

        def fake_llm(system: str, user: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("temporary outage")
            return json.dumps({
                "apps": [{
                    "app_id": app_id,
                    "qa_status": "qa_passed",
                    "qa_issue_note": "",
                    "tests": [{"test": "run_make_test", "arch": "amd64", "status": "pass", "perf": "", "note": ""}],
                }]
            })

        with mock.patch("release_system.core.time.sleep", return_value=None):
            result = core.qa_analyze_log(
                self.conn,
                self.db_path,
                release_id,
                llm_call=fake_llm,
                progress=lambda stage, message: events.append((stage, message)),
                max_llm_attempts=2,
            )

        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["apps"][0]["qa_status"], "qa_passed")
        stages = [stage for stage, _ in events]
        self.assertIn("parsing_text", stages)
        self.assertIn("waiting_llm", stages)
        self.assertIn("retrying_llm", stages)
        self.assertEqual(stages[-1], "completed")

    # --- export CSV ---

    def test_export_test_scope_csv_only_includes_release_apps(self) -> None:
        release_id, app_id = self.import_initial()
        # Add a second app as cicd_only
        core.add_new_app_request(
            self.conn,
            release_id,
            official_name="OtherApp",
            git_url="ssh://other",
            git_branch="main",
            release_decision="cicd_only",
            owner="x",
        )
        csv_text = core.export_test_scope_csv(self.conn, release_id)
        self.assertIn("app_name,version,gerrit_url,branch,owners", csv_text)
        self.assertIn("amber", csv_text)
        self.assertNotIn("OtherApp", csv_text)

    def test_generate_manager_review_csv_includes_all_apps_and_selected_fields(self) -> None:
        release_id, app_id = self.import_initial()
        core.ldap_login_or_create(self.conn, "张三", "Zhang San", ["dl.pde_sa"])
        other_id = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="OtherApp",
            git_url="ssh://other",
            git_branch="main",
            release_decision="cicd_only",
            owner="x",
        )
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.update_snapshot(
            self.conn,
            release_id,
            app_id,
            lambda s: s["doc"].update({"limitations": "owner limitation"}),
        )
        core.qa_set_status(self.conn, release_id, app_id, "has_issues", issue_note="known regression")
        generated_at = "2026-06-06T01:02:03+00:00"
        with mock.patch("release_system.core.now", return_value=generated_at):
            csv_text = core.generate_manager_review_csv(
                self.conn,
                release_id,
                ["app_name", "version", "owners", "qa_issue_note", "releasable", "not_releasable_reason", "known_limitations"],
            )
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        by_name = {row["App"]: row for row in rows}
        self.assertEqual(set(by_name), {"Amber 22", "OtherApp"})
        self.assertEqual(by_name["Amber 22"]["是否可发布"], "是")
        self.assertEqual(by_name["Amber 22"]["不可发布原因"], "")
        self.assertEqual(by_name["Amber 22"]["Owner"], "Zhang San")
        self.assertEqual(by_name["Amber 22"]["QA问题"], "known regression")
        self.assertEqual(by_name["Amber 22"]["已知限制"], "owner limitation")
        self.assertEqual(by_name["OtherApp"]["是否可发布"], "否")
        self.assertEqual(by_name["OtherApp"]["Owner"], "x")
        self.assertEqual(by_name["OtherApp"]["不可发布原因"], "Release决策非发布")
        with mock.patch("release_system.core.now", return_value=generated_at):
            default_header = next(csv.reader(io.StringIO(core.generate_manager_review_csv(self.conn, release_id))))
        self.assertEqual(default_header, ["App", "Owner", "支持芯片类型", "QA问题", "是否可发布", "不可发布原因", "已知限制"])
        artifact = self.conn.execute(
            "SELECT name, content, generated_at FROM artifacts WHERE release_id = ? AND kind = 'manager_review'",
            (release_id,),
        ).fetchone()
        self.assertEqual(artifact["name"], "manager_review_20260511-695_20260606_090203.csv")
        self.assertEqual(artifact["generated_at"], generated_at)
        self.assertIn(other_id, core.get_release(self.conn, release_id)["snapshots"])

    def test_manager_review_not_releasable_reason_uses_categories(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        csv_text = core.generate_manager_review_csv(
            self.conn,
            release_id,
            ["app_name", "not_releasable_reason"],
        )
        row = next(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(row["不可发布原因"], "文档/发布信息未完成；Owner未确认；QA未测试")

        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "cannot_release")
        csv_text = core.generate_manager_review_csv(
            self.conn,
            release_id,
            ["app_name", "not_releasable_reason"],
        )
        row = next(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(row["不可发布原因"], "QA定为不可发布")

    # --- update_snapshot deadline gating ---

    def test_update_snapshot_blocked_after_doc_deadline(self) -> None:
        release_id, app_id = self.import_initial(doc_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            with self.assertRaisesRegex(RuntimeError, "doc deadline"):
                core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "x"}))

    def test_update_snapshot_blocked_when_locked(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.final_lock_release(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "x"}))

    # --- new app request gating ---

    def test_new_app_request_after_freeze_only_allowed_as_non_release(self) -> None:
        release_id, _ = self.import_initial(app_freeze_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            with self.assertRaisesRegex(RuntimeError, "app 冻结 deadline"):
                core.add_new_app_request(
                    self.conn,
                    release_id,
                    official_name="LateRelease",
                    git_url="ssh://x",
                    git_branch="main",
                    release_decision="release",
                    owner="late",
                )
            # cicd_only is allowed
            app_id = core.add_new_app_request(
                self.conn,
                release_id,
                official_name="LateCicd",
                git_url="ssh://x",
                git_branch="main",
                release_decision="cicd_only",
                owner="late",
            )
            self.assertEqual(core.get_release(self.conn, release_id)["snapshots"][app_id]["release_decision"], "cicd_only")

    def test_new_app_request_syncs_to_later_unlocked_releases(self) -> None:
        release_38, _ = self.import_initial()
        release_39 = core.create_release_from_previous(self.conn, "3.9.0")
        app_id = core.add_new_app_request(
            self.conn,
            release_38,
            official_name="f",
            git_url="ssh://gerrit/f",
            git_branch="main",
            release_decision="release",
            owner="owner_f",
        )
        snap_38 = core.get_release(self.conn, release_38)["snapshots"][app_id]
        snap_39 = core.get_release(self.conn, release_39)["snapshots"][app_id]
        self.assertEqual(snap_38["release_decision"], "release")
        self.assertEqual(snap_39["release_decision"], "release")
        self.assertFalse(snap_39["owner_confirmed"])
        self.assertEqual(snap_39["qa_status"], "not_checked")

    def test_new_app_request_syncs_as_cicd_only_to_later_frozen_releases(self) -> None:
        release_38, _ = self.import_initial(app_freeze_deadline="2026-06-01")
        release_39 = core.create_release_from_previous(self.conn, "3.9.0", app_freeze_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            app_id = core.add_new_app_request(
                self.conn,
                release_38,
                official_name="g",
                git_url="ssh://gerrit/g",
                git_branch="main",
                release_decision="release",
                owner="owner_g",
            )
        snap_38 = core.get_release(self.conn, release_38)["snapshots"][app_id]
        snap_39 = core.get_release(self.conn, release_39)["snapshots"][app_id]
        self.assertEqual(snap_38["release_decision"], "release")
        self.assertEqual(snap_39["release_decision"], "cicd_only")

    def test_new_app_request_does_not_modify_later_locked_releases(self) -> None:
        release_38, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_38, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_38, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_38, app_id, "qa_passed")
        release_39 = core.create_release_from_previous(self.conn, "3.9.0")
        core.final_lock_release(self.conn, release_39)
        new_app_id = core.add_new_app_request(
            self.conn,
            release_38,
            official_name="f",
            git_url="ssh://gerrit/f",
            git_branch="main",
            release_decision="release",
            owner="owner_f",
        )
        self.assertIn(new_app_id, core.get_release(self.conn, release_38)["snapshots"])
        self.assertNotIn(new_app_id, core.get_release(self.conn, release_39)["snapshots"])

    # --- final lock / unlock ---

    def test_final_lock_freezes_release_and_generates_artifacts(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        artifacts = core.final_lock_release(self.conn, release_id, user="rm", role="RM")
        self.assertIn("Amber", artifacts["release_note"])
        release = core.get_release(self.conn, release_id)
        self.assertTrue(release["released_locked"])
        self.assertTrue(release["snapshots"][app_id].get("locked_in_release"))
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        with self.assertRaises(RuntimeError):
            core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "x"}))

    def test_final_lock_with_unfinished_apps_still_succeeds(self) -> None:
        """Final lock can run; unfinished apps are excluded from final artifacts."""
        release_id, app_id = self.import_initial()
        # Don't fill anything, just lock
        artifacts = core.final_lock_release(self.conn, release_id, user="rm", role="RM")
        # Release note has no qualifying apps (no doc_confirmed, no QA pass)
        self.assertNotIn("Amber", artifacts["release_note"])
        release = core.get_release(self.conn, release_id)
        self.assertTrue(release["released_locked"])
        # snapshot is not marked locked_in_release since it didn't qualify
        self.assertFalse(release["snapshots"][app_id].get("locked_in_release"))

    def test_final_unlock_clears_lock_and_artifacts(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.final_lock_release(self.conn, release_id)
        core.final_unlock_release(self.conn, release_id)
        release = core.get_release(self.conn, release_id)
        self.assertFalse(release["released_locked"])
        self.assertFalse(release["snapshots"][app_id].get("locked_in_release", False))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artifacts WHERE final = 1").fetchone()[0], 0)

    def test_final_lock_qualifying_uses_owner_confirmed_and_qa(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "cannot_release")
        artifacts = core.final_lock_release(self.conn, release_id)
        # cannot_release is excluded from final
        self.assertNotIn("Amber", artifacts["release_note"])

    def test_final_lock_excludes_when_docs_gate_items_remain(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"owner_confirmed": True}))
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        artifacts = core.final_lock_release(self.conn, release_id)
        self.assertNotIn("Amber", artifacts["release_note"])

    def test_final_lock_has_issues_includes_app_and_appends_note(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "has_issues", issue_note="known regression")
        artifacts = core.final_lock_release(self.conn, release_id)
        self.assertIn("Amber", artifacts["release_note"])
        self.assertIn("QA 备注：known regression", artifacts["manual"])

    # --- release_rows / guide_rows / artifacts ---

    def test_release_rows_preview_vs_final(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        release = core.get_release(self.conn, release_id)
        # Preview and final both exclude unfinished apps.
        self.assertEqual(len(core.release_rows(self.conn, release, final=False)), 0)
        self.assertEqual(core.release_rows(self.conn, release, final=True), [])

        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.refresh_missing_items(self.conn, release_id)
        release = core.get_release(self.conn, release_id)
        self.assertEqual(len(core.release_rows(self.conn, release, final=False)), 1)
        self.assertEqual(len(core.release_rows(self.conn, release, final=True)), 1)

    def test_manual_includes_doc_ready_app_without_qa(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertIn("Amber", artifacts["manual"])
        self.assertNotIn("Amber", artifacts["release_note"])

    def test_ai4sci_manual_includes_doc_ready_app_without_qa(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def make_ai4sci_ready(snapshot: dict) -> None:
            snapshot["doc_target"] = "ai4sci"
            _fill_ready(snapshot)
        core.update_snapshot(self.conn, release_id, app_id, make_ai4sci_ready)
        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertIn("Amber", artifacts["ai4sci"])
        self.assertNotIn("Amber", artifacts["manual"])
        self.assertNotIn("Amber", artifacts["release_note"])

    def test_guide_rows_excludes_stopped_apps(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.final_lock_release(self.conn, release_id)
        next_release = core.create_release_from_previous(self.conn, "next")
        core.update_snapshot(self.conn, next_release, app_id, lambda s: s.update({"release_decision": "stopped"}))
        rel = core.get_release(self.conn, next_release)
        active, stopped = core.guide_rows(self.conn, rel, "manual")
        self.assertEqual(active, [])
        self.assertEqual(stopped, [])

    def test_render_guide_stopped_marker(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {"version": "1.0", "doc": {}, "test_docs": []}
        out = core.render_guide("Guide", [], stopped_rows=[(app, snapshot)])
        self.assertIn("TestApp（已停止支持）", out)

    def test_render_guide_preserves_owner_markdown_without_shell_fence(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {
            "version": "1.0",
            "doc": {
                "intro": "intro",
                "image_usage": "镜像说明\n\n```bash\ndocker run app\n```",
                "binary_usage": "- 下载二进制包\n- 运行 `app --help`",
                "env_setup": "export APP_HOME=/opt/app",
            },
            "test_docs": [],
        }
        out = core.render_guide("Guide", [(app, snapshot)])
        self.assertIn("```bash\ndocker run app\n```", out)
        self.assertIn("- 下载二进制包\n- 运行 `app --help`", out)
        self.assertIn("export APP_HOME=/opt/app", out)
        self.assertNotIn("```shell", out)

    def test_render_guide_places_all_fences_on_new_lines_and_keeps_list_indent(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {
            "version": "1.0",
            "doc": {
                "intro": "intro",
                "image_usage": "镜像说明```bash\ndocker run app\n  ```",
                "binary_usage": "运行前检查 ```text\napp --help\n```",
                "env_setup": "export APP_HOME=/opt/app",
                "limitations": "限制说明```text\nknown issue\n```",
            },
            "test_docs": [{
                "path": "sanity",
                "command": "app --version",
                "dataset": "d",
                "content": "c",
                "result_view": "r",
                "pass_criteria": "p",
            }],
        }
        out = core.render_guide("Guide", [(app, snapshot)])
        self.assertIn("镜像说明\n```bash\n", out)
        self.assertIn("运行前检查\n```text\n", out)
        self.assertIn("限制说明\n```text\n", out)
        self.assertIn("- sanity\n  - 测试命令：`app --version`\n  - 测试数据集：\n    >d\n", out)
        self.assertIn("  - 通过标准：\n    >p\n\n", out)
        self.assertNotIn("```shell\napp --version", out)

        positions = []
        start = 0
        while True:
            idx = out.find("```", start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + 3
        self.assertTrue(positions)
        for idx in positions:
            line_start = out.rfind("\n", 0, idx) + 1
            self.assertEqual(out[line_start:idx].strip(), "")

    def test_markdown_fences_on_new_lines_preserves_nested_list_after_code_block(self) -> None:
        source = "- a\n  - b ```text\n  ddd\n  ```\n  - e"
        out = core.markdown_fences_on_new_lines(source)
        self.assertEqual(out, "- a\n  - b\n  ```text\n  ddd\n  ```\n  - e")

    def test_markdown_fences_on_new_lines_preserves_blockquote_fences(self) -> None:
        source = "  - 结果查看：\n    >```text\n    >abc\n    >```\n  - 通过标准：\n    >p"
        out = core.markdown_fences_on_new_lines(source)
        self.assertEqual(out, source)

    def test_markdown_fences_on_new_lines_indents_code_body_with_moved_fence(self) -> None:
        source = "- a\n  - b ```text\naaa\n   bbb\n```\n  - e"
        out = core.markdown_fences_on_new_lines(source)
        self.assertEqual(out, "- a\n  - b\n  ```text\n  aaa\n     bbb\n  ```\n  - e")

    def test_indented_code_block_indents_multiline_command(self) -> None:
        out = core.code_block("line1\nline2", indent="  ")
        self.assertEqual(out, "  ```shell\n  line1\n  line2\n  ```\n\n")

    def test_render_guide_inlines_multiline_test_command_first(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {
            "version": "1.0",
            "doc": {"intro": "intro"},
            "test_docs": [{
                "path": "sanity",
                "command": "line1\nline2 `quoted`",
                "dataset": "d",
                "content": "c",
                "result_view": "r",
                "pass_criteria": "p",
            }],
        }
        out = core.render_guide("Guide", [(app, snapshot)])
        self.assertIn("- sanity\n  - 测试命令：`` line1 line2 `quoted` ``\n  - 测试数据集：\n    >d\n", out)

    def test_render_guide_indents_multiline_test_doc_fields(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {
            "version": "1.0",
            "doc": {"intro": "intro"},
            "test_docs": [{
                "path": "si16_pw",
                "command": "app --version",
                "dataset": "amber20_benchmark_suite 数据集。\nsss\nccc",
                "content": "bbb\n\nbbb\n\nbbb",
                "result_view": "```text\nabc\n```",
                "pass_criteria": "aaa_bbb\nccc",
            }],
        }
        out = core.render_guide("Guide", [(app, snapshot)])
        self.assertIn("  - 测试数据集：\n    >amber20_benchmark_suite 数据集。\n    >sss\n    >ccc\n", out)
        self.assertIn("  - 测试内容：\n    >bbb\n    >\n    >bbb\n    >\n    >bbb\n", out)
        self.assertIn("  - 结果查看：\n    >```text\n    >abc\n    >```\n", out)
        self.assertIn("  - 通过标准：\n    >aaa_bbb\n    >ccc\n\n", out)

    def test_generate_artifacts_preview_ok_after_unlock(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        # Preview works on an unlocked release, but excludes unfinished apps.
        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertNotIn("Amber", artifacts["release_note"])
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertIn("Amber", artifacts["release_note"])
        # Final artifacts cannot be generated directly
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=True)
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=True, from_lock=True)

    # --- missing_items ---

    def test_missing_items_lists_doc_and_qa_gaps(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        results = core.refresh_missing_items(self.conn, release_id)
        items = results[app_id]
        texts = [core.missing_item_text(it) for it in items]
        self.assertTrue(any("Owner 未确认" in t for t in texts))
        self.assertTrue(any("QA 未测试" in t for t in texts))
        # QA-kind entries must be tagged so _docs_gate_items can skip them
        # without relying on text prefixes.
        qa_kinds = [core.missing_item_kind(it) for it in items if "QA 未测试" in core.missing_item_text(it)]
        self.assertEqual(qa_kinds, ["qa"])

    def test_missing_items_requires_app_type_and_short_description(self) -> None:
        release_id, app_id = self.import_initial()
        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"type": "", "description": ""}))
        items = core.refresh_missing_items(self.conn, release_id)[app_id]
        texts = [core.missing_item_text(it) for it in items]
        self.assertIn("缺少 App类型", texts)
        self.assertIn("缺少描述（30字内）", texts)

        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"type": "分子动力学", "description": " ".join(f"word{i}" for i in range(31))}))
        items = core.refresh_missing_items(self.conn, release_id)[app_id]
        texts = [core.missing_item_text(it) for it in items]
        self.assertIn("描述超过30字", texts)

    def test_normalize_app_description_counts_words_cjk_and_punctuation(self) -> None:
        self.assertEqual(core.normalize_app_description("  简短描述  "), "简短描述")
        self.assertEqual(core.app_description_count("hello world，中国!"), 6)
        self.assertEqual(core.normalize_app_description(" ".join(f"word{i}" for i in range(30))), " ".join(f"word{i}" for i in range(30)))
        with self.assertRaisesRegex(ValueError, "30"):
            core.normalize_app_description(" ".join(f"word{i}" for i in range(31)))

    def test_qa_llm_env_file_parser_supports_windows_and_linux_lines(self) -> None:
        path = self.root / "qa_llm.env"
        path.write_text(
            "\ufeff# comment\r\n"
            "QA_LLM_BASE_URL=http://host/v1\r\n"
            "export QA_LLM_MODEL=\"qwen-test\"\r\n"
            "QA_LLM_API_KEY='secret'\r\n"
            "IGNORED=value\r\n",
            encoding="utf-8",
        )

        parsed = llm.read_env_file(path)

        self.assertEqual(parsed["QA_LLM_BASE_URL"], "http://host/v1")
        self.assertEqual(parsed["QA_LLM_MODEL"], "qwen-test")
        self.assertEqual(parsed["QA_LLM_API_KEY"], "secret")
        self.assertNotIn("IGNORED", parsed)

    def test_qa_llm_settings_reads_file_with_env_override(self) -> None:
        path = self.root / "qa_llm.env"
        path.write_text(
            "QA_LLM_BASE_URL=http://file/v1\n"
            "QA_LLM_MODEL=file-model\n"
            "QA_LLM_API_KEY=file-key\n",
            encoding="utf-8",
        )

        with mock.patch.dict(
            os.environ,
            {
                "QA_LLM_ENV_FILE": str(path),
                "QA_LLM_BASE_URL": "",
                "QA_LLM_MODEL": "env-model",
                "QA_LLM_API_KEY": "",
            },
            clear=False,
        ):
            settings = llm.llm_settings()

        self.assertEqual(settings["QA_LLM_BASE_URL"], "http://file/v1")
        self.assertEqual(settings["QA_LLM_MODEL"], "env-model")
        self.assertEqual(settings["QA_LLM_API_KEY"], "file-key")

    def test_qa_llm_chat_json_uses_openai_sdk(self) -> None:
        path = self.root / "qa_llm.env"
        path.write_text(
            "QA_LLM_BASE_URL=http://local-llm/v1\n"
            "QA_LLM_MODEL=qwen-test\n"
            "QA_LLM_API_KEY=secret\n",
            encoding="utf-8",
        )
        calls: dict[str, object] = {}

        class FakeCompletions:
            def create(self, **kwargs):
                calls["create"] = kwargs
                return iter([
                    types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content='{"ok"'))]),
                    types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=': true}'))]),
                    types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=None))]),
                ])

        class FakeOpenAI:
            def __init__(self, **kwargs):
                calls["client"] = kwargs
                self.chat = types.SimpleNamespace(completions=FakeCompletions())

        fake_openai = types.SimpleNamespace(OpenAI=FakeOpenAI)
        with mock.patch.dict(sys.modules, {"openai": fake_openai}), mock.patch.dict(
            os.environ,
            {
                "QA_LLM_ENV_FILE": str(path),
                "QA_LLM_BASE_URL": "",
                "QA_LLM_MODEL": "",
                "QA_LLM_API_KEY": "",
            },
            clear=False,
        ):
            token_counts: list[int] = []
            result = llm.chat_json("system prompt", "user prompt", timeout=12, progress=token_counts.append)

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(token_counts, [1, 2])
        self.assertEqual(calls["client"], {"base_url": "http://local-llm/v1", "api_key": "secret", "timeout": 12})
        create = calls["create"]
        self.assertEqual(create["model"], "qwen-test")
        self.assertEqual(create["temperature"], 0)
        self.assertEqual(create["stream"], True)
        self.assertEqual(create["response_format"], {"type": "json_object"})
        self.assertEqual(create["messages"][0], {"role": "system", "content": "system prompt"})
        self.assertEqual(create["messages"][1], {"role": "user", "content": "user prompt"})

    def test_missing_items_empty_for_cicd_only(self) -> None:
        release_id, app_id = self.import_initial()
        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"release_decision": "cicd_only"}))
        results = core.refresh_missing_items(self.conn, release_id)
        self.assertEqual(results[app_id], [])
        self.assertEqual(core.get_release(self.conn, release_id)["snapshots"][app_id]["missing_items"], [])

    def test_missing_items_clears_after_complete(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        items = core.refresh_missing_items(self.conn, release_id)[app_id]
        self.assertEqual(items, [])

    def test_docs_gate_does_not_bypass_owner_added_path_starting_with_qa(self) -> None:
        # Regression: _docs_gate_items used to filter by the "QA " text prefix,
        # so an owner-added test path called "QA sanity" would have its
        # gate-blocking entries silently dropped and the app would qualify for
        # final release with empty test fields.
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)

        def add_qa_named_doc(snap: dict) -> None:
            snap.setdefault("test_docs", []).append({
                "id": "td_qa_sanity",
                "path": "QA sanity",
                "name": "QA sanity",
                "command": "echo run",
                "dataset": "",
                "content": "",
                "preconditions": "",
                "result_view": "",
                "pass_criteria": "",
                "coverage": "",
                "owner_added": True,
                "obsolete": False,
            })
        core.update_snapshot(self.conn, release_id, app_id, add_qa_named_doc)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        snapshot = core.refresh_missing_items(self.conn, release_id)
        items = snapshot[app_id]
        doc_blockers = core._docs_gate_items(core.get_release(self.conn, release_id)["snapshots"][app_id])
        # Both the 4 missing test-doc fields land in items, and each carries
        # the "QA sanity" path; all of them must keep blocking the doc gate.
        self.assertTrue(any("QA sanity 缺少" in core.missing_item_text(it) for it in items))
        self.assertTrue(all(core.missing_item_kind(it) == "doc" for it in doc_blockers))
        self.assertTrue(any("QA sanity 缺少" in core.missing_item_text(it) for it in doc_blockers))

    # --- create_release_from_previous ---

    def test_create_release_requires_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "名称"):
            core.create_release_from_previous(self.conn, "")

    def test_create_release_clones_snapshots_and_resets_qa(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        next_id = core.create_release_from_previous(self.conn, "next")
        snap = core.get_release(self.conn, next_id)["snapshots"][app_id]
        # QA state is per-release and must reset on clone.
        self.assertEqual(snap["qa_status"], "not_checked")
        # Owner confirmation and test-doc content carry over — re-uploading the
        # same app_info or cloning a release must not silently force owners to
        # redo work they already finished in the previous release.
        self.assertTrue(snap["owner_confirmed"])
        self.assertTrue(all(d.get("dataset") for d in snap["test_docs"]))

    # --- audit ---

    def test_audit_records_app_events(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit", uploaded_by="zhang")
        log = core.app_audit_log(self.conn, app_id)
        events = [r["event"] for r in log]
        self.assertIn("upload_app_info", events)
        self.assertIn("create_app", events)

    def test_transaction_rolls_back_helper_commits(self) -> None:
        release_id, app_id = self.import_initial()

        with self.assertRaisesRegex(RuntimeError, "boom"):
            with core.transaction(self.conn):
                core.audit(self.conn, "rollback probe", app_id=app_id, release_id=release_id, event="rollback_probe")
                core.update_snapshot(
                    self.conn,
                    release_id,
                    app_id,
                    lambda s: s.update({"description": "should rollback"}),
                )
                raise RuntimeError("boom")

        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        events = [e["event"] for e in core.app_audit_log(self.conn, app_id, release_id)]
        self.assertNotEqual(snap.get("description"), "should rollback")
        self.assertNotIn("rollback_probe", events)

    def test_core_commit_calls_are_limited_to_transaction_boundaries(self) -> None:
        allowed = {"transaction", "init_db"}
        current = ""
        offenders = []
        for lineno, line in enumerate((Path("release_system") / "core.py").read_text(encoding="utf-8").splitlines(), start=1):
            if line.startswith("def "):
                current = line.split("def ", 1)[1].split("(", 1)[0]
            if "conn.commit()" in line and current not in allowed:
                offenders.append(f"{lineno}:{current}:{line.strip()}")
        self.assertEqual(offenders, [])

    # --- variant import ---

    def test_initial_import_preserves_multi_version_variants(self) -> None:
        core.import_initial_rows(
            self.conn,
            core.parse_csv_text(DUP_CSV),
            release_name="variant-release",
        )
        apps = {app["id"]: app for app in core.list_apps(self.conn)}
        self.assertIn("foo_1", apps)
        self.assertIn("foo_2", apps)

    def test_initial_import_rejected_when_releases_exist(self) -> None:
        self.import_initial()
        with self.assertRaisesRegex(RuntimeError, "已存在 release"):
            self.import_initial()

    # --- delete app ---

    def test_delete_app_blocks_when_in_locked_release(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        core.final_lock_release(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.delete_app(self.conn, app_id, user="admin", role="Admin")

    def test_delete_app_succeeds_on_unlocked_release(self) -> None:
        _, app_id = self.import_initial()
        deleted = core.delete_app(self.conn, app_id)
        self.assertEqual(deleted["id"], app_id)
        self.assertEqual(core.list_apps(self.conn), [])

    # --- session/auth ---

    def test_logout_session_removes_token(self) -> None:
        token = core.authenticate(self.conn, "rm", "rm")
        self.assertIsNotNone(token)
        core.logout_session(self.conn, token)
        self.assertIsNone(core.session_user(self.conn, token))

    def test_ldap_session_user_includes_display_name(self) -> None:
        token = core.ldap_login_or_create(self.conn, "m123456", "David Brown", ["CN=dl.pde_sa,OU=Groups,DC=example"])
        user = core.session_user(self.conn, token)
        self.assertEqual(user["username"], "m123456")
        self.assertEqual(user["role"], "Owner")
        self.assertEqual(user["display_name"], "David Brown")

    def test_ldap_first_login_role_from_groups(self) -> None:
        owner_token = core.ldap_login_or_create(self.conn, "owner_ldap", "Owner User", ["dl.pde_sc_alpha"])
        qa_token = core.ldap_login_or_create(self.conn, "qa_ldap", "QA User", ["CN=dl.sw_qa_hpc,OU=Groups,DC=example"])
        spd_token = core.ldap_login_or_create(self.conn, "spd_ldap", "SPD User", ["CN=dl.sw_spd_ops,OU=Groups,DC=example"])
        guest_token = core.ldap_login_or_create(self.conn, "guest_ldap", "Guest User", ["CN=other_group,OU=Groups,DC=example"])

        self.assertEqual(core.session_user(self.conn, owner_token)["role"], "Owner")
        self.assertEqual(core.session_user(self.conn, qa_token)["role"], "QA")
        self.assertEqual(core.session_user(self.conn, spd_token)["role"], "SPD")
        self.assertEqual(core.session_user(self.conn, guest_token)["role"], "Guest")

    def test_ldap_existing_user_role_is_preserved(self) -> None:
        first = core.ldap_login_or_create(self.conn, "stable_ldap", "Stable User", ["dl.sw_qa_hpc"])
        self.assertEqual(core.session_user(self.conn, first)["role"], "QA")

        second = core.ldap_login_or_create(self.conn, "stable_ldap", "Stable Renamed", ["dl.pde_sa"])
        user = core.session_user(self.conn, second)
        self.assertEqual(user["role"], "QA")
        self.assertEqual(user["display_name"], "Stable Renamed")

    def test_admin_bootstrap_and_clear_business_data(self) -> None:
        os.environ["HPC_ADMIN_PASSWORD"] = "admin-test-password"
        try:
            source = server.ensure_admin_user(self.conn)
        finally:
            os.environ.pop("HPC_ADMIN_PASSWORD", None)
        self.assertEqual(source, "HPC_ADMIN_PASSWORD")
        self.assertIsNotNone(core.authenticate(self.conn, "admin", "admin-test-password"))
        release_id, _ = self.import_initial()
        self.assertEqual(len(core.list_releases(self.conn)), 1)
        core.clear_business_data(self.conn, user="admin", role="Admin")
        self.assertEqual(core.list_releases(self.conn), [])
        self.assertEqual(core.list_apps(self.conn), [])

    # --- gerrit fetch ---

    def test_fetch_app_info_from_gerrit_records_commit_payload(self) -> None:
        payload = json.dumps(APP_INFO_V1).encode("utf-8")
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("app_info.json")
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))

        def fake_run_git(args, *, timeout=60):
            if args[:2] == ["git", "ls-remote"]:
                return subprocess.CompletedProcess(args, 0, stdout=b"abc123\trefs/heads/maca\n", stderr=b"")
            if args[:2] == ["git", "archive"]:
                return subprocess.CompletedProcess(args, 0, stdout=archive.getvalue(), stderr=b"")
            raise AssertionError(args)

        old = server.run_git
        server.run_git = fake_run_git
        try:
            raw, commit_id = server.fetch_app_info_from_gerrit("ssh://gerrit/repo", "maca")
        finally:
            server.run_git = old
        self.assertEqual(commit_id, "abc123")
        self.assertEqual(json.loads(raw)["app_version"], "22")

    def test_fetch_app_info_from_gerrit_prefixes_hpc_project_url(self) -> None:
        payload = json.dumps(APP_INFO_V1).encode("utf-8")
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("app_info.json")
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))
        calls = []

        def fake_run_git(args, *, timeout=60):
            calls.append(args)
            if args[:2] == ["git", "ls-remote"]:
                return subprocess.CompletedProcess(args, 0, stdout=b"abc123\trefs/heads/maca\n", stderr=b"")
            if args[:2] == ["git", "archive"]:
                return subprocess.CompletedProcess(args, 0, stdout=archive.getvalue(), stderr=b"")
            raise AssertionError(args)

        old = server.run_git
        server.run_git = fake_run_git
        try:
            server.fetch_app_info_from_gerrit("hpc_hpl", "maca")
        finally:
            server.run_git = old

        remote = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/hpc_hpl"
        self.assertEqual(calls[0], ["git", "ls-remote", remote, "maca"])
        self.assertEqual(calls[1][2], f"--remote={remote}")

    def test_fetch_all_app_infos_from_gerrit_updates_release_snapshots(self) -> None:
        release_id, app_id = self.import_initial()
        payload = json.dumps(APP_INFO_V1).encode("utf-8")
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("app_info.json")
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))

        def fake_run_git(args, *, timeout=60):
            if args[:2] == ["git", "ls-remote"]:
                return subprocess.CompletedProcess(args, 0, stdout=b"abc123\trefs/heads/maca\n", stderr=b"")
            if args[:2] == ["git", "archive"]:
                return subprocess.CompletedProcess(args, 0, stdout=archive.getvalue(), stderr=b"")
            raise AssertionError(args)

        old = server.run_git
        server.run_git = fake_run_git
        try:
            result = server.fetch_all_app_infos_from_gerrit(self.conn, release_id, uploaded_by="rm")
        finally:
            server.run_git = old

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed"], 0)
        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertEqual(snap["app_info"]["source_type"], "gerrit_fetch")
        self.assertEqual(snap["app_info"]["commit_id"], "abc123")

    # --- bug-fix regressions ---

    def test_add_new_app_request_rejects_duplicate_git_location(self) -> None:
        release_id, _ = self.import_initial()
        # imported "amber" lives at ssh://gerrit/PDE/HPC/hpc_amber + branch maca;
        # a new app at the same url+branch is rejected regardless of its name.
        with self.assertRaisesRegex(RuntimeError, "已登记"):
            core.add_new_app_request(
                self.conn,
                release_id,
                official_name="Some Other Name",
                git_url="ssh://gerrit/PDE/HPC/hpc_amber",
                git_branch="maca",
                release_decision="release",
                owner="someone",
            )

    def test_add_new_app_request_allows_same_name_different_branch(self) -> None:
        release_id, _ = self.import_initial()
        # same display name as imported "amber" but a different branch -> allowed,
        # id is suffixed so the two apps coexist.
        app_id = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="amber",
            git_url="ssh://gerrit/PDE/HPC/hpc_amber",
            git_branch="release-22",
            release_decision="release",
            owner="someone",
        )
        self.assertNotEqual(app_id, "amber")
        created = core.get_app(self.conn, app_id)
        self.assertEqual(created["git_branch"], "release-22")
        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertEqual(snap["official_name"], "amber")

    def test_add_new_app_request_git_location_is_case_sensitive(self) -> None:
        release_id, _ = self.import_initial()
        # branch "MACA" differs only in case from imported "maca" -> not a duplicate.
        app_id = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="AmberUpper",
            git_url="ssh://gerrit/PDE/HPC/hpc_amber",
            git_branch="MACA",
            release_decision="release",
            owner="someone",
        )
        self.assertTrue(app_id)

    def test_validate_deadline_order_rejects_reversed(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            core.validate_deadline_order("2026-06-10", "2026-06-01")
        # equal, or either side empty, is accepted
        core.validate_deadline_order("2026-06-01", "2026-06-01")
        core.validate_deadline_order("", "2026-06-01")
        core.validate_deadline_order("2026-06-01", "")

    def test_update_release_deadlines_rejects_reversed(self) -> None:
        release_id, _ = self.import_initial()
        with self.assertRaisesRegex(ValueError, "不能晚于"):
            core.update_release_deadlines(
                self.conn,
                release_id,
                app_freeze_deadline="2026-07-01",
                doc_deadline="2026-06-01",
            )

    def test_delete_app_only_clears_artifacts_of_affected_releases(self) -> None:
        release_a, _ = self.import_initial()
        release_b = core.create_release_from_previous(self.conn, "B")
        # OnlyInB is created in release_b and syncs forward only -- release_a never has it.
        only_in_b = core.add_new_app_request(
            self.conn,
            release_b,
            official_name="OnlyInB",
            git_url="ssh://b",
            git_branch="main",
            release_decision="cicd_only",
            owner="ob",
        )
        core.generate_artifacts(self.conn, release_a)
        core.generate_artifacts(self.conn, release_b)
        core.delete_app(self.conn, only_in_b)
        count_a = self.conn.execute("SELECT COUNT(*) FROM artifacts WHERE release_id = ?", (release_a,)).fetchone()[0]
        count_b = self.conn.execute("SELECT COUNT(*) FROM artifacts WHERE release_id = ?", (release_b,)).fetchone()[0]
        self.assertGreater(count_a, 0)
        self.assertEqual(count_b, 0)

    def test_backup_sqlite_produces_readable_copy(self) -> None:
        release_id, _ = self.import_initial()
        backup_path = self.root / "backup.sqlite"
        core.backup_sqlite(self.db_path, backup_path)
        self.assertTrue(backup_path.exists())
        copy = core.connect(backup_path)
        try:
            self.assertEqual(len(core.list_apps(copy)), 1)
            self.assertIn(release_id, {r["id"] for r in core.list_releases(copy)})
        finally:
            copy.close()

    def test_update_snapshot_skip_doc_deadline_allows_decision_change(self) -> None:
        release_id, app_id = self.import_initial(doc_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            # default: every snapshot edit is blocked past the doc deadline
            with self.assertRaisesRegex(RuntimeError, "doc deadline"):
                core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"release_decision": "cicd_only"}))
            # skip_doc_deadline=True: a decision downgrade is still allowed
            core.update_snapshot(
                self.conn,
                release_id,
                app_id,
                lambda s: s.update({"release_decision": "cicd_only"}),
                skip_doc_deadline=True,
            )
        snap = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertEqual(snap["release_decision"], "cicd_only")

    def test_add_new_app_request_allows_cicd_only_after_doc_deadline(self) -> None:
        release_id, _ = self.import_initial(doc_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            app_id = core.add_new_app_request(
                self.conn,
                release_id,
                official_name="LateCicd",
                git_url="ssh://late-cicd",
                git_branch="main",
                release_decision="cicd_only",
                owner="late",
            )
        self.assertEqual(
            core.get_release(self.conn, release_id)["snapshots"][app_id]["release_decision"],
            "cicd_only",
        )

    def test_qa_set_status_batch_applies_all(self) -> None:
        release_id, app_id = self.import_initial()
        other = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="Second",
            git_url="ssh://second",
            git_branch="main",
            release_decision="release",
            owner="o2",
        )
        core.qa_set_status_batch(
            self.conn,
            release_id,
            [
                {"app_id": app_id, "status": "qa_passed"},
                {"app_id": other, "status": "has_issues", "issue_note": "flaky on c500"},
            ],
        )
        snaps = core.get_release(self.conn, release_id)["snapshots"]
        self.assertEqual(snaps[app_id]["qa_status"], "qa_passed")
        self.assertEqual(snaps[other]["qa_status"], "has_issues")
        self.assertEqual(snaps[other]["qa_issue_note"], "flaky on c500")

    def test_official_url_round_trips_and_shows_in_guide(self) -> None:
        release_id, app_id = self.import_initial()
        core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"official_url": "https://amber.example.org"}))
        self.assertEqual(
            core.get_release(self.conn, release_id)["snapshots"][app_id]["official_url"],
            "https://amber.example.org",
        )
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        core.update_snapshot(self.conn, release_id, app_id, _fill_ready)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertIn("https://amber.example.org", artifacts["manual"])

    def test_app_info_after_app_freeze_blocks_qa_scope_expansion(self) -> None:
        release_id, app_id = self.import_initial(app_freeze_deadline="2026-06-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 1)):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        # APP_INFO_V2 adds chip n300 and test path "sanity" -> rejected after freeze
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 7, 1)):
            with self.assertRaisesRegex(RuntimeError, "扩大 QA 范围"):
                core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")

    def test_app_info_after_app_freeze_allows_command_only_change(self) -> None:
        release_id, app_id = self.import_initial(app_freeze_deadline="2026-06-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 1)):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        # same chips, same test path -- only the test_cmd string changes
        tweaked = json.loads(json.dumps(APP_INFO_V1))
        tweaked["app_test"]["run_make_test"]["test_cmd"] = "cd /root/amber22/test && bash test_amber.sh --verbose"
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 7, 1)):
            snap = core.apply_app_info(self.conn, release_id, app_id, tweaked, source="unit")
        self.assertEqual(snap["x86_chips"], "C500,X301")

    def test_app_info_first_upload_after_app_freeze_allowed(self) -> None:
        release_id, app_id = self.import_initial(app_freeze_deadline="2026-06-01")
        # no prior app_info -> no baseline -> first upload is allowed even after freeze
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 7, 1)):
            snap = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        self.assertEqual(snap["x86_chips"], "C500,X301")

    def test_qa_set_status_batch_is_atomic_on_failure(self) -> None:
        release_id, app_id = self.import_initial()
        other = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="Second",
            git_url="ssh://second",
            git_branch="main",
            release_decision="release",
            owner="o2",
        )
        # the second item is invalid (has_issues without a note) -> whole batch rejected
        with self.assertRaisesRegex(ValueError, "问题说明"):
            core.qa_set_status_batch(
                self.conn,
                release_id,
                [
                    {"app_id": app_id, "status": "qa_passed"},
                    {"app_id": other, "status": "has_issues", "issue_note": ""},
                ],
            )
        # the valid item must NOT have slipped through
        snaps = core.get_release(self.conn, release_id)["snapshots"]
        self.assertEqual(snaps[app_id]["qa_status"], "not_checked")
        self.assertEqual(snaps[other]["qa_status"], "not_checked")


    # --- decision sync to later releases ---

    def test_sync_decision_copies_to_later_release(self) -> None:
        r38, app_id = self.import_initial()
        r39 = core.create_release_from_previous(self.conn, "3.9.0")
        result = core.sync_decision_to_later_releases(self.conn, r38, app_id, "cicd_only")
        self.assertEqual(len(result["applied"]), 1)
        snap39 = core.get_release(self.conn, r39)["snapshots"][app_id]
        self.assertEqual(snap39["release_decision"], "cicd_only")

    def test_sync_decision_downgrade_applies_past_doc_deadline(self) -> None:
        r38, app_id = self.import_initial()
        r39 = core.create_release_from_previous(self.conn, "3.9.0", doc_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            core.sync_decision_to_later_releases(self.conn, r38, app_id, "stopped")
        snap39 = core.get_release(self.conn, r39)["snapshots"][app_id]
        self.assertEqual(snap39["release_decision"], "stopped")

    def test_sync_decision_skips_release_upgrade_past_app_freeze(self) -> None:
        r38, app_id = self.import_initial()
        core.create_release_from_previous(self.conn, "3.9.0", app_freeze_deadline="2026-01-01")
        with mock.patch("release_system.core.beijing_now", return_value=dt.datetime(2026, 5, 15)):
            result = core.sync_decision_to_later_releases(self.conn, r38, app_id, "release")
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_sync_decision_skips_locked_release(self) -> None:
        r38, app_id = self.import_initial()
        core.create_release_from_previous(self.conn, "3.9.0")
        core.final_lock_release(self.conn, core.list_releases(self.conn)[-1]["id"])
        result = core.sync_decision_to_later_releases(self.conn, r38, app_id, "stopped")
        self.assertEqual(result["applied"], [])
        self.assertEqual(len(result["skipped"]), 1)


    # --- audit detail ---

    def test_test_docs_diff_reports_field_changes(self) -> None:
        before = [{"id": "t1", "path": "scf", "dataset": "old", "content": "c"}]
        after = [
            {"id": "t1", "path": "scf", "dataset": "new", "content": "c"},
            {"id": "t2", "path": "relax", "owner_added": True},
        ]
        changes = core.test_docs_diff(before, after)
        labels = {c["label"] for c in changes}
        self.assertIn("scf · 测试数据集", labels)
        self.assertTrue(any("relax" in lbl and "新增" in lbl for lbl in labels))
        ds = next(c for c in changes if c["label"] == "scf · 测试数据集")
        self.assertEqual((ds["old"], ds["new"]), ("old", "new"))

    def test_app_info_upload_audit_carries_diff_detail(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="v1")
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="v2")
        uploads = [e for e in core.app_audit_log(self.conn, app_id) if e["event"] == "upload_app_info"]
        self.assertTrue(uploads)
        self.assertTrue(uploads[0]["detail"])


    def test_app_audit_log_filters_by_release(self) -> None:
        r1, app_id = self.import_initial()
        r2 = core.create_release_from_previous(self.conn, "3.9.0")
        core.qa_set_status(self.conn, r2, app_id, "qa_passed")
        r1_events = {e["event"] for e in core.app_audit_log(self.conn, app_id, r1)}
        r2_events = {e["event"] for e in core.app_audit_log(self.conn, app_id, r2)}
        self.assertIn("create_app", r1_events)
        self.assertNotIn("qa_set_status", r1_events)
        self.assertIn("qa_set_status", r2_events)
        self.assertNotIn("create_app", r2_events)
        self.assertGreaterEqual(len(core.app_audit_log(self.conn, app_id)), len(r1_events) + len(r2_events))

    def test_release_qa_audit_logs_groups_status_changes(self) -> None:
        release_id, app_id = self.import_initial()
        other = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="Second",
            git_url="ssh://second",
            git_branch="main",
            release_decision="release",
            owner="o2",
        )
        untouched = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="Third",
            git_url="ssh://third",
            git_branch="main",
            release_decision="release",
            owner="o3",
        )
        core.qa_upload_log(self.conn, self.db_path, release_id, b"log", "qa.log")
        core.qa_set_status_batch(
            self.conn,
            release_id,
            [
                {"app_id": app_id, "status": "qa_passed"},
                {"app_id": other, "status": "has_issues", "issue_note": "flaky on c500"},
            ],
            user="qa_user",
            role="QA",
        )

        logs = core.release_qa_audit_logs(self.conn, release_id)
        self.assertEqual(set(logs), {app_id, other, untouched})
        self.assertEqual(logs[untouched], [])
        self.assertEqual([entry["event"] for entry in logs[app_id]], ["qa_set_status"])
        self.assertEqual(logs[app_id][0]["app_id"], app_id)
        self.assertEqual(logs[app_id][0]["user"], "qa_user")
        self.assertTrue(any(d["field"] == "qa_status" for d in logs[other][0]["detail"]))
        self.assertNotIn("qa_upload_log", [entry["event"] for values in logs.values() for entry in values])

    def test_app_audit_access_allows_rm_admin_qa_and_current_owner(self) -> None:
        release_id, app_id = self.import_initial()

        for username, role in [("rm", "RM"), ("admin", "Admin"), ("qa", "QA"), ("张三", "Owner")]:
            handler = object.__new__(server.Handler)
            handler._conn = self.conn
            handler.conn = lambda: self.conn
            handler.user = lambda username=username: username
            handler.role = lambda role=role: role
            handler.require_app_audit_access(app_id, release_id)

    def test_app_audit_access_rejects_other_users(self) -> None:
        release_id, app_id = self.import_initial()
        handler = object.__new__(server.Handler)
        handler._conn = self.conn
        handler.conn = lambda: self.conn
        handler.user = lambda: "other_owner"
        handler.role = lambda: "Owner"

        with self.assertRaises(server.AuthzError):
            handler.require_app_audit_access(app_id, release_id)


    def test_clone_records_per_app_origin(self) -> None:
        r1, app_id = self.import_initial()
        r2 = core.create_release_from_previous(self.conn, "3.8.0")
        r2_log = core.app_audit_log(self.conn, app_id, r2)
        self.assertTrue(any(e["event"] == "clone_app" for e in r2_log))

    def test_new_app_sync_records_origin_in_later_release(self) -> None:
        r1, _ = self.import_initial()
        r2 = core.create_release_from_previous(self.conn, "3.8.0")
        app_id = core.add_new_app_request(
            self.conn, r1, official_name="a", git_url="ssh://a", git_branch="main",
            release_decision="release", owner="oa",
        )
        src_log = core.app_audit_log(self.conn, app_id, r1)
        later_log = core.app_audit_log(self.conn, app_id, r2)
        self.assertTrue(any(e["event"] == "create_app" for e in src_log))
        self.assertTrue(any(e["event"] == "sync_app" for e in later_log))


class WikiCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test.db"
        self.conn = core.connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_wiki_articles_can_be_created_pinned_updated_and_soft_deleted(self) -> None:
        first = wiki_core.save_article(
            self.conn,
            title="普通文章",
            body_md="hello **wiki**",
            pinned=False,
            user="rm",
            role="RM",
        )
        second = wiki_core.save_article(
            self.conn,
            title="置顶文章",
            body_md="# pinned",
            pinned=True,
            user="admin",
            role="Admin",
        )

        rows = wiki_core.list_articles(self.conn)
        self.assertEqual([second["id"], first["id"]], [row["id"] for row in rows])
        self.assertNotIn("body_md", rows[0])

        updated = wiki_core.save_article(
            self.conn,
            article_id=first["id"],
            title="普通文章 v2",
            body_md="updated",
            pinned=True,
            user="rm",
            role="RM",
        )
        self.assertTrue(updated["pinned"])
        self.assertEqual("updated", wiki_core.get_article(self.conn, first["id"])["body_md"])

        wiki_core.delete_article(self.conn, second["id"], user="admin", role="Admin")
        self.assertNotIn(second["id"], [row["id"] for row in wiki_core.list_articles(self.conn)])
        with self.assertRaises(KeyError):
            wiki_core.get_article(self.conn, second["id"])

    def test_wiki_write_requires_rm_or_admin(self) -> None:
        with self.assertRaises(PermissionError):
            wiki_core.save_article(
                self.conn,
                title="Owner draft",
                body_md="not allowed",
                user="owner_test",
                role="Owner",
            )

    def test_wiki_images_can_be_saved_and_retrieved(self) -> None:
        image = wiki_core.save_image(
            self.conn,
            content=b"\x89PNG\r\n\x1a\n",
            filename="paste.png",
            content_type="image/png",
            user="rm",
            role="RM",
        )

        self.assertTrue(image["url"].startswith("/api/wiki/images/"))
        loaded = wiki_core.get_image(self.conn, image["id"])
        self.assertEqual("paste.png", loaded["filename"])
        self.assertEqual("image/png", loaded["content_type"])
        self.assertEqual(b"\x89PNG\r\n\x1a\n", loaded["content"])

    def test_wiki_images_reject_unsupported_types_and_non_writers(self) -> None:
        with self.assertRaises(ValueError):
            wiki_core.save_image(
                self.conn,
                content=b"<svg></svg>",
                filename="bad.svg",
                content_type="image/svg+xml",
                user="rm",
                role="RM",
            )
        with self.assertRaises(PermissionError):
            wiki_core.save_image(
                self.conn,
                content=b"\x89PNG\r\n\x1a\n",
                filename="owner.png",
                content_type="image/png",
                user="owner_test",
                role="Owner",
            )


if __name__ == "__main__":
    unittest.main()
