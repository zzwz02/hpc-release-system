"""Read-only Jenkins API client. Credentials stay on the configured controller."""
from __future__ import annotations

import base64
import json
import re
import ssl
from urllib.parse import unquote, urlsplit

import httpx

from app import runtime_config

JSON_LIMIT = 8 * 1024 * 1024
CONSOLE_CHUNK_BYTES = 64 * 1024


class JenkinsError(Exception):
    pass


class JenkinsClient:
    def __init__(self):
        section = runtime_config.section('cicd_agent')
        self.base = runtime_config.required('cicd_agent', section, 'JENKINS_BASE_URL').rstrip('/')
        self.username = runtime_config.required('cicd_agent', section, 'JENKINS_USERNAME')
        self.token = runtime_config.required('cicd_agent', section, 'JENKINS_API_TOKEN')
        self.timeout = runtime_config.required_int('cicd_agent', section, 'JENKINS_TIMEOUT_SECONDS')
        parsed = urlsplit(self.base)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or self.timeout <= 0:
            raise JenkinsError('Jenkins BASE_URL 或 TIMEOUT_SECONDS 配置无效')

    def build_url(self, value: str) -> str:
        parsed, base = urlsplit(value), urlsplit(self.base)
        prefix = base.path.rstrip('/') + '/'
        if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or parsed.query or parsed.fragment or not parsed.path.startswith(prefix):
            raise JenkinsError('构建 URL 必须属于配置的 Jenkins 地址，且不能包含查询参数或 fragment')
        path = parsed.path[len(prefix):].rstrip('/')
        if not re.fullmatch(r'(?:job/[^/]+/)+[1-9][0-9]*', path):
            raise JenkinsError('请提供包含数字构建号的 Jenkins build_url')
        decoded = path
        for _ in range(3):
            decoded = unquote(decoded)
            if any(part in ('.', '..') for part in decoded.split('/')) or '\\' in decoded or any(ord(c) < 32 for c in decoded):
                raise JenkinsError('构建 URL 路径无效')
        return self.base + '/' + path + '/'

    def _redact(self, text: str) -> str:
        encoded = base64.b64encode(f'{self.username}:{self.token}'.encode()).decode()
        return text.replace(encoded, '[REDACTED]').replace(self.token, '[REDACTED]')

    def get(self, url: str, *, params=None, limit=JSON_LIMIT, optional=False):
        try:
            # Never follow redirects: a redirect must not carry the bot credential elsewhere.
            with httpx.Client(auth=(self.username, self.token), timeout=self.timeout,
                              verify=ssl.create_default_context(),
                              follow_redirects=False, trust_env=False) as client:
                with client.stream('GET', url, params=params) as response:
                    if response.status_code == 404 and optional:
                        return None, {}, False
                    if response.status_code != 200:
                        raise JenkinsError(f'Jenkins HTTP {response.status_code}；请检查地址、API Token 及账号读取权限')
                    data = bytearray()
                    truncated = False
                    for chunk in response.iter_bytes(chunk_size=16384):
                        data.extend(chunk)
                        if len(data) > limit:
                            truncated = True
                            del data[limit:]
                            break
                    return bytes(data), dict(response.headers), truncated
        except httpx.HTTPError as exc:
            # Do not expose request objects, auth headers or remote response bodies.
            raise JenkinsError(f'Jenkins 请求失败：{type(exc).__name__}') from None

    def json(self, url: str, *, params=None, optional=False):
        data, _, truncated = self.get(url, params=params, optional=optional)
        if data is None:
            return None
        if truncated:
            raise JenkinsError('Jenkins JSON 超过读取上限，请缩小查询范围')
        try:
            result = json.loads(self._redact(data.decode('utf-8')))
        except (ValueError, UnicodeError):
            raise JenkinsError('Jenkins 未返回有效 JSON') from None
        if not isinstance(result, dict):
            raise JenkinsError('Jenkins JSON 格式无效')
        return result

    def console(self, build_url: str, start: int):
        data, headers, truncated = self.get(build_url + 'logText/progressiveText',
                                            params={'start': start}, limit=CONSOLE_CHUNK_BYTES)
        next_start = start + len(data)
        if not truncated:
            try:
                next_start = int(headers.get('x-text-size', next_start))
            except ValueError:
                pass
        return {'build_url': build_url, 'start': start, 'next_start': next_start,
                'truncated': truncated,
                'offset_note': '截断时续读可能包含少量重复行（Jenkins 日志注解占用额外字节）。' if truncated else '',
                'has_more': truncated,
                'building': headers.get('x-more-data', '').lower() == 'true',
                'content': self._redact(data.decode('utf-8', errors='replace'))}
