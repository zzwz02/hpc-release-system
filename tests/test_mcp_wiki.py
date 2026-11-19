"""Verify Wiki login, renewal, and access through the normal MCP endpoint."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import settings
from app.integrations import xwiki
from tests.test_mcp import call, connect_client, rpc, site  # noqa: F401
from tests.test_mcp_jenkins_access import listed, membership, set_identity

BASE = "https://wiki.example"
HTTP_BASE = "http://wiki.example"
SPACE = ("3. Organizations", "PDE", "C500")
HOME = xwiki.PageRef(SPACE)
GUIDE = xwiki.PageRef((*SPACE, "指南"), "Getting Started")
OTHER = xwiki.PageRef(("Other", "Docs"))
PRIVATE = xwiki.PageRef((*SPACE[:-1], "C500-private"))


class Wiki:
    """An expired XWiki session denies reads and returns empty search results."""

    def __init__(self):
        self.logins = 0
        self.expired = True
        self.allow_login = True
        self.login_redirect = None
        self.requests = []
        self.body = "测试文档" * 6100

    def request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path))
        assert str(request.url.copy_with(path="/", query=None)) == BASE + "/"
        path = request.url.raw_path.split(b"?")[0].decode()
        cookie = request.headers.get("cookie", "")
        logged_in = not self.expired and cookie == f"JSESSIONID=wiki-session-{self.logins}"
        if path == xwiki._LOGIN:
            assert dict(request.url.params) == {"loginLink": "1", "xredirect": "/rest/wikis/xwiki"}
            if self.login_redirect:
                return httpx.Response(302, headers={"location": self.login_redirect})
            if logged_in:
                redirect = "/rest/wikis/xwiki"
                return httpx.Response(302, headers={"location": BASE + redirect if self.logins == 1 else redirect})
            return httpx.Response(
                200, headers={"Set-Cookie": "JSESSIONID=anonymous; Path=/; Secure"},
                html='<form action="/bin/loginsubmit/XWiki/XWikiLogin" method="post">'
                     '<input name="form_token" value="form-key"></form>',
            )
        if path == xwiki._SUBMIT:
            assert request.method == "POST" and cookie == "JSESSIONID=anonymous"
            assert parse_qs(request.content.decode()) == {
                "j_username": ["wiki-bot"], "j_password": ["ephemeral-password"],
                "form_token": ["form-key"], "xredirect": ["/rest/wikis/xwiki"],
            }
            self.logins += 1
            if not self.allow_login:
                return httpx.Response(302, headers={"location": xwiki._LOGIN})
            self.expired = False
            return httpx.Response(302, headers={
                "Set-Cookie": f"JSESSIONID=wiki-session-{self.logins}; Path=/; Secure; HttpOnly",
                "location": "/rest/wikis/xwiki",
            })
        if path in (GUIDE.rest_path, OTHER.rest_path):
            assert logged_in
            return httpx.Response(200, json={"title": "快速入门", "content": self.body, "syntax": "xwiki/2.1"})
        if path == PRIVATE.rest_path:
            assert logged_in
            return httpx.Response(403)
        assert path == "/rest/wikis/xwiki/search"
        assert request.url.params.get_list("scope") == ["name", "title", "content"]
        assert request.url.params["q"] == "快速入门"
        hits = [
            {"title": "快速入门", "links": [
                {"rel": "http://www.xwiki.org/rel/page", "href": HTTP_BASE + GUIDE.rest_path + "/translations/zh"},
            ]},
            {"title": "其他空间", "links": [
                {"rel": "http://www.xwiki.org/rel/page", "href": BASE + OTHER.rest_path},
            ]},
            {"title": "其他站点", "links": [
                {"rel": "http://www.xwiki.org/rel/page", "href": "http://other.example" + GUIDE.rest_path},
            ]},
        ]
        return httpx.Response(200, json={"searchResults": hits if logged_in else []})


@pytest.fixture()
def wiki(tmp_path, monkeypatch):
    upstream = Wiki()
    conf = tmp_path / "wiki.conf"
    conf.write_text(
        "[xwiki]\n"
        f"BASE_URL = {BASE}/\n"
        "USERNAME = wiki-bot\n"
        "PASSWORD = ephemeral-password\n"
        "TIMEOUT_SECONDS = 10\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_conf_path", conf)
    original = xwiki.WikiClient
    monkeypatch.setattr(xwiki, "WikiClient", lambda config: original(
        config, transport=httpx.MockTransport(upstream.request),
    ))
    monkeypatch.setattr(xwiki, "_client", None)
    yield upstream
    if xwiki._client is not None:
        xwiki._client.close()


def test_mcp_wiki_renews_shared_session_and_reads_across_spaces(site, wiki):
    client, _, token_for, _ = site
    owner_token = token_for("alice", "Owner")
    session, _ = connect_client(client, owner_token)
    search = call(client, owner_token, session, "wiki_search", {"keyword": "快速入门", "limit": 3})
    search_data = json.loads(search["content"][0]["text"])
    assert search_data == {
        "results": [
            {"title": "快速入门", "space": list(GUIDE.spaces), "url": BASE + GUIDE.view_path},
            {"title": "其他空间", "space": list(OTHER.spaces), "url": BASE + OTHER.view_path},
        ],
        "next_offset": 3,
    }
    rm_token = token_for("rm", "RM")
    rm_session, _ = connect_client(client, rm_token)
    read = call(client, rm_token, rm_session, "wiki_read_page", {
        "url": search_data["results"][0]["url"] + "?xpage=plain#H测试",
    })
    data = json.loads(read["content"][0]["text"])
    assert data["content"] == wiki.body[:24_000]
    assert data["next_offset"] == 24_000 and data["url"] == BASE + GUIDE.view_path
    other = call(client, rm_token, rm_session, "wiki_read_page", {"url": BASE + OTHER.view_path})
    assert json.loads(other["content"][0]["text"])["url"] == BASE + OTHER.view_path
    assert wiki.logins == 1

    wiki.expired = True
    search = call(client, owner_token, session, "wiki_search", {"keyword": "快速入门"})
    assert len(json.loads(search["content"][0]["text"])["results"]) == 2
    assert wiki.logins == 2
    assert "wiki-session" not in json.dumps(search) and "ephemeral-password" not in json.dumps(read)


def test_mcp_wiki_denies_guest_invalid_links_and_pages_denied_by_wiki(site, wiki):
    client, _, token_for, _ = site
    guest = token_for("guest", "Guest")
    guest_session, _ = connect_client(client, guest)
    result = rpc(client, guest, "tools/list", session=guest_session).json()["result"]
    assert not any(tool["name"].startswith("wiki_") for tool in result["tools"])
    denied = rpc(client, guest, "tools/call", {
        "name": "wiki_read_page", "arguments": {"url": BASE + GUIDE.view_path},
    }, session=guest_session)
    assert "error" in denied.json()

    token = token_for("alice", "Owner")
    session, _ = connect_client(client, token)
    names = {tool["name"] for tool in rpc(client, token, "tools/list", session=session).json()["result"]["tools"]}
    assert {"wiki_search", "wiki_read_page"} <= names
    for url in ("https://other.example" + HOME.view_path, HTTP_BASE + GUIDE.view_path,
                BASE + HOME.view_path + "%2e%2e/Page", BASE + HOME.rest_path + "/history"):
        response = call(client, token, session, "wiki_read_page", {"url": url})
        assert response["isError"]
    assert wiki.requests == []
    response = call(client, token, session, "wiki_read_page", {"url": BASE + PRIVATE.view_path})
    assert response["isError"] and "无权读取" in response["content"][0]["text"]
    assert wiki.logins == 1


@pytest.mark.parametrize("group", ["CN=dl.pde_hpc,OU=Groups,DC=example", "DL.SW_Tools"])
def test_wiki_group_grant_applies_to_listing_and_calls_on_existing_token(site, wiki, monkeypatch, group):
    client, _, token_for, _ = site
    set_identity("guest", "Guest")
    groups = [group]
    membership(monkeypatch, groups)
    token = token_for("guest", "Guest")
    session, _ = connect_client(client, token)

    tools = {"wiki_search": {"keyword": "快速入门"}, "wiki_read_page": {"url": BASE + GUIDE.view_path}}
    assert tools.keys() <= listed(client, token, session)
    assert tools.keys() <= {tool["name"] for tool in client.get("/api/mcp/info").json()["tools"]}
    for name, arguments in tools.items():
        assert not call(client, token, session, name, arguments)["isError"]

    groups.clear()
    request_count = len(wiki.requests)
    assert not (tools.keys() & listed(client, token, session))
    for name, arguments in tools.items():
        denied = rpc(client, token, "tools/call", {"name": name, "arguments": arguments}, session=session)
        assert denied.json()["error"]["code"] == -32602
    assert len(wiki.requests) == request_count


def test_wiki_login_failed_has_cooldown_and_keeps_credentials_private(wiki):
    wiki.allow_login = False
    with xwiki.use_wiki() as client:
        with pytest.raises(xwiki.WikiError, match="登录失败") as error:
            client.search("快速入门", 0, 10)
        assert "ephemeral-password" not in str(error.value)
        with pytest.raises(xwiki.WikiError, match="暂不可用"):
            client.search("快速入门", 0, 10)
    assert wiki.logins == 1
    assert all(not path.endswith("/search") for _, path in wiki.requests)


def test_wiki_session_check_rejects_unexpected_redirect(wiki):
    with xwiki.use_wiki() as client:
        client.search("快速入门", 0, 10)
        wiki.requests.clear()
        wiki.login_redirect = "https://other.example/rest/wikis/xwiki"
        with pytest.raises(xwiki.WikiError, match="无法检查 Wiki 登录会话"):
            client.search("快速入门", 0, 10)
    assert wiki.requests == [("GET", xwiki._LOGIN)]
    assert wiki.logins == 1
