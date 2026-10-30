"""Authorized artifact previews and disk-backed ZIP downloads."""
from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED

from app.api.errors import ApiError
from app.repositories import jira_agent_repo as repo
from app.services import jira_agent_service as service
from app.services.jira_agent_runner import open_db

PREVIEW_BYTES = 1024 * 1024
CODE_LANGUAGES = {
    "c": "c", "h": "c", "cc": "cpp", "c++": "cpp", "cpp": "cpp", "cxx": "cpp",
    "hpp": "cpp", "hh": "cpp", "cu": "cpp", "cuh": "cpp", "py": "python",
    "js": "javascript", "jsx": "javascript", "ts": "typescript", "tsx": "typescript",
    "sh": "bash", "bash": "bash", "json": "json", "yaml": "yaml", "yml": "yaml",
    "patch": "diff", "diff": "diff", "cmake": "cmake", "rs": "rust", "go": "go",
}


def preview(user, conversation_id, file_id):
    path, name = service.file_for_download(user, conversation_id, file_id)
    with path.open("rb") as stream:
        raw = stream.read(PREVIEW_BYTES + 1)
    truncated = len(raw) > PREVIEW_BYTES
    raw = raw[:PREVIEW_BYTES]
    result = {"name": name, "kind": "binary", "language": "", "text": "", "truncated": truncated}
    if b"\x00" in raw or any(b < 9 or 13 < b < 32 for b in raw):
        return result
    try:
        # Ignore only an incomplete final UTF-8 sequence at the preview boundary.
        import codecs
        text = codecs.getincrementaldecoder("utf-8")("strict").decode(raw, final=not truncated)
    except UnicodeError:
        return result
    suffix = name.rsplit(".", 1)[-1].lower()
    language = CODE_LANGUAGES.get(suffix, "")
    return {**result, "kind": "markdown" if suffix in {"md", "markdown"} else "code" if language else "text",
            "text": text, "language": language}


def artifact_zip(user, turn_id):
    conn = open_db()
    archive = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        turn = repo.get_turn(conn, turn_id)
        if not turn:
            raise ApiError(404, "轮次不存在")
        conversation = service._get_visible(conn, user, turn["conversation_id"])
        files = [f for f in repo.list_files(conn, conversation["id"]) if f["turn_id"] == turn_id and f["source"] == "artifact"]
        if not files:
            raise ApiError(404, "本轮没有产物")
        if turn["delivery_status"] not in ("", "complete"):
            raise ApiError(409, "产物尚未收齐，请先重试回收")
        names = set()
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
            for file in files:
                path, name = service.file_for_download(user, conversation["id"], file["id"])
                relative = PurePosixPath(file["remote_path"] or name)
                if relative.is_absolute() or ".." in relative.parts:
                    relative = PurePosixPath(name)
                arcname = str(relative)
                if arcname in names:
                    arcname = file["id"] + "/" + arcname
                names.add(arcname)
                zip_file.write(path, arcname)
        archive.seek(0)
        return archive, f"{conversation['issue_key']}-round-{turn['seq']}-artifacts.zip"
    except BaseException:
        archive.close()
        raise
    finally:
        conn.close()
