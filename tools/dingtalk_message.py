#!/usr/bin/env python3
"""独立钉钉机器人收发工具；用法见 dingtalk_message.md。"""

import argparse
import asyncio
import json
import logging
import os
import sys

import requests

API = "https://api.dingtalk.com/v1.0"
LOG = logging.getLogger("dingtalk_message")


def post_json(url, payload, headers=None):
    """不自动重试发送，避免网络超时后重复投递。"""
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=(10, 30))
    except requests.RequestException:
        # requests 的异常可能携带含 token 的 webhook URL。
        raise RuntimeError("网络请求失败或超时；发送结果可能未知，请检查后再重试") from None
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(f"钉钉返回非 JSON 响应（HTTP {response.status_code}）") from None
    if not isinstance(result, dict):
        raise RuntimeError("钉钉返回了非预期的响应格式")
    if not response.ok or result.get("errcode", 0) not in (0, "0") or result.get("code"):
        code = result.get("code", result.get("errcode", "unknown"))
        raise RuntimeError(f"钉钉 API 失败：HTTP {response.status_code}, code={code}")
    return result


def send_text(client_id, client_secret, robot_code, text, *, user_ids=None, group_id=None):
    token = post_json(
        f"{API}/oauth2/accessToken", {"appKey": client_id, "appSecret": client_secret}
    ).get("accessToken")
    if not token:
        raise RuntimeError("获取 accessToken 失败：响应缺少 accessToken")
    payload = {
        "robotCode": robot_code,
        "msgKey": "sampleText",
        "msgParam": json.dumps({"content": text}, ensure_ascii=False),
    }
    if group_id:
        path = "robot/groupMessages/send"
        payload["openConversationId"] = group_id
    else:
        path = "robot/oToMessages/batchSend"
        payload["userIds"] = user_ids
    result = post_json(f"{API}/{path}", payload, {"x-acs-dingtalk-access-token": token})
    # 单聊可能部分失败：保留 invalidUserIdList / flowControlledStaffIdList 等字段。
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("invalidUserIdList") or result.get("flowControlledStaffIdList"):
        raise RuntimeError("部分接收人无效或被限流，请查看上方结果")


def receive(client_id, client_secret, reply):
    try:
        import dingtalk_stream
    except ImportError:
        raise RuntimeError(
            "请先安装依赖：pip install -r tools/dingtalk_message.requirements.txt"
        ) from None

    class Handler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback):
            data = callback.data
            # 仅输出业务字段，不输出 sessionWebhook 等凭据。
            event = {
                key: data.get(key)
                for key in (
                    "msgId",
                    "msgtype",
                    "senderNick",
                    "senderStaffId",
                    "conversationType",
                    "conversationId",
                    "robotCode",
                )
            }
            event["text"] = (data.get("text") or {}).get("content")
            print(json.dumps(event, ensure_ascii=False), flush=True)
            if reply is not None:
                webhook = data.get("sessionWebhook")
                if not webhook:
                    LOG.error("收到消息但缺少 sessionWebhook，无法回复")
                else:
                    try:
                        await asyncio.to_thread(
                            post_json, webhook, {"msgtype": "text", "text": {"content": reply}}
                        )
                    except RuntimeError as exc:
                        LOG.error("回复失败：%s", exc)
            # 接收完成即 ACK；回复失败也不请求重投，避免重复回复。
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    client = dingtalk_stream.DingTalkStreamClient(
        dingtalk_stream.Credential(client_id, client_secret)
    )
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, Handler())
    LOG.info("启动 Stream 接收；请向机器人发消息，群聊中需 @机器人。Ctrl+C 退出。")
    client.start_forever()


def main():
    parser = argparse.ArgumentParser(description="钉钉企业内部机器人：接收、回复、主动发送文本")
    commands = parser.add_subparsers(dest="command", required=True)
    listen = commands.add_parser("receive", help="通过 Stream 接收消息，输出 JSON 行")
    listen.add_argument("--reply", help="可选：收到消息后自动回复的固定文本")
    for command, help_text in (("send-user", "发送单聊消息"), ("send-group", "发送群消息")):
        send = commands.add_parser(command, help=help_text)
        send.add_argument("--text", required=True, help="发送的文本")
        send.add_argument("--robot-code", help="默认使用 DINGTALK_CLIENT_ID")
        if command == "send-user":
            send.add_argument(
                "--user-id", action="append", required=True, help="企业 userId，可重复"
            )
        else:
            send.add_argument("--conversation-id", required=True, help="群 openConversationId")
    args = parser.parse_args()
    credentials = [
        os.environ.get(key, "").strip() for key in ("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET")
    ]
    if not all(credentials):
        parser.error("请设置 DINGTALK_CLIENT_ID 和 DINGTALK_CLIENT_SECRET 环境变量")
    if args.command != "receive" and not args.text.strip():
        parser.error("--text 不能为空")
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    LOG.setLevel(logging.INFO)
    try:
        if args.command == "receive":
            receive(*credentials, args.reply)
        else:
            send_text(
                *credentials,
                args.robot_code or credentials[0],
                args.text,
                user_ids=getattr(args, "user_id", None),
                group_id=getattr(args, "conversation_id", None),
            )
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
