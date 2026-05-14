from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

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
    "app_build": {},
    "app_test": {
        "arm_only": {
            "test_cmd": "run-arm-test",
            "supported_chip": {"c500": ["ubuntu20.04_aarch64"]},
            "enabled": True,
        }
    },
}


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
        self.assertIn("c500", snapshot["x86_chips"])
        self.assertEqual(len(snapshot["test_docs"]), 1)

        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertIn("owner 未确认", blockers)
        self.assertTrue(any("测试数据集" in item for item in blockers))

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
                    "test_method": "运行 app_info 中所有 test_cmd",
                    "test_result": "查看 /root/amber22/test/log",
                }
            )
            for doc in snapshot["test_docs"]:
                doc.update(
                    {
                        "dataset": "amber sample dataset",
                        "content": "make test",
                        "result_view": "查看 test/log",
                        "pass_criteria": "exit code 0",
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
                    "test_method": "run tests",
                    "test_result": "check logs",
                }
            )
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "data", "content": "content", "result_view": "logs", "pass_criteria": "pass"})

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
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e", "test_method": "t", "test_result": "r"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p"})
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
        rows = core.release_rows(self.conn, core.get_release(self.conn, release_id), admitted_only=True)
        self.assertEqual(rows[0][0]["name"], original_name)
        self.assertNotEqual(rows[0][0]["name"], "Changed Amber")
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        with self.assertRaises(RuntimeError):
            core.generate_artifacts(self.conn, release_id, final=False)
        plan = core.gerrit_push_plan(self.conn, release_id)
        self.assertFalse(plan["ready"])

    def test_lock_requires_qa_admission(self) -> None:
        release_id, _ = self.import_initial()
        with self.assertRaises(RuntimeError):
            core.lock_release(self.conn, release_id)

    def test_lock_requires_qa_passed_not_just_in_qa(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e", "test_method": "t", "test_result": "r"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p"})

        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.lock_release(self.conn, release_id)
        release = core.get_release(self.conn, release_id)
        self.assertEqual(release["state"], "qa_open")
        with self.assertRaises(RuntimeError):
            core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "bad"}))

    def test_qa_open_blocks_direct_key_change(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def ready(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e", "test_method": "t", "test_result": "r"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p"})

        core.update_snapshot(self.conn, release_id, app_id, ready)
        core.run_admission_check(self.conn, release_id)
        core.open_qa(self.conn, release_id)
        with self.assertRaises(RuntimeError):
            core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V2, source="unit")
        with self.assertRaises(RuntimeError):
            core.update_snapshot(self.conn, release_id, app_id, lambda s: s.update({"version": "bad"}))

    def test_cicd_only_requires_infra_fields(self) -> None:
        release_id, app_id = self.import_initial()

        def cicd(snapshot: dict) -> None:
            snapshot["release_decision"] = "cicd_only"

        core.update_snapshot(self.conn, release_id, app_id, cicd)
        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertIn("缺少可追溯 AppInfoSnapshot", blockers)
        self.assertIn("缺少 CICD build 配置", blockers)

    def test_owner_added_test_requires_command_and_docs(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")

        def add_owner_test(snapshot: dict) -> None:
            snapshot["owner_confirmed"] = True
            snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e", "test_method": "t", "test_result": "r"})
            for doc in snapshot["test_docs"]:
                doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p"})
            snapshot["test_docs"].append({"id": "owner", "path": "owner_added.1", "owner_added": True, "command": "", "dataset": "", "content": "", "result_view": "", "pass_criteria": ""})

        core.update_snapshot(self.conn, release_id, app_id, add_owner_test)
        blockers = core.run_admission_check(self.conn, release_id)[app_id]
        self.assertTrue(any("owner-added 测试命令" in item for item in blockers))

    def test_arm_supported_chip_is_not_reported_as_x86(self) -> None:
        parsed = core.parse_app_info(APP_INFO_ARM)
        self.assertIn("c500", parsed["arm_chips"])
        self.assertNotIn("c500", parsed["x86_chips"])

    def test_new_app_request_three_fields(self) -> None:
        release_id, _ = self.import_initial()
        app_id = core.add_new_app_request(
            self.conn,
            release_id,
            official_name="New Model",
            git_url="ssh://gerrit/new",
            git_branch="maca",
            owner="李四",
        )
        app = core.get_app(self.conn, app_id)
        self.assertEqual(app["owners"], ["李四"])
        self.assertEqual(app["git_url"], "ssh://gerrit/new")


if __name__ == "__main__":
    unittest.main()
