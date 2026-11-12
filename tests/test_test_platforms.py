"""OS/architecture/chip joins shared by app_info, QA and historical images."""
from copy import deepcopy

from app.domain import app_info, image_lookup, test_commands
from app.db.connection import transaction
from app.repositories import entries_repo
from app.services import catalog_service, cycle_service, lookup_service, qa_service
from tests.conftest import close_signup, make_platform_app_info as hpcg_info, seed_app, seed_cycle, seed_signup


def test_screenshot_mapping_keeps_os_and_arch_chip_sets_separate():
    parsed = app_info.parse_app_info(hpcg_info())
    benchmark, arm = parsed["tests"]
    assert [(p["os"], p["arch"]) for p in benchmark["platforms"]] == [("ubuntu20.04", "amd64"), ("kylinv11", "amd64")]
    assert benchmark["platforms"][1]["chips"] == ["C600", "X201"]
    assert "C500" in benchmark["platforms"][0]["chips"]
    assert [(p["arch"], p["chips"]) for p in arm["platforms"]] == [("arm64", ["C500"])]
    assert parsed["arm_chips"] == ["C500", "C600", "X302", "X201"]


def test_command_generator_never_cross_products_a_test_into_unreferenced_builds():
    rows = test_commands.command_rows(hpcg_info())
    assert len(rows) == 3
    assert {row["build"] for row in rows if row["test"] == "benchmark"} == {"ubuntu20.04_amd64", "kylinv11_amd64"}
    kylin = next(row for row in rows if row["os"] == "kylinv11")
    assert kylin["chips"] == ["C600", "X201"]
    assert "kylinv11-amd64" in kylin["command"]
    arm = next(row for row in rows if row["test"] == "benchmark_arm")
    assert arm["arch"] == "arm64" and arm["chips"] == ["C500"]
    assert "ubuntu20.04-arm64" in arm["command"]


def test_invalid_disabled_and_unsupported_chip_references_do_not_become_commands():
    raw = hpcg_info()
    raw["app_test"]["benchmark"]["supported_chip"] = {"C500": ["kylinv11_amd64"], "X201": ["missing_alias"]}
    raw["app_build"]["ubuntu20.04_arm64"]["enabled"] = "false"
    assert test_commands.command_rows(raw) == []
    assert app_info.parse_app_info(raw)["tests"][0]["platforms"] == []


def test_legacy_chip_lists_intersect_each_build_and_arch_aliases_are_normalized():
    raw = hpcg_info()
    raw["app_test"] = {"test": {"test_cmd": "run", "supported_chip": ["c500"]}}
    raw["app_build"]["ubuntu20.04_arm64"]["arch"] = "aarch64"
    rows = test_commands.command_rows(raw)
    assert {(row["os"], row["arch"], tuple(row["chips"])) for row in rows} == {
        ("ubuntu20.04", "amd64", ("C500",)), ("ubuntu20.04", "arm64", ("C500",)),
    }


def test_legacy_build_alias_can_supply_an_omitted_os_and_x86_64_architecture():
    raw = {"app_build": {"ubuntu22.04_x86_64": {"supported_chip": ["c500"]}},
           "app_test": {"test": {"test_cmd": "run", "supported_chip": {"C500": ["ubuntu22.04_x86_64"]}}}}
    parsed = app_info.parse_app_info(raw)
    assert parsed["x86_chips"] == ["C500"] and parsed["arm_chips"] == []
    assert parsed["tests"][0]["platforms"][0]["arch"] == "amd64"
    assert parsed["tests"][0]["platforms"][0]["os"] == "ubuntu22.04"


def test_referencing_another_os_or_arch_for_an_existing_chip_requires_qa_approval():
    raw = hpcg_info()
    widened = deepcopy(raw)
    widened["app_test"]["benchmark"]["supported_chip"]["C500"].append("ubuntu20.04_arm64")
    additions = app_info.qa_scope_additions(app_info.parse_app_info(raw), app_info.parse_app_info(widened))
    assert len(additions) == 1
    assert "新增平台芯片" in additions[0] and "arm64" in additions[0] and "C500" in additions[0]
    assert app_info.qa_scope_additions(app_info.parse_app_info(widened), app_info.parse_app_info(raw)) == []


def test_python_build_aliases_remain_distinct_on_the_same_os_and_arch():
    raw = hpcg_info()
    raw["app_build"]["torch24_py310"] = {**raw["app_build"]["ubuntu20.04_amd64"], "python_label": "py310", "pytorch_label": "torch2.4"}
    raw["app_test"] = {"test": {"test_cmd": "run", "supported_chip": {"C500": ["torch24_py310"]}}}
    rows = test_commands.command_rows(raw)
    assert len(rows) == 1
    assert rows[0]["build"] == "torch24_py310"
    assert "torch2.4-py310-ubuntu20.04-amd64" in rows[0]["command"]


