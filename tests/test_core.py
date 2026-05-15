from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import server
from release_system import core


RELEASE_CSV = """app_name,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch
amber,22,"c500,x301",x201,x86,20260511-695,ssh://gerrit/PDE/HPC/hpc_amber,maca
"""

OWNER_CSV = """类别,id,名称,Owner,类型,描述,对应官方版本,X86支持芯片系列,ARM支持芯片类型,备注,开发者社区发布情况,开发者社区发布包支持python版本,开发者社区发布包支持的底层框架及版本,ARM / Kylin sanity,Ubuntu sanity / 兼容性sanity
HPC APP,1,Amber,张三,分子动力学,Amber app,22,"c500,x301",,,,,,,
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

DUP_RELEASE_CSV = """app_name,app_version,maca_chip,hpcc_chip,arch,maca_version,git_url,git_branch
foo,1,c500,,x86,3.7.0,ssh://gerrit/foo,main
foo,1,c600,,arm,3.7.0,ssh://gerrit/foo,main
foo,2,n300,,x86,3.7.0,ssh://gerrit/foo,release-2
"""

DUP_OWNER_CSV = """类别,id,名称,Owner,类型,描述,对应官方版本,X86支持芯片系列,ARM支持芯片类型,备注,开发者社区发布情况,开发者社区发布包支持python版本,开发者社区发布包支持的底层框架及版本,ARM / Kylin sanity,Ubuntu sanity / 兼容性sanity
HPC APP,1,foo,Alice,solver,Foo v1,1,,,,,,,
HPC APP,2,foo,Bob,solver,Foo v2,2,,,,,,,
"""


class CoreWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release_csv = self.root / "release.csv"
        self.owner_csv = self.root / "owner.csv"
        self.release_csv.write_text(RELEASE_CSV, encoding="utf-8")
        self.owner_csv.write_text(OWNER_CSV, encoding="utf-8")
        self.conn = core.connect(":memory:")

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def import_initial(self) -> tuple[str, str]:
        release_id = core.import_initial(self.conn, self.release_csv, self.owner_csv, alias_text="amber=Amber")
        app_id = core.normalize_name("amber")
        return release_id, app_id

    def test_initial_import_and_app_info_gate(self) -> None:
        release_id, app_id = self.import_initial()
        apps = core.list_apps(self.conn)
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0]["owners"], ["张三"])

        snapshot = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        self.assertEqual(snapshot["version"], "22")
        self.assertIn("C500", snapshot["x86_chips"])
        self.assertEqual(len(snapshot["test_docs"]), 1)

        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertIn("owner 未确认", blockers)
        self.assertTrue(any("测试数据集" in item for item in blockers))

    def test_refresh_release_status_updates_live_blockers_without_admission(self) -> None:
        release_id, app_id = self.import_initial()
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["state"], "owner_filling")
        self.assertEqual(release["snapshots"][app_id]["blockers"], [])

        live = core.refresh_release_status(self.conn, release_id)
        self.assertTrue(any("AppInfoSnapshot" in item for item in live[app_id]))
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["state"], "owner_filling")
        self.assertEqual(release["snapshots"][app_id]["qa_status"], "blocked")
        self.assertTrue(any("AppInfoSnapshot" in item for item in release["snapshots"][app_id]["blockers"]))

        core.update_snapshot(self.conn, release_id, app_id, lambda snapshot: snapshot.update({"owner_confirmed": True}))
        live = core.refresh_release_status(self.conn, release_id)
        self.assertTrue(any("AppInfoSnapshot" in item for item in live[app_id]))
        self.assertFalse(any(item == "owner 未确认" for item in live[app_id]))

    def test_complete_snapshot_can_enter_qa_and_generate_rst(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def fill(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update(
                {
                    "image_usage": "docker run amber",
                    "binary_usage": "tar xf amber.tar.xz",
                    "env_setup": "export MACA_PATH=/opt/maca",
                }
            )
            for doc in snapshot["test_docs"]:
                doc.update(
                    {
                        "dataset": "amber sample dataset",
                        "content": "make test",
                        "result_view": "查看 test/log",
                        "pass_criteria": "exit code 0",
                        "stale": False,
                    }
                )

        core.update_snapshot(self.conn, release_id, app_id, fill)
        result = core.run_admission_check(self.conn, release_id)
        self.assertEqual(result[app_id], [])
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["snapshots"][app_id]["qa_status"], "eligible")

        artifacts = core.generate_artifacts(self.conn, release_id)
        self.assertIn("Amber", artifacts["release_note"])
        self.assertIn("amber sample dataset", artifacts["manual"])
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=True)
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=True, from_lock=True)

    def test_new_release_app_info_diff_blocks_until_confirmed(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def make_ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update(
                {
                    "image_usage": "docker run amber",
                    "binary_usage": "tar xf amber.tar.xz",
                    "env_setup": "export MACA_PATH=/opt/maca",
                }
            )
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "data", "content": "content", "result_view": "logs", "pass_criteria": "pass", "stale": False})

        core.update_snapshot(self.conn, release_id, app_id, make_ready)
        next_release = core.create_release_from_previous(self.conn, "next")
        snapshot = core.apply_app_info(self.conn, next_release, app_id, APP_INFO_V2, source="unit")
        self.assertGreaterEqual(len(snapshot["app_info_diffs"]), 3)
        self.assertTrue(any(doc["stale"] for doc in snapshot["test_docs"] if doc["path"] == "run_make_test"))

        blockers = core.run_admission_check(self.conn, next_release)[app_id]
        self.assertIn("app_info 差异未确认", blockers)

        def confirm(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            for diff in snapshot["app_info_diffs"]:
                diff["confirmed"] = True
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "data", "content": "content", "result_view": "logs", "pass_criteria": "pass", "stale": False})

        core.update_snapshot(self.conn, next_release, app_id, confirm)
        blockers = core.run_admission_check(self.conn, next_release)[app_id]
        self.assertEqual(blockers, [])

    def test_lock_makes_snapshot_immutable(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        core.mark_qa_passed(self.conn, release_id, app_id)
        original_name = core.get_app(self.conn, app_id)["name"]
        artifacts = core.lock_release(self.conn, release_id)
        self.assertIn("Amber", artifacts["release_note"])
        locked_data = json.loads(artifacts["data"])
        self.assertEqual(locked_data["release"]["state"], "release_locked")
        self.assertTrue(locked_data["release"]["snapshots"][app_id]["locked"])
        app = core.get_app(self.conn, app_id)
        app["name"] = "Changed Amber"
        core.save_app(self.conn, app)
        rows = core.release_rows(self.conn, core.get_release(self.conn, release_id))
        self.assertEqual(rows[0][0]["name"], original_name)
        self.assertNotEqual(rows[0][0]["name"], "Changed Amber")
        next_release = core.create_release_from_previous(self.conn, "next")
        next_rows = core.release_rows(self.conn, core.get_release(self.conn, next_release))
        self.assertEqual(next_rows[0][0]["name"], "Changed Amber")
        self.assertNotIn("app_meta", core.get_release(self.conn, next_release)["snapshots"][app_id])
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=False)
        plan = core.gerrit_push_plan(self.conn, release_id)
        self.assertFalse(plan["ready"])

    def test_lock_allows_blocked_apps(self) -> None:
        """Lock succeeds even when apps have unresolved blockers; blocked apps stay unlocked."""
        release_id, app_id = self.import_initial()
        # Don't do admission — app has blockers
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        artifacts = core.lock_release(self.conn, release_id)
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["state"], "release_locked")
        snapshot = release["snapshots"][app_id]
        self.assertFalse(snapshot.get("locked", False))
        self.assertNotIn(app_id, [a["name"] for a, _ in core.release_rows(self.conn, release, final=True)])

    def test_lock_skips_in_qa_apps(self) -> None:
        """Lock succeeds but in_qa (not passed) apps are not locked."""
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})

        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        # Don't mark QA passed — app is still in_qa
        artifacts = core.lock_release(self.conn, release_id)
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["state"], "release_locked")
        snapshot = release["snapshots"][app_id]
        self.assertFalse(snapshot.get("locked", False))
        # Release note should be empty (no QA-passed apps)
        self.assertNotIn("Amber", artifacts["release_note"])

    def test_qa_open_blocks_direct_key_change(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})

        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        with self.assertRaises(RuntimeError):
            core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "bad"}))

    def test_cicd_only_requires_app_info_but_not_infra_fields(self) -> None:
        release_id, app_id = self.import_initial()

        def cicd(snapshot: dict) -> None:
            snapshot["release_decision"] = "cicd_only"

        core.update_snapshot(self.conn, release_id, app_id, cicd)
        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertIn("缺少可追溯 AppInfoSnapshot", blockers)
        self.assertFalse(any("CICD" in item or "Infra" in item for item in blockers))

    def test_owner_added_test_requires_command_and_docs(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def add_owner_test(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
            snapshot["test_docs"].append({"id": "owner", "path": "owner_added.1", "owner_added": True, "command": "", "dataset": "", "content": "", "result_view": "", "pass_criteria": ""})

        core.update_snapshot(self.conn, release_id, app_id, add_owner_test)
        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertTrue(any("owner-added 测试命令" in item for item in blockers))

    def test_disabled_tests_excluded_from_parse_and_chips(self) -> None:
        parsed = core.parse_app_info(APP_INFO_DISABLED)
        self.assertIn("C500", parsed["x86_chips"])
        self.assertIn("X301", parsed["x86_chips"])
        self.assertNotIn("N300", parsed["x86_chips"])
        test_paths = [t["path"] for t in parsed["tests"]]
        self.assertIn("run_make_test", test_paths)
        self.assertNotIn("disabled_test", test_paths)

    def test_disabled_tests_excluded_from_test_docs(self) -> None:
        release_id, app_id = self.import_initial()
        snapshot = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_DISABLED, source="unit")
        doc_paths = [d["path"] for d in snapshot["test_docs"]]
        self.assertIn("run_make_test", doc_paths)
        self.assertNotIn("disabled_test", doc_paths)

    def test_weekly_tests_excluded_from_parse(self) -> None:
        parsed = core.parse_app_info(APP_INFO_WEEKLY)
        test_paths = [t["path"] for t in parsed["tests"]]
        self.assertIn("daily_test", test_paths)
        self.assertNotIn("weekly_test", test_paths)

    def test_app_info_upload_updates_chips(self) -> None:
        release_id, app_id = self.import_initial()
        snap_before = core.get_release(self.conn, release_id)["snapshots"][app_id]
        self.assertEqual(snap_before["x86_chips"], "c500,x301")
        snapshot = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        self.assertEqual(snapshot["x86_chips"], "C500,N300,X301")

    def test_arm_supported_chip_is_not_reported_as_x86(self) -> None:
        parsed = core.parse_app_info(APP_INFO_ARM)
        self.assertIn("C500", parsed["arm_chips"])
        self.assertNotIn("C500", parsed["x86_chips"])

    def test_app_info_source_metadata_and_arm_snapshot_update(self) -> None:
        release_id, app_id = self.import_initial()
        snapshot = core.apply_app_info(
            self.conn,
            release_id,
            app_id,
            APP_INFO_ARM,
            source="app_info.json",
            source_type="owner_upload",
            uploaded_by="owner_test",
        )
        self.assertEqual(snapshot["arm_chips"], "C500")
        self.assertEqual(snapshot["x86_chips"], "")
        self.assertEqual(snapshot["app_info"]["source_type"], "owner_upload")
        self.assertEqual(snapshot["app_info"]["uploaded_by"], "owner_test")

        snapshot = core.apply_app_info(
            self.conn,
            release_id,
            app_id,
            APP_INFO_V1,
            source="ssh://gerrit/repo maca:app_info.json",
            source_type="gerrit_fetch",
            commit_id="abc123",
        )
        self.assertEqual(snapshot["app_info"]["source_type"], "gerrit_fetch")
        self.assertEqual(snapshot["app_info"]["commit_id"], "abc123")

    def test_new_app_request_requires_release_decision(self) -> None:
        release_id, _ = self.import_initial()
        app_id = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="New Model",
            git_url="ssh://gerrit/new",
            git_branch="maca",
            release_decision="cicd_only",
            doc_target="ai4sci",
            owner="李四",
        )
        app = core.get_app(self.conn, app_id)
        self.assertEqual(app["owners"], ["李四"])
        self.assertEqual(app["git_url"], "ssh://gerrit/new")
        self.assertEqual(app["doc_target"], "ai4sci")
        self.assertEqual(core.get_release(self.conn, release_id)["snapshots"][app_id]["release_decision"], "cicd_only")
        with self.assertRaises(ValueError):
            core.add_new_app_request(
                self.conn,
                release_id,
                official_name="Bad Model",
                git_url="ssh://gerrit/bad",
                git_branch="maca",
                release_decision="",
                owner="李四",
            )

    def test_update_release_deadline(self) -> None:
        release_id, _ = self.import_initial()
        release = core.update_release_deadline(self.conn, release_id, "2026-06-01", user="rm", role="RM")
        self.assertEqual(release["deadline"], "2026-06-01")
        with self.assertRaises(ValueError):
            core.update_release_deadline(self.conn, release_id, "20260601")

    def test_no_release_is_normalized_to_stopped(self) -> None:
        release_id, app_id = self.import_initial()

        def no_release(snapshot: dict) -> None:
            snapshot["release_decision"] = "no_release"

        core.update_snapshot(self.conn, release_id, app_id, no_release)
        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertEqual(blockers, [])
        self.assertEqual(core.get_release(self.conn, release_id)["snapshots"][app_id]["release_decision"], "stopped")

    def test_delete_app_removes_unlocked_snapshots_and_blocks_locked_releases(self) -> None:
        release_id, app_id = self.import_initial()
        core.generate_artifacts(self.conn, release_id)
        deleted = core.delete_app(self.conn, app_id, user="admin", role="Admin")
        self.assertEqual(deleted["id"], app_id)
        self.assertEqual(core.list_apps(self.conn), [])
        self.assertEqual(core.get_release(self.conn, release_id)["snapshots"], {})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)

        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})

        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        core.mark_qa_passed(self.conn, release_id, app_id)
        core.lock_release(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.delete_app(self.conn, app_id, user="admin", role="Admin")

    def test_initial_import_preserves_multi_version_variants_and_combines_arches(self) -> None:
        release_id = core.import_initial_rows(
            self.conn,
            core.parse_csv_text(DUP_RELEASE_CSV),
            core.parse_csv_text(DUP_OWNER_CSV),
            release_name="variant-release",
        )
        apps = {app["id"]: app for app in core.list_apps(self.conn)}
        self.assertIn("foo_1", apps)
        self.assertIn("foo_2", apps)
        self.assertEqual(apps["foo_1"]["owners"], ["Alice"])
        self.assertEqual(apps["foo_2"]["owners"], ["Bob"])

        release = core.get_release(self.conn, release_id)
        snap_v1 = release["snapshots"]["foo_1"]
        self.assertEqual(snap_v1["x86_chips"], "c500")
        self.assertEqual(snap_v1["arm_chips"], "c600")
        self.assertEqual(apps["foo_2"]["git_branch"], "release-2")

    def test_default_debug_users_admin_bootstrap_and_clear_business_data(self) -> None:
        self.assertIsNotNone(core.authenticate(self.conn, "rm", "rm"))
        self.assertIsNotNone(core.authenticate(self.conn, "owner_test", "owner_test"))
        os.environ["HPC_ADMIN_PASSWORD"] = "admin-test-password"
        try:
            source = server.ensure_admin_user(self.conn)
        finally:
            os.environ.pop("HPC_ADMIN_PASSWORD", None)
        self.assertEqual(source, "HPC_ADMIN_PASSWORD")
        self.assertIsNotNone(core.authenticate(self.conn, "admin", "admin-test-password"))

        release_id, _ = self.import_initial()
        self.assertEqual(len(core.list_releases(self.conn)), 1)
        self.assertEqual(len(core.list_apps(self.conn)), 1)
        core.clear_business_data(self.conn, user="admin", role="Admin")
        self.assertEqual(core.list_releases(self.conn), [])
        self.assertEqual(core.list_apps(self.conn), [])
        self.assertIsNotNone(core.authenticate(self.conn, "owner_test", "owner_test"))
        self.assertIsNotNone(core.authenticate(self.conn, "admin", "admin-test-password"))

    def test_logout_session_removes_token(self) -> None:
        token = core.authenticate(self.conn, "rm", "rm")
        self.assertIsNotNone(token)
        self.assertEqual(core.session_user(self.conn, token)["username"], "rm")
        core.logout_session(self.conn, token)
        self.assertIsNone(core.session_user(self.conn, token))

    def test_fetch_app_info_from_gerrit_records_commit_payload(self) -> None:
        payload = json.dumps(APP_INFO_V1).encode("utf-8")
        archive = BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            info = tarfile.TarInfo("app_info.json")
            info.size = len(payload)
            tar.addfile(info, BytesIO(payload))

        def fake_run_git(args: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
            if args[:2] == ["git", "ls-remote"]:
                return subprocess.CompletedProcess(args, 0, stdout=b"abc123\trefs/heads/maca\n", stderr=b"")
            if args[:2] == ["git", "archive"]:
                self.assertIn("abc123", args)
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


    def test_guide_rows_new_app_excluded_when_blocked(self) -> None:
        """A brand-new app that is blocked should NOT appear in the guide."""
        release_id, app_id = self.import_initial()
        # App has no prior locked release — it's new.
        # Don't do QA pass so it stays blocked.
        release = core.get_release(self.conn, release_id)
        active, stopped = core.guide_rows(self.conn, release, "manual")
        self.assertEqual(active, [])
        self.assertEqual(stopped, [])

    def test_guide_rows_new_app_included_when_qa_passed(self) -> None:
        """A new app with QA passed appears in the guide."""
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        core.mark_qa_passed(self.conn, release_id, app_id)
        release = core.get_release(self.conn, release_id)
        active, stopped = core.guide_rows(self.conn, release, "manual")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0][0]["name"], "amber")

    def test_guide_rows_previously_published_app_uses_last_locked_when_blocked(self) -> None:
        """A previously locked app falls back to its last locked snapshot when blocked."""
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        core.mark_qa_passed(self.conn, release_id, app_id)
        core.lock_release(self.conn, release_id)
        # Create next release — app is cloned but not QA'd
        next_release = core.create_release_from_previous(self.conn, "next")
        next_rel = core.get_release(self.conn, next_release)
        active, stopped = core.guide_rows(self.conn, next_rel, "manual")
        self.assertEqual(len(active), 1)
        # Should use data from the locked first release
        self.assertEqual(active[0][1].get("version"), "22")

    def test_guide_rows_stopped_app_at_end(self) -> None:
        """Stopped apps appear in stopped_rows with last-published data."""
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        core.mark_qa_passed(self.conn, release_id, app_id)
        core.lock_release(self.conn, release_id)
        # Create next release, mark app as stopped
        next_release = core.create_release_from_previous(self.conn, "next")
        core.update_snapshot(self.conn, next_release, app_id, lambda s: s.update({"release_decision": "stopped"}))
        next_rel = core.get_release(self.conn, next_release)
        active, stopped = core.guide_rows(self.conn, next_rel, "manual")
        self.assertEqual(active, [])
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0][1].get("version"), "22")

    def test_render_guide_stopped_section_marker(self) -> None:
        """Stopped entries get (已停止支持) in their heading."""
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {"version": "1.0", "doc": {}, "test_docs": []}
        out = core.render_guide("Guide", [], stopped_rows=[(app, snapshot)])
        self.assertIn("TestApp（已停止支持）", out)

    def test_release_note_excludes_non_passed_apps_in_final(self) -> None:
        """Final release note only includes QA-passed apps."""
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})
        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        # Don't pass QA — app is in_qa but not passed
        final_rows = core.release_rows(self.conn, core.get_release(self.conn, release_id), final=True)
        self.assertEqual(final_rows, [])
        # Preview still includes it
        preview_rows = core.release_rows(self.conn, core.get_release(self.conn, release_id))
        self.assertEqual(len(preview_rows), 1)


if __name__ == "__main__":
    unittest.main()
