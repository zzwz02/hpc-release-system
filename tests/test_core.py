from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest import mock

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


def _fill_ready(snapshot: dict) -> None:
    snapshot["owner_confirmed"] = True
    snapshot["doc"].update({"image_usage": "i", "binary_usage": "b", "env_setup": "e"})
    for doc in snapshot["test_docs"]:
        doc.update({"dataset": "d", "content": "c", "result_view": "r", "pass_criteria": "p", "stale": False})


class CoreWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.release_csv = self.root / "release.csv"
        self.owner_csv = self.root / "owner.csv"
        self.release_csv.write_text(RELEASE_CSV, encoding="utf-8")
        self.owner_csv.write_text(OWNER_CSV, encoding="utf-8")
        self.db_path = self.root / "test.db"
        self.conn = core.connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def import_initial(self, **kwargs) -> tuple[str, str]:
        release_id = core.import_initial(self.conn, self.release_csv, self.owner_csv, alias_text="amber=Amber", **kwargs)
        app_id = core.normalize_name("amber")
        return release_id, app_id

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
        self.assertEqual(apps[0]["owners"], ["张三"])
        release = core.get_release(self.conn, release_id)
        self.assertFalse(release["released_locked"])
        self.assertIn(app_id, release["snapshots"])
        self.assertEqual(release["snapshots"][app_id]["qa_status"], "not_checked")

    def test_default_users_include_qa(self) -> None:
        self.assertIsNotNone(core.authenticate(self.conn, "qa", "qa"))

    # --- app_info upload ---

    def test_app_info_upload_uppercases_chips(self) -> None:
        release_id, app_id = self.import_initial()
        snapshot = core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        self.assertEqual(snapshot["x86_chips"], "C500,X301")
        self.assertEqual(len(snapshot["test_docs"]), 1)

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
        meta = core.qa_upload_log(self.conn, self.db_path, release_id, b"hello-log", "test.log", user="qa", role="QA")
        self.assertEqual(meta["filename"], "test.log")
        self.assertTrue(Path(meta["storage_path"]).exists())
        self.assertEqual(Path(meta["storage_path"]).read_bytes(), b"hello-log")
        stored = core.get_qa_log(self.conn, release_id)
        self.assertEqual(stored["filename"], "test.log")
        self.assertEqual(stored["uploaded_by"], "qa")

    def test_qa_log_upload_replaces_previous(self) -> None:
        release_id, _ = self.import_initial()
        core.qa_upload_log(self.conn, self.db_path, release_id, b"first", "a.log", user="qa", role="QA")
        core.qa_upload_log(self.conn, self.db_path, release_id, b"second", "b.log", user="qa", role="QA")
        meta = core.get_qa_log(self.conn, release_id)
        self.assertEqual(meta["filename"], "b.log")
        self.assertEqual(Path(meta["storage_path"]).read_bytes(), b"second")

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
        """Missing items don't block final lock; they only appear in missing_items."""
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
        # Preview includes the app (no QA needed)
        self.assertEqual(len(core.release_rows(self.conn, release, final=False)), 1)
        # Final excludes because no owner_confirmed + no QA pass
        self.assertEqual(core.release_rows(self.conn, release, final=True), [])

    def test_guide_rows_stopped_section(self) -> None:
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
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0][1].get("version"), "22")

    def test_render_guide_stopped_marker(self) -> None:
        app = {"name": "TestApp", "description": "desc", "doc_target": "manual"}
        snapshot = {"version": "1.0", "doc": {}, "test_docs": []}
        out = core.render_guide("Guide", [], stopped_rows=[(app, snapshot)])
        self.assertIn("TestApp（已停止支持）", out)

    def test_generate_artifacts_preview_ok_after_unlock(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit")
        # Preview works on un-locked release
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
        self.assertTrue(any("Owner 未确认" in x for x in items))
        self.assertTrue(any("QA 未测试" in x for x in items))

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
        # Confirm all diffs
        def confirm_diffs(snap):
            for d in snap.get("app_info_diffs", []):
                d["confirmed"] = True
        core.update_snapshot(self.conn, release_id, app_id, confirm_diffs)
        core.qa_set_status(self.conn, release_id, app_id, "qa_passed")
        items = core.refresh_missing_items(self.conn, release_id)[app_id]
        self.assertEqual(items, [])

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
        self.assertEqual(snap["qa_status"], "not_checked")
        self.assertFalse(snap["owner_confirmed"])
        # test_docs reset to stale
        self.assertTrue(all(d["stale"] for d in snap["test_docs"]))

    # --- audit ---

    def test_audit_records_app_events(self) -> None:
        release_id, app_id = self.import_initial()
        core.apply_app_info(self.conn, release_id, app_id, APP_INFO_V1, source="unit", uploaded_by="zhang")
        log = core.app_audit_log(self.conn, app_id)
        events = [r["event"] for r in log]
        self.assertIn("upload_app_info", events)
        self.assertIn("create_app", events)

    # --- variant import ---

    def test_initial_import_preserves_multi_version_variants(self) -> None:
        release_id = core.import_initial_rows(
            self.conn,
            core.parse_csv_text(DUP_RELEASE_CSV),
            core.parse_csv_text(DUP_OWNER_CSV),
            release_name="variant-release",
        )
        apps = {app["id"]: app for app in core.list_apps(self.conn)}
        self.assertIn("foo_1", apps)
        self.assertIn("foo_2", apps)

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


if __name__ == "__main__":
    unittest.main()
