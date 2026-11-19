"""Read XWiki with a server-managed account and a renewable session."""
from __future__ import annotations

import json
import re
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from threading import Lock
from urllib.parse import quote, unquote, urlsplit

import httpx

from app import runtime_config

_SECTION = "xwiki"
_REST = "/rest/wikis/xwiki"
_VIEW = "/bin/view/"
_LOGIN = "/bin/login/XWiki/XWikiLogin"
_LOGIN_PARAMS = {"loginLink": "1", "xredirect": _REST}
_SUBMIT = "/bin/loginsubmit/XWiki/XWikiLogin"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_PAGE_CHARS = 24_000


class WikiError(RuntimeError):
    """A Wiki error safe to show in MCP."""


def _segment(value: str) -> str:
    if (not value or value in (".", "..") or re.search(r"[/\\;%\x00-\x1f\x7f]", value)
            or value != value.strip()):
        raise WikiError("Wiki 链接包含无效路径")
    return value


@dataclass(frozen=True)
class PageRef:
    spaces: tuple[str, ...]
    name: str = "WebHome"

    @property
    def rest_path(self) -> str:
        spaces = "".join(f"/spaces/{quote(part, safe='')}" for part in self.spaces)
        return f"{_REST}{spaces}/pages/{quote(self.name, safe='')}"

    @property
    def view_path(self) -> str:
        spaces = "/".join(quote(part, safe="") for part in self.spaces)
        name = "" if self.name == "WebHome" else quote(self.name, safe="")
        return f"{_VIEW}{spaces}/{name}"

    @classmethod
    def from_url(cls, url: str, base_url: str) -> PageRef:
        try:
            parts = urlsplit(url)
            origin = urlsplit(base_url)
            if parts.scheme != origin.scheme or parts.netloc.lower() != origin.netloc:
                raise ValueError
            if parts.path.startswith(_VIEW):
                segments = [_segment(unquote(part, errors="strict"))
                            for part in parts.path.removeprefix(_VIEW).removesuffix("/").split("/")]
                if parts.path.endswith("/") or len(segments) == 1:
                    return cls(tuple(segments))
                return cls(tuple(segments[:-1]), segments[-1])
            if parts.path.startswith(_REST + "/spaces/"):
                segments = parts.path.removeprefix(_REST + "/").split("/")
                if (len(segments) >= 4 and len(segments) % 2 == 0
                        and segments[-2] == "pages" and all(
                            part == "spaces" for part in segments[:-2:2]
                        )):
                    names = [_segment(unquote(part, errors="strict")) for part in segments[1::2]]
                    return cls(tuple(names[:-1]), names[-1])
        except ValueError:
            pass
        raise WikiError("请提供配置站点上的 XWiki 页面链接")


@dataclass(frozen=True)
class WikiConfig:
    base_url: str
    username: str
    password: str = field(repr=False)
    timeout: int

    @classmethod
    def read(cls) -> WikiConfig:
        values = runtime_config.section(_SECTION)
        base_url = runtime_config.required(_SECTION, values, "BASE_URL")
        try:
            parts = urlsplit(base_url)
            if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                    or parts.path not in ("", "/") or parts.query or parts.fragment):
                raise ValueError
            parts.port
        except ValueError:
            raise runtime_config.ConfigError("[xwiki] BASE_URL 必须是 HTTPS 站点根地址") from None
        timeout = runtime_config.required_int(_SECTION, values, "TIMEOUT_SECONDS")
        if not 1 <= timeout <= 60:
            raise runtime_config.ConfigError("[xwiki] TIMEOUT_SECONDS 必须在 1 至 60 秒之间")
        return cls(
            f"https://{parts.netloc.lower()}",
            runtime_config.required(_SECTION, values, "USERNAME"),
            runtime_config.required(_SECTION, values, "PASSWORD"),
            timeout,
        )


