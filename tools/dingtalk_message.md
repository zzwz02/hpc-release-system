# 钉钉机器人消息收发

独立 Python 3.11+ 工具，凭据仅从环境变量读取，不依赖发布系统服务或数据库。
支持机器人消息的 Stream 接收、固定文本自动回复、主动单聊/群聊文本发送。
不是个人账号客户端，不能读取任意聊天历史或接收机器人无权访问的消息。

## 准备

在钉钉开发者后台创建企业内部应用，启用机器人能力，消息接收模式选择 Stream，
发布应用版本并设置可用范围；群聊使用前将机器人加入目标群。
主动发送需开通对应的机器人单聊/群聊发送权限，且接收人应在应用可用范围内。

在仓库根目录运行：

```bash
python3 -m venv /tmp/dingtalk-message-venv
source /tmp/dingtalk-message-venv/bin/activate
pip install -r tools/dingtalk_message.requirements.txt

read -r -p 'AppKey: ' DINGTALK_CLIENT_ID
read -r -s -p 'AppSecret: ' DINGTALK_CLIENT_SECRET
echo
export DINGTALK_CLIENT_ID DINGTALK_CLIENT_SECRET
```

不需要把密钥发到聊天中，也不需要配置公网回调地址；运行机器需能访问钉钉 HTTPS / WebSocket 服务。

## 使用

```bash
# 仅接收：私聊机器人或在群里 @机器人
python tools/dingtalk_message.py receive

# 接收并自动回复固定文本
python tools/dingtalk_message.py receive --reply '收到消息了'

# 主动单聊：可重复 --user-id 指定多个接收人
python tools/dingtalk_message.py send-user --user-id '企业用户ID' --text '你好'

# 主动群聊
python tools/dingtalk_message.py send-group --conversation-id 'cid群会话ID' --text '大家好'
```

接收结果是 JSON 行，包含消息类型、发送人、会话 ID 和文本；非文本消息只显示元数据，
不下载图片或附件。`senderStaffId` 可用于单聊发送；群消息的 `conversationId` 可用于
`--conversation-id`。这些字段缺失时需通过企业通讯录/群会话接口取得，不能用手机号、
昵称或 `senderId` 替代企业 userId。`robotCode` 默认使用 AppKey，也可用 `--robot-code` 指定。

发送命令输出 API 返回结果；接口受理不等于用户已读。超时不自动重试，因为消息可能已经发出。
接收后即确认回调，自动回复失败会记录错误，不请求重投；本工具没有持久化去重，
断线重投等情况可能产生重复输出或回复。生产业务可在处理处按 `msgId` 持久化去重。
日志包含消息内容与用户 ID，请按需保管；不输出原始回调中的 sessionWebhook。

## 参考

- [官方 Stream Python SDK](https://github.com/open-dingtalk/dingtalk-stream-sdk-python)
- [获取应用 access_token](https://open.dingtalk.com/document/development/obtain-orgapp-token)
- [机器人单聊消息](https://open.dingtalk.com/document/orgapp/chatbots-send-one-on-one-chat-messages-in-batches)
- [机器人群聊消息](https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message)
