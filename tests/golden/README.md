# API Golden 回放

这里保存代表性 API 响应的期望结果，检查响应形状、权限和业务行为。它起源于旧系统捕获，但部分 fixture 已按当前 FastAPI 规则更新；**当前 golden 不等于全部旧行为必须保持不变**。

## 文件与入口

- `responses/*.json`：被 Git 跟踪的期望响应。
- `scrub.py`：时间戳、token 等非确定字段归一化。
- `test_golden_replay.py`：fixture 检查、非 HTTP 回放与标记为 `phase2` 的真实 FastAPI HTTP 回放。
- `capture.py`：历史捕获工具，启动旧 `server.py`，不代表当前 FastAPI 的完整契约。
- `tests/conftest.py`：临时测试库、服务、身份和动态 ID 映射。

在仓库根运行：

```bash
python -m pytest tests/golden/test_golden_replay.py -q
python -m pytest tests/golden/test_golden_replay.py -m phase2 -q
```

先按 [隔离验证说明](../../.agents/skills/release-system-dev/references/verification.md) 设置主库、助手库和 Admin 文件路径。HTTP 回放需要创建本机 socket 并启动临时 uvicorn；权限或网络被阻止时应报告环境限制，不能声称该部分通过。

## Fixture 内容

以实际 JSON 为准，常见字段包括 `_golden_name`、`_note`、请求相关元信息、`status` 和 `body`。Body 可能是 JSON，也可能是文本响应。

Scrubber 移除非确定的时间/token 等；服务生成的动态 ID还由测试映射。`origin`、`app_id`、发布决策、申请状态和权限结果属于业务字段，不应为消除失败而加入 scrub 列表。

## 变更流程

行为未改变时，保留期望值并修复实现中的回归。用户有意改变行为时：

1. 对照当前 router/service/domain 确定新规则与请求/响应形状。
2. 在隔离实例验证实际 HTTP 提交与回读，包括权限拒绝和错误状态。
3. 只更新受影响 fixture，并解释字段/状态为何变化；补充新边界用例。
4. 运行相关回放，检查是否误伤其他角色或生命周期状态。

不要删除或跳过 fixture 来隐藏回归，也不要把真实业务数据、账号信息、会话或内部配置复制进 fixture。静态结构测试通过不等于实际 HTTP 回放通过。

## 历史捕获工具的边界

`python tests/golden/capture.py` 会创建临时库并启动旧服务器，然后写入响应文件。只有明确要检查旧实现时才使用；先检查脚本输出位置，并在隔离工作区运行，避免批量覆盖已经更新的当前期望。

新增 FastAPI 接口不要求先在旧实现增加端点。当前运行层以 App ID 承载 CICD，旧表/旧状态只能作为兼容测试背景，不能借由重新捕获把已退休行为带回来。
