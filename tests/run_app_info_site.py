"""Isolated App detail browser checks with local Gerrit responses."""
import argparse
import json
import os
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--signup", action="store_true", help="Include signup dialog scenarios")
    parser.add_argument("--lookup", action="store_true", help="Include Harbor image lookup scenarios")
    parser.add_argument("--image-publications", action="store_true", help="Include multiple publications of one digest")
    parser.add_argument("--platforms", action="store_true", help="Include OS/architecture/chip mapping scenarios")
    parser.add_argument("--releases", action="store_true", help="Include frozen publication history scenarios")
    parser.add_argument("--legacy-history", action="store_true", help="Include entry-only migrated publication history")
    parser.add_argument("--dependencies", action="store_true", help="Include paired Python/PyTorch dependency scenarios")
    parser.add_argument("--latest-running", action="store_true", help="Include latest successful running App publications")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="app-info-site-") as temporary:
        folder = Path(temporary)
        os.environ.update(
            DB_PATH=str(folder / "main.db"), ADMIN_PASSWORD_FILE=str(folder / "admin"),
            RUNTIME_CONF_PATH=str(folder / "missing.conf"),
            ASSISTANT_DATABASE_URL=f"sqlite:///{folder}/assistant.db",
            JIRA_AGENT_DATABASE_URL=f"sqlite:///{folder}/agent.db",
            JIRA_AGENT_DATA_DIR=str(folder / "data"),
            JIRA_AGENT_RUNNER_ENABLED="false", JIRA_LEARNING_RUNNER_ENABLED="false",
            JIRA_TESTING_RUNNER_ENABLED="false", APP_INFO_SYNC_ENABLED="false",
        )
        from app.config import settings
        from app.db.connection import connect
        from app.domain.authn import hash_password
        from app.integrations import gerrit, harbor
        from app.main import create_app
        from app.services import catalog_service
        from tests.conftest import close_qa, close_signup, make_app_info, make_dependency_app_info, make_platform_app_info, seed_app, seed_cycle, seed_signup, make_harbor_registry, seed_legacy_qa
        import uvicorn

        register_artifacts, saved_artifact = make_harbor_registry()
        harbor.get_artifact = saved_artifact
        conn = connect(settings.db_path)
        for username, role in [
            ("app_rm", "RM"), ("app_owner", "Owner"), ("app_other", "Owner"),
            ("app_qa", "QA"), ("app_guest", "Guest"), ("app_spd", "SPD"),
        ]:
            conn.execute("INSERT INTO users(username,password_hash,role,display_name) VALUES (?,?,?,?)",
                         (username, hash_password("test-password"), role, username))
        conn.commit()
        for app_id in ["info-check", "info-error"]:
            seed_app(conn, app_id, official_name=app_id, owners=["app_owner"], app_type="性能基准测试")
        catalog_service.update_profile(
            conn, "info-check", {"description": "保留的手册描述", "official_url": "https://example.test"},
            user="app_rm", role="RM",
        )
        if args.platforms:
            platform_info = make_platform_app_info()
            seed_app(conn, "platform-check", official_name="HPCG", app_info=platform_info,
                     commit_id="b641bd0deac7c59ecb6a53c814ed3c150b7811a4")
            from app.domain import delivery_images
            platform_builds = delivery_images.builds(platform_info)
            register_artifacts([{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/hpcg-maca@sha256:{digit * 64}"}
                                for build in platform_builds if build["os"] == "ubuntu20.04"
                                for digit in (["a"] if build["arch"] == "amd64" else ["b"])])
        if args.dependencies:
            from app.services import cycle_service, qa_service

            for app_id in ["runtime-published", "runtime-unpublished", "runtime-stopped", "runtime-unknown"]:
                runtime_info = make_dependency_app_info(app_id)
                if app_id == "runtime-unknown":
                    runtime_info.pop("app_dependency")
                seed_app(conn, app_id, official_name=app_id, owners=["app_owner"], app_info=runtime_info,
                         commit_id="0123456789abcdef0123456789abcdef01234567",
                         cicd_status="stopped" if app_id == "runtime-stopped" else "running")
            dependency_release = seed_cycle(conn, name="runtime-published-cycle")
            seed_signup(conn, dependency_release, "runtime-unknown")
            qa_service.set_qa_status_batch(conn, dependency_release, [{"app_id": "runtime-unknown", "status": "qa_passed"}],
                                           user="app_qa", role="QA")
            entry = seed_signup(conn, dependency_release, "runtime-published")
            images = [
                {**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{digit * 64}"}
                for build, digit in zip((build for build in entry["delivery_builds"] if build["os"] != "ubuntu24.04"), "abcde")
            ]
            register_artifacts(images)
            qa_service.set_qa_status_batch(conn, dependency_release, [{"app_id": "runtime-published", "status": "qa_passed",
                                                                     "deliverable_images": images, "delivery_revision": entry["delivery_revision"]}],
                                           user="app_qa", role="QA")
            close_qa(conn, dependency_release)
            cycle_service.publish(conn, dependency_release, user="app_rm", role="RM")
            # An older publication has no publication-time manifest; reads must label reconstruction.
            conn.execute("UPDATE official_versions SET test_manifest_json=NULL WHERE app_id='runtime-unknown'")
            conn.commit()
            development_info = make_dependency_app_info("runtime-published")
            development_info["app_dependency"] = ["development-sdk"]
            for build in development_info["app_build"].values():
                build.update(python_label="py313", pytorch_label="torch2.10")
            catalog_service.store_app_info(conn, "runtime-published", development_info, commit_id="development-runtime", source="test", user="app_rm")
        if args.latest_running:
            from app.services import cycle_service, qa_service

            for name in ("amber", "hpl", "stopped"):
                seed_app(conn, name, official_name=name.upper(), app_info=make_dependency_app_info(name))
            for version in ("3.9", "3.10"):
                release_id = seed_cycle(conn, name=version)
                for name in ("amber", "hpl", "stopped"):
                    entry = seed_signup(conn, release_id, name, target_version=f"{name}-{version}")
                    images = [{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{str(index + 1) * 64}"}
                              for index, build in enumerate(entry["delivery_builds"])]
                    register_artifacts(images)
                    qa_service.set_qa_status_batch(conn, release_id, [{"app_id": name,
                        "status": "cannot_release" if name == "amber" and version == "3.10" else "qa_passed",
                        "issue_note": "QA 测试未通过" if name == "amber" and version == "3.10" else "",
                        "deliverable_images": images, "delivery_revision": entry["delivery_revision"]}], user="app_qa", role="QA")
                close_qa(conn, release_id)
                cycle_service.publish(conn, release_id, user="app_rm", role="RM")
            conn.execute("UPDATE apps SET cicd_status='stopped' WHERE id='stopped'")
            conn.commit()
        if args.releases or args.legacy_history:
            from app.services import cycle_service, qa_service

            for app_id in ["release-pass", "release-issues", "release-failed", "release-withdrawn", "release-missing"]:
                seed_app(conn, app_id, official_name=app_id, owners=["app_owner"])
            for index, name in enumerate(["history-empty", "history-3.9.0", "history-3.10.0"]):
                release_id = seed_cycle(conn, name=name)
                if index:
                    items = []
                    for app_id in ["release-pass", "release-issues", "release-failed", "release-withdrawn", "release-missing"]:
                        entry = seed_signup(conn, release_id, app_id, target_version=f"{index}.0")
                        if app_id == "release-withdrawn":
                            cycle_service.withdraw(conn, entry["id"], user="app_rm", role="RM")
                            continue
                        status = "has_issues" if app_id == "release-issues" else "cannot_release" if app_id == "release-failed" else "qa_passed"
                        item = {"app_id": app_id, "status": status, "issue_note": f"版本 {index} 的 QA 说明"}
                        if app_id != "release-missing":
                            item.update(deliverable_image=f"harbor.sh.mxcr.io/hpc-release/{app_id}:v{index}",
                                        deliverable_digest="sha256:" + str(index) * 64)
                        items.append(item)
                    seed_legacy_qa(conn, release_id, items, user="app_qa", role="QA")
                close_qa(conn, release_id)
                cycle_service.publish(conn, release_id, user="app_rm", role="RM")
                if args.legacy_history and name == "history-3.9.0":
                    conn.execute("DELETE FROM official_versions WHERE release_id=?", (release_id,))
                    conn.commit()
        for name in ["browser-cycle-a", "browser-cycle-b"]:
            release_id = seed_cycle(conn, name=name)
            seed_signup(conn, release_id, "info-check")
            if args.platforms:
                seed_signup(conn, release_id, "platform-check")
            close_signup(conn, release_id)
        if args.lookup:
            historical = make_app_info(app_name="abacus", app_version="3.9.0.27", tests={"sanity": ["C500"], "dataset": ["C500"]})
            historical["app_build"]["ubuntu22.04_amd64"] = historical["app_build"].pop("ubuntu20.04_amd64")
            historical["app_build"]["ubuntu22.04_amd64"]["os"] = "ubuntu22.04"
            for cfg in historical["app_test"].values():
                cfg["supported_chip"] = {"C500": ["ubuntu22.04_amd64"]}
                cfg["img_target"] = "release"
                cfg["mount_dataset"] = True
                cfg["container_args"] = "--device=/dev/dri -u 0"
            seed_app(conn, "lookup-image", app_info=make_app_info(app_name="abacus", app_version="4.0"))
            from app.api.errors import ApiError
            def lookup_artifact(project, repository, digest):
                if repository.startswith("published-image"):
                    raise RuntimeError("Published image queries must not access Harbor")
                if repository not in ("abacus-maca", "hpcg-maca"):
                    return saved_artifact(project, repository, digest)
                if digest == "sha256:" + "e" * 64:
                    raise ValueError("Harbor 中未找到该镜像，请检查镜像名和 digest")
                if digest == "sha256:" + "d" * 64:
                    raise ApiError(502, "Harbor 查询失败（ReadTimeout），请稍后重试")
                tags = [] if digest == "sha256:" + "f" * 64 else [
                    "3.9.0.27-bb46c60-ubuntu22.04-amd64-3012",
                    "3.9.0.27-3.10.1.2-ubuntu22.04-amd64-3012",
                    "3.9.0.27-3.10.1.2-ubuntu22.04-amd64",
                ]
                architecture = "amd64"
                if repository == "hpcg-maca":
                    architecture = "arm64" if digest == "sha256:" + "b" * 64 else "amd64"
                    platform_tag = "ubuntu20.04-arm64" if architecture == "arm64" else "kylinv11-amd64"
                    tags = [f"0.9.0-bb46c60-{platform_tag}-3012"]
                return {"digest": digest, "tags": [{"name": tag} for tag in tags], "extra_attrs": {"architecture": architecture}}

        if args.image_publications:
            from app.services import cycle_service, qa_service

            seed_app(conn, "image-published", official_name="Published Image", app_info=None, owners=["app_owner"])
            for index in (1, 2):
                info = make_dependency_app_info("published-image")
                info["app_version"] = f"{index}.0"
                info["app_test"]["sanity"]["test_cmd"] = f"run-original-{index}"
                info["app_test"]["extra"] = {**info["app_test"]["sanity"], "test_cmd": f"extra-original-{index}"}
                catalog_service.store_app_info(conn, "image-published", info, commit_id=str(index) * 40, source="test", user="app_rm")
                release_id = seed_cycle(conn, name=f"image-publication-{index}")
                entry = seed_signup(conn, release_id, "image-published", target_version=f"{index}.0", excluded_tests=["extra"] if index == 1 else [])
                images = [{**build, "reference": f"harbor.sh.mxcr.io/hpc-release/{build['image_pattern'].partition(':')[0]}@sha256:{digit * 64}"}
                          for build, digit in zip(entry["delivery_builds"], "abcdef")]
                register_artifacts(images)
                qa_service.set_qa_status_batch(conn, release_id, [{"app_id": "image-published", "status": "has_issues", "issue_note": f"original-QA-{index}",
                    "delivery_revision": entry["delivery_revision"], "deliverable_images": images}], user="app_qa", role="QA")
                close_qa(conn, release_id)
                cycle_service.publish(conn, release_id, user="app_rm", role="RM")
        if args.signup:
            from app.services import cicd_service

            for app_id in ["own-ready-a", "own-ready-b", "own-race", "own-stopped", "own-missing", "own-stopping", "other-ready"]:
                seed_app(
                    conn, app_id, official_name=app_id,
                    owners=["app_other"] if app_id == "other-ready" else ["app_owner"],
                    cicd_status="stopped" if app_id == "own-stopped" else "running",
                    app_info=None if app_id == "own-missing" else make_app_info(app_version="2.0"),
                )
            cicd_service.submit_request(
                conn, task_id="own-stopping", request_type="modify",
                payload={"status": {"old": "running", "new": "stopped"}},
                submitter="app_owner", submitter_role="Owner", submitter_display="Owner", source="app_workbench",
            )
            for index, name in enumerate(["signup-open-a", "signup-open-b"]):
                release_id = seed_cycle(conn, name=name)
                catalog = [
                    {"component": "Driver", "version": f"driver-{index}", "note": "驱动说明"},
                    {"component": "SDK", "version": f"sdk-{index}", "note": ""},
                    {"component": "PyTorch", "version": f"torch-{index}", "note": ""},
                ]
                from app.services import cycle_service

                cycle_service.save_environment(conn, release_id, catalog, user="app_rm", role="RM")
                if index == 0:
                    seed_signup(conn, release_id, "own-ready-b")
        if args.lookup:
            harbor.get_artifact = lookup_artifact
        conn.close()

        def head_commit(url, *_args, **_kwargs):
            if url.endswith("hpc_info-error"):
                raise RuntimeError("Gerrit 测试连接失败")
            return "expanded-head"

        gerrit.head_commit = head_commit
        def fetch_app_info(url, commit, **_kwargs):
            if args.image_publications and url.endswith("hpc_image-published"):
                raise RuntimeError("Published image queries must not access Gerrit")
            if args.platforms and url.endswith("hpc_platform-check"):
                return json.dumps(platform_info)
            if args.lookup and url.endswith("hpc_lookup-image"):
                return json.dumps(historical)
            return json.dumps(make_app_info(app_version="2.0", chips=["c500", "c600"]))

        gerrit.fetch_app_info_at = fetch_app_info
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