def test_app_history_and_qa_reports_use_the_locked_platforms_and_chips(temp_db):
    raw = hpcg_info()
    app_id = seed_app(temp_db, app_info=raw)
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    summary = catalog_service.version_summary({"id": 1, "raw": raw})
    record = lookup_service.by_app(temp_db, app_id)["entries"][0]
    assert record["app_info"]["x86_chips"] == summary["x86_chips"]
    assert record["app_info"]["arm_chips"] == summary["arm_chips"]
    assert {p["build"]: p for p in record["scope_tests"][0]["platforms"]} == {
        p["build"]: p for p in summary["tests"][0]["platforms"]
    }
    report = qa_service.get_qa_reports(temp_db, release_id)["test_cmd"]
    rows = [dict(zip(report["columns"], row)) for row in report["rows"]]
    assert "base_image" not in report["columns"]
    assert "构建平台" not in report["columns"]
    assert {row["commit_id"] for row in rows} == {"commit0"}
    assert len(rows) == len(record["test_commands"]) == 3
    assert next(row for row in rows if row["OS"] == "kylinv11")["支持芯片"] == "C600,X201"
    assert next(row for row in record["test_commands"] if row["os"] == "kylinv11")["chips"] == ["C600", "X201"]


def test_image_scope_only_contains_the_platform_of_the_queried_artifact():
    result = image_lookup.historical_image_commands(
        hpcg_info(), image="harbor.sh.mxcr.io/hpc-release/hpcg-maca@sha256:" + "a" * 64,
        repository="hpcg-maca", tags=["0.9.0-abcdef1-kylinv11-amd64-42"], architecture="amd64",
    )
    assert result["x86_chips"] == ["C600", "X201"] and result["arm_chips"] == []
    assert len(result["test_commands"]) == 1
    assert result["test_commands"][0]["chips"] == ["C600", "X201"]


def test_new_platform_for_an_existing_chip_keeps_the_signup_locked_until_qa_approves(temp_db):
    raw = hpcg_info()
    app_id = seed_app(temp_db, app_info=raw)
    release_id = seed_cycle(temp_db)
    entry = seed_signup(temp_db, release_id, app_id)
    close_signup(temp_db, release_id)
    widened = deepcopy(raw)
    widened["app_test"]["benchmark"]["supported_chip"]["C500"].append("ubuntu20.04_arm64")
    with transaction(temp_db):
        version_id = catalog_service.store_app_info(temp_db, app_id, widened, commit_id="new-platform", source="test", user="owner")
    current = entries_repo.get_entry(temp_db, entry["id"])
    assert current["locked_version_id"] == entry["locked_version_id"]
    request = entries_repo.pending_scope_request(temp_db, entry["id"])
    assert request and "arm64" in " ".join(request["additions"])
    assert len(lookup_service.by_app(temp_db, app_id)["entries"][0]["test_commands"]) == 3
    report = qa_service.get_qa_reports(temp_db, release_id)["test_cmd"]
    assert {row[report["columns"].index("commit_id")] for row in report["rows"]} == {"commit0"}
    cycle_service.decide_scope_request(temp_db, request["id"], approve=True, note="", user="qa", role="QA")
    current = entries_repo.get_entry(temp_db, entry["id"])
    assert current["locked_version_id"] == version_id
    assert len(lookup_service.by_app(temp_db, app_id)["entries"][0]["test_commands"]) == 4
    report = qa_service.get_qa_reports(temp_db, release_id)["test_cmd"]
    assert {row[report["columns"].index("commit_id")] for row in report["rows"]} == {"new-platform"}


def test_qa_preserves_build_aliases_that_carry_additional_configuration(temp_db):
    raw = hpcg_info()
    raw["app_build"] = {"torch24_py310": {
        **raw["app_build"]["ubuntu20.04_amd64"], "python_label": "py310", "pytorch_label": "torch2.4",
    }}
    raw["app_test"] = {"test": {"test_cmd": "run", "supported_chip": {"C500": ["torch24_py310"]}}}
    app_id = seed_app(temp_db, app_info=raw, commit_id="a" * 40)
    release_id = seed_cycle(temp_db)
    seed_signup(temp_db, release_id, app_id)
    report = qa_service.get_qa_reports(temp_db, release_id)["test_cmd"]
    row = dict(zip(report["columns"], report["rows"][0]))
    assert row["构建平台"] == "torch24_py310"
    assert row["commit_id"] == "a" * 40