class _LoginForm(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self._actions = {_SUBMIT, base_url + _SUBMIT}
        self._inside = False
        self.token = ""

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "form":
            self._inside = fields.get("action") in self._actions
        if self._inside and tag == "input" and fields.get("name") == "form_token":
            self.token = fields.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._inside = False


def _denied(response: httpx.Response) -> bool:
    location = response.headers.get("location", "")
    return response.status_code in (401, 403) or (
        response.is_redirect and "/bin/login/" in location
    )


def _json_response(response: httpx.Response, *, missing: str) -> None:
    if _denied(response):
        raise WikiError("Wiki 公共账号无权读取此内容")
    if response.status_code == 404:
        raise WikiError(missing)
    if response.status_code != 200:
        raise WikiError(f"Wiki 请求失败（HTTP {response.status_code}）")
    if response.headers.get("content-type", "").partition(";")[0] != "application/json":
        raise WikiError("Wiki 返回了非 JSON 响应")


def _read_body(response: httpx.Response, *, limit: int = _MAX_RESPONSE_BYTES) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > limit:
            raise WikiError("Wiki 响应超过读取上限")
        body.extend(chunk)
    return bytes(body)


class WikiClient:
    def __init__(self, config: WikiConfig, *, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.http = httpx.Client(
            base_url=config.base_url, timeout=config.timeout, trust_env=False,
            follow_redirects=False, verify=ssl.create_default_context(), transport=transport,
        )
        self._retry_after = 0.0

    def close(self) -> None:
        self.http.close()

    def _session_active(self) -> bool:
        # XWiki's login page redirects an authenticated user to xredirect.
        # Inspect that redirect without following it or needing a protected page.
        with self.http.stream("GET", _LOGIN, params=_LOGIN_PARAMS,
                              headers={"Cache-Control": "no-cache"}) as response:
            if response.status_code in (200, 401):
                return False
            if response.is_redirect and response.headers.get("location") in (
                _REST, self.config.base_url + _REST,
            ):
                return True
            raise WikiError(f"无法检查 Wiki 登录会话（HTTP {response.status_code}）")

    def _login(self) -> None:
        if time.monotonic() < self._retry_after:
            raise WikiError("Wiki 公共账号登录暂不可用，请检查服务配置后重试")

        self.http.cookies.clear()
        try:
            with self.http.stream("GET", _LOGIN, params=_LOGIN_PARAMS) as response:
                if response.status_code not in (200, 401):
                    raise WikiError(f"无法读取 Wiki 登录表单（HTTP {response.status_code}）")
                form = _LoginForm(self.config.base_url)
                form.feed(_read_body(response, limit=256 * 1024).decode("utf-8", errors="replace"))
            if not form.token:
                raise WikiError("Wiki 登录表单缺少校验字段")
            with self.http.stream("POST", _SUBMIT, data={
                "j_username": self.config.username,
                "j_password": self.config.password,
                "form_token": form.token,
                "xredirect": _REST,
            }) as response:
                if response.status_code >= 400:
                    raise WikiError(f"Wiki 公共账号登录失败（HTTP {response.status_code}）")
            if not self._session_active():
                raise WikiError("Wiki 公共账号登录失败")
        except (WikiError, httpx.HTTPError):
            self.http.cookies.clear()
            self._retry_after = time.monotonic() + 30
            raise
        self._retry_after = 0.0

    def _get_json(self, path: str, *, params=None) -> dict:
        # The search API may return a successful empty list for an expired session.
        if not self.http.cookies or not self._session_active():
            self._login()
        with self.http.stream("GET", path, params=params, headers={"Accept": "application/json"}) as response:
            _json_response(response, missing="找不到 Wiki 页面或搜索接口")
            try:
                data = json.loads(_read_body(response))
            except ValueError:
                raise WikiError("Wiki 返回了无效的 JSON") from None
        if not isinstance(data, dict):
            raise WikiError("Wiki 返回了无效的数据结构")
        return data

    def search(self, keyword: str, offset: int, limit: int) -> dict:
        data = self._get_json(_REST + "/search", params=[
            ("q", keyword), ("scope", "name"), ("scope", "title"), ("scope", "content"),
            ("start", offset), ("number", limit),
        ])
        hits = data.get("searchResults")
        if not isinstance(hits, list):
            raise WikiError("Wiki 搜索结果格式无效")
        results = []
        for hit in hits[:limit]:
            page = self._search_ref(hit)
            if page:
                results.append({
                    "title": hit.get("title") or page.name, "space": list(page.spaces),
                    "url": self.config.base_url + page.view_path,
                })
        return {"results": results, "next_offset": offset + limit if len(hits) >= limit else None}

    def _search_ref(self, hit: object) -> PageRef | None:
        if not isinstance(hit, dict) or not isinstance(hit.get("links"), list):
            return None
        for link in hit["links"]:
            if not isinstance(link, dict) or link.get("rel") != "http://www.xwiki.org/rel/page":
                continue
            href = link.get("href")
            if not isinstance(href, str):
                continue
            try:
                # A TLS-terminating proxy can leave HTTP links in XWiki metadata.
                # Parse the page reference against the configured HTTPS origin.
                if href.lower().startswith("http://"):
                    href = "https://" + href.split("://", 1)[1]
                # A localized search hit links to the translation. Read the canonical page.
                href = re.sub(r"/translations/[^/?#]+$", "", href)
                return PageRef.from_url(href, self.config.base_url)
            except WikiError:
                continue
        return None

    def read_page(self, page: PageRef, offset: int) -> dict:
        data = self._get_json(page.rest_path)
        content = data.get("content")
        if not isinstance(content, str):
            raise WikiError("Wiki 返回的页面缺少正文")
        end = offset + _PAGE_CHARS
        return {
            "title": data.get("title") or page.name, "syntax": data.get("syntax"),
            "url": self.config.base_url + page.view_path, "content": content[offset:end],
            "next_offset": end if end < len(content) else None,
        }


_lock = Lock()
_client: WikiClient | None = None


@contextmanager
def use_wiki() -> Iterator[WikiClient]:
    global _client
    with _lock:
        config = WikiConfig.read()
        if _client is None or _client.config != config:
            if _client is not None:
                _client.close()
                _client = None
            _client = WikiClient(config)
        try:
            yield _client
        except httpx.HTTPError:
            raise WikiError("连接 Wiki 失败，请检查站点网络和证书后重试") from None
