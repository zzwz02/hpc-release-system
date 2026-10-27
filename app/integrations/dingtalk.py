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


def configuration() -> dict[str, str] | None:
    values = runtime_config.section("dingtalk")
    # A blank token explicitly disables notifications in the shipped template.
    if not values or not values.get("ACCESS_TOKEN"):
        return None
    for key in ("ACCESS_TOKEN", "SECRET", "USERID"):
        runtime_config.required("dingtalk", values, key)
    return values


async def send_text(message: str) -> bool:
    config = configuration()
    if config is None:
        return False
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(config["SECRET"].encode(),
                      f'{timestamp}\n{config["SECRET"]}'.encode(), hashlib.sha256).digest()
    params = {"access_token": config["ACCESS_TOKEN"], "timestamp": timestamp,
              "sign": base64.b64encode(digest).decode()}
    users = [item.strip() for item in config["USERID"].split(",") if item.strip()]
    if not users:
        raise runtime_config.ConfigError("release_system.conf [dingtalk] USERID 不能为空")
    body = {"msgtype": "text", "text": {"content": message},
            "at": {"atUserIds": users, "isAtAll": False}}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("https://oapi.dingtalk.com/robot/send", params=params, json=body)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError):
        # HTTP exceptions contain the full URL, including access_token and sign.
        raise DingTalkError("钉钉请求失败（网络、HTTP 或响应格式错误）") from None
    if not isinstance(result, dict) or result.get("errcode") != 0:
        raise DingTalkError("钉钉拒绝消息，请检查机器人加签、群和用户配置")
    return True
