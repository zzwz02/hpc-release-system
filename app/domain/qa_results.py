"""Flat command contracts, legacy history identities and versioned QA result keys."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from decimal import Decimal, InvalidOperation

from app.domain.container_image import parse_base_image_reference
from app.domain.test_commands import denormalize_arch, image_tag_matches, replace_command_image, select_image_tag

KEY_COLUMNS = ("app_name", "git_branch", "app_version", "arch", "OS", "GPU", "test_name")
COMMAND_COLUMNS = [
    "app_name",
    "git_branch",
    "app_version",
    "arch",
    "OS",
    "GPU",
    "commit_id",
    "test_name",
    "docker_cmd",
    "perf_golden",
    "perf_unit",
    "Python label",
    "PyTorch label",
]
RESULT_COLUMNS = ["test_result", "test_digest", "test_env"]
COLUMNS = COMMAND_COLUMNS + RESULT_COLUMNS


def hpc_families(version: str) -> set[str]:
    return {'.'.join(value.strip().split('.')[:3]) for value in str(version or '').split(',')
            if re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', value.strip())}

# One worksheet contract for version queries, CSV downloads and QA uploads.
WORKSHEET_QA_COLUMNS = ["qa_digest", "qa_docker_cmd", "qa_result", "qa_env", "qa_status", "qa_comment"]
WORKSHEET_METADATA_COLUMNS = [
    "app_name", "git_branch", "hpc_version", "app_version", "arch", "OS", "GPU",
    "py_label", "torch_label", "test_name", "perf_golden", "perf_unit",
]
WORKSHEET_COLUMNS = [*WORKSHEET_METADATA_COLUMNS, "website_docker_cmd", *WORKSHEET_QA_COLUMNS]
REPORT_METADATA_COLUMNS = {"Excel 提交时间": "uploaded_at", "提交人": "uploaded_by", "原始 Excel": "filename"}
REPORT_COLUMNS = [*WORKSHEET_COLUMNS, *REPORT_METADATA_COLUMNS]
WORKSHEET_ALIASES = {
    "支持芯片": "GPU", "Python label": "py_label", "PyTorch label": "torch_label",
    "test_result": "qa_result", "test_digest": "qa_digest", "test_env": "qa_env",
    "docker_cmd": "qa_docker_cmd",
}


def canonical_result(row: dict) -> dict:
    data = dict(row)
    for old, new in WORKSHEET_ALIASES.items():
        if old in data:
            data.setdefault(new, data.pop(old))
    data.setdefault("qa_digest_gerrit_commit_id", data.pop("commit_id", ""))
    return {**dict.fromkeys(WORKSHEET_COLUMNS, ""), **data}


def report_matches(row: dict, *, searches=(), column_filters=None) -> bool:
    """Match displayed cell values literally, across the complete result set."""
    def text(column):
        value = row.get(column)
        return ("" if value is None else str(value)).casefold()

    return all(not needle.strip() or any(needle.strip().casefold() in text(column)
                                         for column in REPORT_COLUMNS) for needle in searches) and all(
        value.strip().casefold() in text(column) for column, value in (column_filters or {}).items())


def report_sort_key(row: dict, column: str):
    value = row.get(column)
    text = "" if value is None else str(value)
    return [(0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", text)]


def performance_metadata_matches(row: dict, expected: dict, column: str) -> bool:
    """Excel numeric cells may format the same baseline as 1, 1.0 or 1e0."""
    actual = str(row.get(column, "")).strip()
    value = str(expected.get(column, "")).strip()
    if column == "perf_golden":
        try:
            left, right = Decimal(actual), Decimal(value)
            if left.is_finite() and right.is_finite():
                return left == right
        except InvalidOperation:
            pass
    return actual == value


def worksheet_commands(result: dict, *, hpc_version: str | None = None) -> list[dict]:
    """QA cells are always blank, even when a historical result exists."""
    rows = []
    commands = [{**command, "command": _website_command(result, command, hpc_version)}
                for command in result["test_commands"]]
    for flat in flat_commands({**result, "test_commands": commands}):
        row = canonical_result(flat)
        row["hpc_version"] = result.get("hpc_version", "") if hpc_version is None else hpc_version
        row["website_docker_cmd"] = flat["docker_cmd"]
        row.update(dict.fromkeys(WORKSHEET_QA_COLUMNS, ""))
        rows.append({column: row[column] for column in WORKSHEET_COLUMNS})
    return rows


def _website_command(result: dict, command: dict, hpc_version: str | None) -> str:
    reference = result.get("image", "")
    if not reference:
        return command["command"]  # The caller marks missing-image rows no_image.
    pattern = command["image_pattern"].partition(":")[2]
    wanted = {value.strip() for value in str(
        result.get("hpc_version", "") if hpc_version is None else hpc_version).split(",") if value.strip()}
    tag = select_image_tag(pattern, result["tags"], wanted)
    repository, digest = reference.split("@", 1)
    tagged = f"{repository}:{tag}@{digest}"
    return replace_command_image(command["command"], reference, tagged)


def identity_values(row: dict) -> tuple[str, ...]:
    values = [str(row.get(column, "") or "").strip() for column in KEY_COLUMNS]
    # Normalize App/platform spelling; preserve repository branch and test-path case.
    values[0] = values[0].casefold()
    values[3] = denormalize_arch(values[3])
    values[4] = values[4].lower()
    values[5] = values[5].upper()
    return tuple(values)


def result_key(row: dict) -> str:
    values = identity_values(row)
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def worksheet_result_key(row: dict) -> str:
    data = canonical_result(row)
    values = ("qa-worksheet-v1", *identity_values(data),
              *(str(data.get(column, "") or "").strip()
                for column in ("hpc_version", "py_label", "torch_label")))
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def flat_commands(result: dict) -> list[dict]:
    rows = []
    for command in result["test_commands"]:
        for chip in command["chips"] or [""]:
            rows.append(
                {
                    "app_name": result["app_name"],
                    "git_branch": result["git_branch"],
                    "app_version": result["app_version"],
                    "arch": denormalize_arch(command["arch"]),
                    "OS": command["os"],
                    "GPU": chip,
                    "commit_id": result["commit_id"],
                    "test_name": command["test"],
                    "docker_cmd": command["command"],
                    "perf_golden": command["perf_golden"],
                    "perf_unit": command["perf_unit"],
                    "Python label": command.get("python_label", ""),
                    "PyTorch label": command.get("pytorch_label", ""),
                    **{column: "" for column in RESULT_COLUMNS},
                }
            )
    return rows


def result_reference(row: dict) -> str:
    """Accept full image@digest or resolve a bare digest using the Excel command."""
    row = canonical_result(row)
    value = str(row.get("qa_digest") or "").strip()
    if not value:
        return ""
    if "@" in value:
        parsed = parse_base_image_reference(value)
    else:
        digest = value if value.startswith("sha256:") else "sha256:" + value
        if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise ValueError("qa_digest 应为 64 位 digest、sha256:digest 或完整镜像@digest")
        command = str(row.get("qa_docker_cmd") or row.get("website_docker_cmd") or "")
        images = docker_images(command)
        if not images or "/" not in images[0]:
            raise ValueError("填写裸 digest 时，qa_docker_cmd 或 website_docker_cmd 中需要有完整 Harbor 镜像名")
        parsed = parse_base_image_reference(images[0].split("@")[0] + "@" + digest)
    address = parsed["image"].rsplit("/", 1)
    address[-1] = address[-1].partition(":")[0]
    return "/".join(address) + "@" + parsed["digest"]


# Parse image operands without executing QA shell text. Unknown options fail
# explicitly instead of guessing whether their next token is an image.
_RUN_VALUE_OPTIONS = set("""
--add-host --annotation -a --attach --blkio-weight --blkio-weight-device
--cap-add --cap-drop --cgroup-parent --cgroupns --cidfile --cpu-count --cpu-percent
--cpu-period --cpu-quota --cpu-rt-period --cpu-rt-runtime -c --cpu-shares --cpus
--cpuset-cpus --cpuset-mems --device --device-cgroup-rule --device-read-bps
--device-read-iops --device-write-bps --device-write-iops --dns --dns-option
--dns-search --domainname --entrypoint -e --env --env-file --expose --gpus
--group-add --health-cmd --health-interval --health-retries --health-start-interval
--health-start-period --health-timeout -h --hostname --init-path --ip --ip6 --ipc
--isolation --kernel-memory -l --label --label-file --link --link-local-ip
--log-driver --log-opt --mac-address -m --memory --memory-reservation --memory-swap
--memory-swappiness --mount --name --network --net --network-alias --oom-score-adj
--pid --pids-limit --platform -p --publish --pull --restart --runtime --security-opt
--shm-size --stop-signal --stop-timeout --storage-opt --sysctl --tmpfs --ulimit
-u --user --userns --uts -v --volume --volume-driver --volumes-from -w --workdir
--detach-keys
""".split())
_RUN_FLAGS = set("""
-d --detach --disable-content-trust --help --init -i --interactive --no-healthcheck
--oom-kill-disable --privileged -P --publish-all --read-only --rm --sig-proxy
-t --tty --use-api-socket --quiet -q
""".split())


def _without_shell_comments(command: str) -> str:
    # shlex normally consumes the newline along with a comment, which would
    # hide a second Docker command on the next line. Keep shell boundaries.
    chars, quote, word_start = [], "", True
    i = 0
    while i < len(command):
        char = command[i]
        if char == "\\" and quote != "'" and i + 1 < len(command):
            chars.extend(command[i:i + 2])
            i += 2
            word_start = False
            continue
        if not quote and char == "#" and word_start:
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        chars.append(char)
        if char == quote:
            quote = ""
        elif not quote and char in ("'", '"'):
            quote = char
        word_start = not quote and char in " \t\r\n;&|()"
        i += 1
    return "".join(chars)


def docker_images(command: str) -> list[str]:
    """Read run/create/pull operands; quoted container scripts are opaque tokens."""
    try:
        command = _without_shell_comments(command.replace("\\\n", ""))
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise ValueError("qa_docker_cmd 引号不完整") from exc
    images = []
    start = 0
    for end in range(len(tokens) + 1):
        if end < len(tokens) and (not tokens[end] or not set(tokens[end]) <= set(";&|\n")):
            continue
        segment = tokens[start:end]
        start = end + 1
        if segment[:1] == ["sudo"]:
            segment = segment[1:]
            if segment[:1] == ["-n"]:
                segment = segment[1:]
        if segment[:1] == ["env"]:
            segment = segment[1:]
        while segment and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", segment[0]):
            segment = segment[1:]
        if not segment or segment[0].rsplit("/", 1)[-1] != "docker":
            if any(re.search(r"\bdocker\s+(?:container\s+)?(?:run|create|pull)\b", token)
                   or token.rsplit("/", 1)[-1] == "docker" for token in segment):
                raise ValueError("qa_docker_cmd 无法确定镜像参数，请使用直接的 docker run/create/pull 命令")
            continue
        segment = segment[1:]
        if segment[:1] == ["container"]:
            segment = segment[1:]
        if not segment or segment[0] not in ("run", "create", "pull"):
            raise ValueError("qa_docker_cmd 仅支持校验 docker run/create/pull 的镜像参数")
        i = 1
        while i < len(segment) and segment[i].startswith("-"):
            option = segment[i]
            if option == "--":
                i += 1
                break
            name, equals, _ = option.partition("=")
            if name in _RUN_VALUE_OPTIONS:
                i += 1 if equals else 2
            elif name in _RUN_FLAGS or re.fullmatch(r"-[ditPq]+", option):
                i += 1
            elif len(option) > 2 and option[:2] in _RUN_VALUE_OPTIONS:
                i += 1  # e.g. -v/path:/path or -eNAME=value
            else:
                raise ValueError(f"qa_docker_cmd 无法识别 Docker 参数 {option}，请使用明确的镜像参数")
        if i >= len(segment):
            raise ValueError("qa_docker_cmd 缺少 Docker 镜像参数")
        images.append(segment[i])
    return images


def validate_command_images(row: dict, reference: str, tags: list[str]) -> None:
    """Require every explicit Docker tag/digest to identify this exact artifact."""
    expected_repository, expected_digest = reference.split("@", 1)
    for operand in docker_images(str(row.get("qa_docker_cmd") or "")):
        if operand in ("[docker_image]", "[docker_image_release]", "[docker_image_dbg]"):
            continue
        address = operand.split("@", 1)[0]
        path, _, leaf = address.rpartition("/")
        name, separator, tag = leaf.partition(":")
        repository = f"{path}/{name}" if path else name
        allowed_repositories = {expected_repository, expected_repository.split("/", 1)[1],
                                expected_repository.rsplit("/", 1)[-1]}
        if repository not in allowed_repositories:
            raise ValueError("qa_docker_cmd 的镜像仓库与 qa_digest 不匹配")
        # QA's downloaded template is intentionally unpinned. It may be filled
        # with a digest later; a concrete tag must instead be a current alias.
        if "<hpc_version>" in tag and "@" not in operand:
            parse_base_image_reference(operand.replace("<hpc_version>", "placeholder"))
            if not any(image_tag_matches(tag, actual) for actual in tags):
                raise ValueError("qa_docker_cmd 的镜像 tag 模板与 qa_digest 的 Harbor tags 不匹配")
            continue
        parsed = parse_base_image_reference(operand)
        if parsed["digest"] and parsed["digest"] != expected_digest:
            raise ValueError("qa_docker_cmd 的 digest 与 qa_digest 不一致")
        if separator and tag not in tags:
            raise ValueError("qa_docker_cmd 的 tag 不属于 qa_digest 对应镜像（Harbor 实际 tags 不匹配）")
        if not parsed["digest"] and not separator and "latest" not in tags:
            raise ValueError("qa_docker_cmd 的隐含 latest tag 不属于 qa_digest 对应镜像")
