# 附件下载与诊断

## 正常路径

1. 从当前工单获取附件 ID、`content/url`、名称、大小和 MIME。只选择影响当前问题理解的附件，不按文件名猜测 URL。
2. 使用已有 `JIRA_TOKEN_FILE` 和原始地址运行 `scripts/download_attachment.py`；保留 JSON 回执在任务私有目录。输出为 `<output-dir>/<issue-id>/<filename>`；同名文件按附件 ID 用 `--filename` 区分，不覆盖旧证据。
3. 核对 `success=true`、`data.status=downloaded` 和 `size_matches_metadata=true`。下载器只接受 HTTP 200，比较元数据大小、限制预算、拒绝明显的非预期 HTML；文件为 0600。SHA-256 标识实际取得的字节，不是服务端提供的完整性证明，也不代表代码正确或内容已解读。
4. 把成功回执的 `attachment_id/saved_path/sha256` 转成附件解读的 `files.json`，路径相对获准附件根目录。失败项不进入成功文件清单，独立保留原因和原附件引用。

不执行附件，不为下载增加密码登录、复制浏览器 Cookie 或修改服务器配置。真实 HTML 附件可以下载，但本下载器不验证其语义真实性；后续仍须解读。失败输出没有成功摘要，不能把残留文件当作有效附件。

## 失败分支

| 返回 | 可记录的事实 | 下一步 |
|---|---|---|
| `authentication_required` / HTTP 401 | 当前请求需要有效认证 | 核对现有凭据注入；必要时只读检查当前身份，不自动更换账号 |
| `access_denied` / HTTP 403 | 当前资源拒绝访问 | 核对该工单/附件访问范围，不借用他人权限 |
| `login_redirect` / 3xx | 当前附件请求跳转到同源登录页 | 不直接认定 token 失效；按下节做一次有界诊断 |
| `redirect_blocked` | 收到其他跳转，未跟随 | 核对当前元数据与允许 origin，不向目标发送凭据 |
| `attachment_unavailable` / HTTP 404 | 当前 URL 未提供附件 | 核对最新元数据；记录不可用，不断言已删除或存储损坏 |
| `unexpected_html` | 预期文件收到明显网页 | 不作为有效附件保存；核对响应和附件元数据 |
| `size_mismatch` / `transfer_incomplete` | 大小不符或传输未完成 | 不进入解读；根据当前元数据、连接和预算安排后续重试 |
| `network_error` / `http_error` / `local_io_error` | 连接、其他 HTTP 或本地保存异常 | 保留具体状态；不归因为登录限制，不覆盖已有文件 |

元数据读取失败与附件内容请求失败分开记录。API 可读不保证每个附件都可下载；某个附件不可下载也不证明所有附件均受登录限制。

### 登录跳转的有界诊断

仅在原地址返回同源 `/login.jsp` 跳转时，可再次运行同一下载命令并增加 `--diagnose-auth-redirect`。该选项先请求已核验的原始 URL，遇到上述跳转后，使用相同 Bearer token 对原 URL 加 `os_authType=basic` 最多探测一次；不跟随 Location、不请求登录表单、不改认证身份。`attempts` 保留两次 HTTP 状态。正常下载不增加额外请求。

该参数控制认证挑战响应，不授予额外权限，也不保证下载成功；参见 [Atlassian 参数说明](https://developer.atlassian.com/server/framework/atlassian-sdk/rest-and-os-authtype/)。若仍失败则保留缺口，不循环试 Cookie、用户名密码或改写附件路径。

## 返回内容理解

逐附件记录成功或失败；可用材料继续解读，不因一个文件失败放弃整单。缺失关键日志/截图时，说明限制了哪一项判断或复现条件，由信息检查组件汇总最小补充项。仅在该缺口需要人工处理时，经总调度交统一评论组件发布结论，不为每次下载尝试发评论。

维护下载器时运行 `python3 -B tests/test_download_offline.py`；该测试仅使用回环 HTTP 与临时文件，不需要真实凭据。不要因此运行同目录中会访问真实 Jira 的原始写操作测试。
