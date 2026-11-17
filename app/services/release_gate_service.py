"""Release admission reads the enrolled snapshot, never syncs source or changes QA locks."""
import re

from app.api.errors import ApiError
from app.db.connection import transaction
from app.identity import repo_storage_path
from app.repositories import app_info_repo, apps_repo, entries_repo, releases_repo, release_gate_repo
from app.repositories.audit_repo import log_audit
from app.timeutil import beijing_timestamp

VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+")
COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")


def save_version(conn, body, user):
    version = body.maca_version.strip()
    if not VERSION.fullmatch(version):
        raise ValueError("MACA_VERSION 必须是完整四段版本，例如 3.10.2.11")
    with transaction(conn, immediate=True):
        release = releases_repo.get_release_row(conn, body.release_id)
        if not release or release["name"] != version.rsplit(".", 1)[0]:
            raise ValueError("完整版本必须对应同一发布版本，例如 3.10.2.11 对应 3.10.2")
        old = release_gate_repo.get_version(conn, version)
        if (old and old["revision"] != body.expected_revision) or (not old and body.expected_revision != -1):
            raise ApiError(409, "准入配置已变化，请刷新后重试")
        if old and old["release_id"] != body.release_id:
            raise ValueError("已登记版本不能改绑其他发布周期")
        if body.enabled and release["released_locked"]:
            raise ValueError("发布版本已封存，不能启用构建准入")
        ts = beijing_timestamp()
        release_gate_repo.save_version(conn, version, body.release_id, body.enabled, body.note.strip(),
                                       (old["revision"] + 1) if old else 0, user["username"], ts)
        log_audit(conn, f"Release 构建准入：{version} {'启用' if body.enabled else '停用'}", ts=ts,
                  user=user["username"], role=user["role"], release_id=body.release_id,
                  event="release_build_admission", detail={"before": old, "after": release_gate_repo.get_version(conn, version)})
    return {"versions": release_gate_repo.list_versions(conn)}


def _repo(value):
    value = value.strip().rstrip("/")
    kind = "repo" if value.endswith(".xml") else "git"
    value = repo_storage_path(kind, value)
    return value[:-4] if kind == "git" and value.endswith(".git") else value


def check(conn, body):
    evidence = {"maca_version": body.maca_version, "git_url": body.git_url, "git_branch": body.git_branch,
                "phase": body.phase, "actual_commit": body.actual_commit, "source_project": body.source_project}

    def result(decision, reason, message):
        return {"decision": decision, "reason": reason, "message": message, "evidence": dict(evidence)}

    if body.build_type != "release":
        return result("allow", "not_release", "非 release 构建不执行发布准入检查")
    # One read transaction makes the version, signup, lock and baseline a consistent snapshot.
    with transaction(conn):
        version = release_gate_repo.get_version(conn, body.maca_version)
        if not version or not version["enabled"]:
            return result("block", "version_unauthorized", "触发了未经授权的 release 版本，请 DevOps 核对触发参数并联系 RM")
        release = releases_repo.get_release_row(conn, version["release_id"])
        if not release:
            return result("block", "service_error", "准入配置关联的发布版本不存在，需 RM 核实")
        if release["name"] != body.maca_version.rsplit(".", 1)[0]:
            return result("block", "service_error", "准入版本与当前发布版本名称不一致，需 RM 核实")
        evidence.update(release_id=release["id"], release_name=release["name"], version_revision=version["revision"])
        branch = body.git_branch.removeprefix("refs/heads/")
        matches = [app for app in apps_repo.list_apps(conn)
                   if _repo(app["git_url"]) == _repo(body.git_url) and app["git_branch"] == branch]
        if len(matches) > 1:
            return result("block", "service_error", "仓库和分支匹配多个 APP，需 RM 核实")
        if not matches:
            return result("skip", "app_not_enrolled", "该仓库和分支不在本期报名列表，正常跳过")
        app = matches[0]
        owners = app.get("owners") or []
        evidence.update(app_id=app["id"], official_name=app.get("official_name") or app["id"],
                        code_owner=owners[0] if owners else "")
        entry = entries_repo.find_entry(conn, release["id"], app["id"])
        if not entry or entry["status"] != "active":
            return result("skip", "app_not_enrolled", "APP 未报名本期发布或已撤回，正常跳过")
        evidence.update(entry_id=entry["id"], build_lock_revision=entry["build_lock_revision"],
                        build_locked=bool(entry["build_locked"]), snapshot_id=entry["locked_version_id"])
        if entry["build_locked"] or release["released_locked"]:
            return result("block", "build_locked", "APP 已锁定构建或版本已封存；如需重新构建，请联系 QA/RM 按发布流程解锁")
        snapshot = app_info_repo.get_version(conn, entry["locked_version_id"])
        expected = str(snapshot.get("commit_id") or "").strip().lower() if snapshot else ""
        if not snapshot or snapshot["app_id"] != app["id"] or not COMMIT.fullmatch(expected):
            return result("block", "service_error", "本期报名缺少有效的工作台 commit 基线，需 RM 核实")
        evidence.update(expected_commit=expected, app_version=snapshot["raw"].get("app_version", ""))
        if body.phase == "preflight":
            return result("allow", "preflight_passed", "版本、报名和构建锁检查通过，待 checkout 后核对 commit")
        if body.baseline_commit.lower() != expected:
            return result("block", "service_error", "检查期间工作台 commit 基线发生变化，请重新检查构建")
        resolved = body.resolved_expected_commit.lower()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", body.actual_commit):
            return result("block", "service_error", "缺少有效的实际 checkout commit，需 RM 核实")
        if not re.fullmatch(r"[0-9a-f]{40}", resolved) or not resolved.startswith(expected):
            return result("block", "service_error", "工作台 commit 无法在实际仓库中唯一解析，需 RM 核实")
        if body.actual_commit.lower() != resolved:
            return result("block", "commit_mismatch", "工作台本期 commit 与 Jenkins 实际代码版本不一致，请 Code Owner 核对并在工作台同步更新")
        return result("allow", "passed", "Release 构建准入检查通过")
