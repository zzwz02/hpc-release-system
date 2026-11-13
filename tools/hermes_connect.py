#!/usr/bin/env python3
"""Hermes 连接检查：验证 HTTPS 接口和 WebSocket 心跳。

使用方法（Python 3.11+，在仓库根目录执行）：
    pip3 install 'websockets==16.0'
    export AF_API_KEY='你的 API 访问密钥'
    python3 tools/hermes_connect.py

密钥优先读取 AF_API_KEY，为空时读取 /tmp/hermes-af-api-key。
输出 HTTPS 200、WebSocket 101 且 gateway.ping 返回 ok=true 表示连接成功。
"""

import asyncio
import json
import os
from pathlib import Path
import ssl
import sys
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from websockets.asyncio.client import connect


# 当前已验证的 Hermes 实例；更换目标时修改此处，HTTPS 和 WS 共用该地址。
BASE_URL = "https://hermes-7e9dcdb5.agent.kai.metax-tech.com"
# 用于不同终端之间临时传递密钥；文件仅保留在本机，权限应为 0600。
KEY_FILE = Path("/tmp/hermes-af-api-key")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


async def check_websocket(key, tls):
    url = BASE_URL.replace("https://", "wss://", 1) + "/api/ws"
    async with connect(
        url,
        additional_headers={"Authorization": "Bearer " + key},
        ssl=tls,
        open_timeout=15,
        close_timeout=3,
    ) as ws:
        print("WebSocket:", ws.response.status_code, url)
        await ws.send(json.dumps({
            "jsonrpc": "2.0",
            "id": "connection-check",
            "method": "gateway.ping",
            "params": {},
        }) + "\n")
        async with asyncio.timeout(15):
            async for frame in ws:
                for line in frame.splitlines():
                    if not line.strip():
                        continue
                    message = json.loads(line)
                    if message.get("method") == "event":
                        event = message.get("params", {}).get("type")
                        if event == "gateway.ready":
                            print("Event:", event)
                    if message.get("id") == "connection-check":
                        print("gateway.ping:", json.dumps(message, ensure_ascii=False))
                        if message.get("result", {}).get("ok") is not True:
                            raise RuntimeError("gateway.ping did not succeed")
                        return
        raise RuntimeError("WebSocket closed before gateway.ping replied")


def main():
    key = os.environ.get("AF_API_KEY", "").strip()
    if not key and KEY_FILE.is_file():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise SystemExit("Set AF_API_KEY in this terminal before running this script.")

    ca_file = os.environ.get("SSL_CERT_FILE")
    system_ca = Path("/etc/ssl/certs/ca-certificates.crt")
    if not ca_file and system_ca.is_file():
        ca_file = str(system_ca)
    tls = ssl.create_default_context(cafile=ca_file)

    request = Request(BASE_URL + "/api/health", headers={"Authorization": "Bearer " + key})
    opener = build_opener(NoRedirects(), HTTPSHandler(context=tls))
    with opener.open(request, timeout=20) as response:
        health = json.load(response)
        print("HTTPS:", response.status, "ok=" + str(health.get("ok")))
    asyncio.run(check_websocket(key, tls))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Authentication headers and bootstrap credentials are never printed.
        print("Connection failed:", type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
