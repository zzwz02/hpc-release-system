"""Public archive reads must be exact and pinned; never substitute a branch head."""
import ssl

import httpx
import pytest

from app import runtime_config
from app.api.errors import ApiError
from app.config import settings
from app.integrations import gerrit, manifest_archive

PATH = "hpc_manifest_master/APP/demo/release/3.10.0.7_123_default.xml"
COMMIT = "ab" * 20
XML = f'<manifest><project name="hpc_demo" revision="{COMMIT}"><linkfile src="app_info.json" dest="app_info.json"/></project></manifest>'


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    config = tmp_path / "release_system.conf"
    config.write_text("[hpc_manifest_archive]\nBASE_URL = https://archive.test/pde-hpc\n")
    monkeypatch.setattr(settings, "runtime_conf_path", config)
    real_client = httpx.Client

    def response(body=XML, status=200):
        def handler(request):
            assert request.method == "GET"
            assert request.url == "https://archive.test/pde-hpc/" + PATH
            assert "authorization" not in request.headers
            return httpx.Response(status, text=body)

        def client(**kwargs):
            assert isinstance(kwargs["verify"], ssl.SSLContext)
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", client)

    return config, response


def read():
    return manifest_archive.app_info_commit(PATH, gerrit.gerrit_remote_url("hpc_demo"))


def test_public_manifest_resolves_only_full_commit_in_declared_project(archive):
    config, response = archive
    response()
    result = read()
    assert result == {"git_url": gerrit.gerrit_remote_url("hpc_demo"), "commit_id": COMMIT,
                      "manifest_url": "https://archive.test/pde-hpc/" + PATH}
    # Configuration is read on each use.
    config.write_text("[hpc_manifest_archive]\n")
    with pytest.raises(runtime_config.ConfigError, match="BASE_URL"):
        read()


@pytest.mark.parametrize("body,error", [
    (XML.replace(COMMIT, "master"), "未固定"),
    (XML.replace(COMMIT, COMMIT[:8]), "未固定"),
    (XML.replace("hpc_demo", "other_project"), "不匹配"),
    (XML.replace("app_info.json", "other.json"), "无法唯一"),
    (XML.replace("</manifest>", XML.removeprefix("<manifest>")), "无法唯一"),
    ("not xml", "无效"),
])
def test_invalid_unpinned_or_ambiguous_archive_cannot_generate_commands(archive, body, error):
    archive[1](body)
    with pytest.raises(ValueError, match=error):
        read()


@pytest.mark.parametrize("status,error", [(404, manifest_archive.ArchiveNotFound), (403, ApiError), (503, ApiError), (302, ApiError)])
def test_archive_errors_are_explicit(archive, status, error):
    archive[1](status=status)
    with pytest.raises(error):
        read()


def test_archive_path_cannot_escape_configured_public_base(archive):
    archive[1]()
    for path in ["../other.xml", "/other.xml", "https://other.test/x.xml", "a//b.xml"]:
        with pytest.raises(ValueError, match="路径无效"):
            manifest_archive.app_info_commit(path, "ignored")
