"""离线验证独立钉钉工具，不发送真实消息。"""

import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch

import requests

from tools import dingtalk_message as bot


class DingTalkMessageTests(unittest.TestCase):
    def test_user_and_group_payloads(self):
        for target, field, value in (
            ({"user_ids": ["u1"]}, "userIds", ["u1"]),
            ({"group_id": "cid1"}, "openConversationId", "cid1"),
        ):
            with (
                self.subTest(target=target),
                patch.object(
                    bot,
                    "post_json",
                    side_effect=[{"accessToken": "token"}, {"processQueryKey": "q"}],
                ) as post,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                bot.send_text("app", "secret", "robot", '你好 "世界"', **target)
                args = post.call_args.args
                self.assertEqual(args[1][field], value)
                self.assertEqual(json.loads(args[1]["msgParam"]), {"content": '你好 "世界"'})
                self.assertEqual(args[2], {"x-acs-dingtalk-access-token": "token"})

    def test_partial_failure_is_not_success(self):
        with (
            patch.object(
                bot,
                "post_json",
                side_effect=[{"accessToken": "token"}, {"invalidUserIdList": ["bad"]}],
            ),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(RuntimeError),
        ):
            bot.send_text("app", "secret", "robot", "hello", user_ids=["bad"])

    def test_http_and_business_errors(self):
        for status, body in ((403, {"code": "Forbidden"}), (200, {"errcode": 1})):
            response = Mock(status_code=status, ok=status == 200)
            response.json.return_value = body
            with patch.object(bot.requests, "post", return_value=response):
                with self.assertRaises(RuntimeError):
                    bot.post_json("https://example.invalid", {})

    def test_network_error_does_not_expose_webhook(self):
        with patch.object(bot.requests, "post", side_effect=requests.Timeout("secret-url")):
            with self.assertRaises(RuntimeError) as raised:
                bot.post_json("https://example.invalid?token=secret", {})
        self.assertNotIn("secret", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
