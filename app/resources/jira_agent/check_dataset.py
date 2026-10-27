"""Read-only probe sent to an execution machine; uses Python's standard library."""
import base64
import json
import os
import subprocess
import sys


def check(policy):
    path = policy["path"]
    if not path:
        return {"status": "unknown", "code": "not_configured", "message": "未配置共享数据目录及检查标准"}
    if not os.path.isdir(path):
        return {"status": "fail", "code": "missing", "message": "数据目录不存在或不可访问"}
    if not os.access(path, os.R_OK | os.X_OK):
        return {"status": "fail", "code": "permission", "message": "执行账号无法读取数据目录"}
    try:
        found = subprocess.run(["findmnt", "--json", "--target", os.path.realpath(path),
                                "--output", "TARGET,SOURCE,FSTYPE"],
                               capture_output=True, text=True, timeout=5)
        mounts = json.loads(found.stdout).get("filesystems", [])
        # autofs may return its placeholder followed by the mounted filesystem
        # at the same target. Prefer the deepest real mount, then the top layer.
        def filesystems(entries):
            for entry in entries:
                yield entry
                yield from filesystems(entry.get("children", []))
        actual = [m for m in filesystems(mounts) if m.get("fstype") != "autofs"]
        mount = max(enumerate(actual), key=lambda pair: (len(pair[1].get("target", "")), pair[0]))[1] if found.returncode == 0 and actual else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        mount = None
    if not mount:
        return {"status": "unknown", "code": "mount_unknown", "message": "无法读取实际挂载信息"}
    evidence = {k: str(mount.get(k, ""))[:500] for k in ("target", "source", "fstype")}
    source = evidence["source"]
    if policy["sources"] and source not in policy["sources"] and source.split("[", 1)[0] not in policy["sources"]:
        return {"status": "fail", "code": "source_mismatch", "message": "实际挂载来源与预期不符", "mount": evidence}
    if not policy["sources"]:
        return {"status": "unknown", "code": "source_unconfigured", "message": "目录可访问，尚未配置预期共享来源", "mount": evidence}
    if not policy["probe"]:
        return {"status": "unknown", "code": "probe_unconfigured", "message": "挂载来源匹配，尚未配置读取探测文件", "mount": evidence}
    root = os.path.realpath(path)
    probe = os.path.realpath(os.path.join(root, policy["probe"]))
    if os.path.commonpath([root, probe]) != root:
        return {"status": "fail", "code": "probe_escape", "message": "探测文件指向数据目录之外", "mount": evidence}
    try:
        with open(probe, "rb") as handle:
            content = handle.read(4097)
    except OSError:
        return {"status": "fail", "code": "probe_unreadable", "message": "探测文件不存在或不可读", "mount": evidence}
    if policy["expected"] and (len(content) > 4096 or content.decode("utf-8", "replace").strip() != policy["expected"]):
        return {"status": "fail", "code": "identity_mismatch", "message": "数据标识与预期不符", "mount": evidence}
    return {"status": "pass", "code": "readable", "message": "挂载来源匹配，探测文件可读", "mount": evidence}


if __name__ == "__main__":
    try:
        result = check(json.loads(base64.b64decode(sys.argv[1])))
    except Exception:
        result = {"status": "unknown", "code": "probe_error", "message": "数据检查未能完成"}
    print(json.dumps(result, ensure_ascii=False))
