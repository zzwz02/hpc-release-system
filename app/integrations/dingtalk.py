"""Signed DingTalk custom-robot text messages; never expose webhook secrets."""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

from app import runtime_config


class _RedactWebhook(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx logs full request URLs at INFO, including query credentials.
        if "oapi.dingtalk.com/robot/send" in record.getMessage():
            record.msg = "DingTalk HTTP request (webhook URL redacted)"
            record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactWebhook())


class DingTalkError(RuntimeError):
    pass


DINGTALK_SEND_URL = "https://oapi.dingtalk.com/robot/send"


def _request(channel: dict, message: str) -> tuple[dict, dict]:
    token = str(channel.get("access_token") or "").strip()
    secret = str(channel.get("secret") or "").strip()
    if not token or not secret:
        raise DingTalkError("钉钉机器人缺少 ACCESS_TOKEN 或 SECRET")
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
    params = {"access_token": token, "timestamp": timestamp,
              "sign": base64.b64encode(digest).decode()}
    users = [str(user).strip() for user in channel.get("at_user_ids") or [] if str(user).strip()]
    body = {"msgtype": "text", "text": {"content": message},
            "at": {"atUserIds": users, "isAtAll": False}}
    return params, body


def _check(result: object) -> None:
    if not isinstance(result, dict) or result.get("errcode") != 0:
        raise DingTalkError("钉钉拒绝消息，请检查机器人加签、群和用户配置")


async def send_text(channel: dict, message: str) -> None:
    """Send *message* through the robot described by *channel*."""
    params, body = _request(channel, message)
    try:
        async with httpx.AsyncClient(
            timeout=15, proxy=runtime_config.proxy_url(), trust_env=False
        ) as client:
            response = await client.post(DINGTALK_SEND_URL, params=params, json=body)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        # HTTP exceptions contain the full URL, including access_token and sign.
        raise DingTalkError("钉钉请求失败（网络、HTTP 或响应格式错误）") from None
    _check(result)


def send_text_sync(channel: dict, message: str) -> None:
    """Blocking variant of send_text for worker threads."""
    params, body = _request(channel, message)
    try:
        with httpx.Client(timeout=15, proxy=runtime_config.proxy_url(), trust_env=False) as client:
            response = client.post(DINGTALK_SEND_URL, params=params, json=body)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        raise DingTalkError("钉钉请求失败（网络、HTTP 或响应格式错误）") from None
    _check(result)
