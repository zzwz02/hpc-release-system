# 详情读取覆盖

`get_issue_details.py` 优先取得明确工单的原字段，随后读取字段字典和全部可见评论。使用现有获准的 token 和 origin；不增加登录方式、不跟随重定向。

- 工单本身未取得或身份不符：`success=false`，不伪造空快照。
- 已取得工单：`success=true`，`completeness=complete|partial`。退出 0 只表示可用材料已返回，不代表无缺项。
- `data.acquisition` 保存 started_at、finished_at、scope 和 parts，分别说明 issue、field_definitions、comments、attachment_metadata。
- 字段字典不可读时保留原始 customfield ID/value，不猜名称。评论分页失败保留已成功分页；尚未取得任何完整页时，可采用详情内已知的嵌入页并标 `fallback=issue_embedded_page`，仍为 partial。
- 分页回执包含已成功页的 startAt/count/total、collected、reported_total。总数变化、重复/缺少 ID、位置错误或缺失 total 都不声明完整。
- 401、403、404、网络/超时和其他 HTTP 错误保留区分；不输出异常正文、响应内容或凭据。

complete 仅表示本次各请求在 API 可见范围内按返回结构完成，不证明受权限隐藏的评论不存在，不包含完整编辑历史，也不是跨请求原子快照。空附件字段与未返回附件字段区分；附件内容仍需另行下载与解读。

旧的 `get_comments.py` 继续要求完整分页，遇到未完成采集会失败；只有详情采集显式启用部分返回。业务处理必须同时看读取覆盖与当前动作需求，不能单看 success 决定复现、修复或关闭工单。
