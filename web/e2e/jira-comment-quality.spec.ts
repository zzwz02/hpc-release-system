import { expect, test } from "@playwright/test";

const base = process.env.JIRA_COMMENT_E2E_URL || "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires the isolated local publication fixture");

for (const uncertain of [false, true]) {
  test(`review, save history and publish once with attachment (lost response: ${uncertain})`, async ({ page }) => {
    expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
    const initial = await (await page.request.post(`${base}/api/_testing/publication?accepted_timeout=${uncertain}`)).json();
    const issue = `MC3-${Date.now()}`;
    const start = await page.request.post(`${base}/api/jira-agent/conversations`, {
      data: { issue_key: issue, note: "请检查尾部元素", post_comment: true, request_id: issue },
    });
    expect(start.ok()).toBeTruthy();
    const cid = (await start.json()).conversation.id;
    await expect.poll(async () => {
      const detail = await (await page.request.get(`${base}/api/jira-agent/conversations/${cid}`)).json();
      return detail.turns.at(-1).delivery_status;
    }).toBe("complete");
    const before = await (await page.request.post(`${base}/api/_testing/publication?accepted_timeout=${uncertain}`)).json();
    expect(before.post_calls).toBe(initial.post_calls);
    await page.goto(`${base}/jira-agent?issue=${issue}&conversation=${cid}`);
    await expect(page.getByRole("checkbox", { name: /本轮结论发布到 JIRA/ })).toHaveCount(0);
    await expect(page.locator(".jira-agent-files")).toHaveCount(0);
    const artifacts = page.getByTestId("jira-agent-delivery");
    await expect(artifacts).not.toHaveAttribute("open");
    await expect(artifacts.getByRole("link", { name: "打包下载本轮产物（ZIP）" })).toBeVisible();
    await artifacts.getByText("本轮产物已全部回收").click();
    await artifacts.getByRole("button", { name: /fix.patch$/ }).click();
    await expect(page.getByRole("dialog").locator("pre")).toContainText("blocks = (n + 255) / 256;");
    await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click();
    await expect(page.getByTestId("jira-agent-result").getByText("修复补丁", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "发送到 JIRA", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "发送到 JIRA", exact: true });
    const editor = dialog.getByLabel("JIRA 评论内容");
    await expect(editor).toBeEnabled();
    await editor.fill("人工审核后的结论：修复已验证，请审核本轮附件。");
    await dialog.getByRole("checkbox", { name: "fix.patch" }).check();
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await page.getByRole("button", { name: "发送到 JIRA", exact: true }).click();
    await expect(editor).toHaveValue("人工审核后的结论：修复已验证，请审核本轮附件。");
    await expect(dialog.getByRole("checkbox", { name: "fix.patch" })).toBeChecked();
    await dialog.getByRole("button", { name: "查看编辑历史" }).click();
    await expect(dialog.getByText(/用户编辑 · rm/).first()).toBeVisible();
    await dialog.getByRole("button", { name: "确认发送并上传 1 个附件" }).click();
    if (uncertain) {
      await expect(dialog.getByRole("alert")).toContainText("发布结果待确认");
      await dialog.getByRole("button", { name: "核对并重试发送" }).click();
    }
    await expect(dialog.getByText(/本轮不能再次发送/)).toBeVisible();
    await expect(editor).toBeDisabled();
    await expect(dialog.getByRole("button", { name: /确认发送|重试发送/ })).toHaveCount(0);
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(page.getByTestId("jira-agent-comment").getByRole("link", { name: /^#/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "发送到 JIRA", exact: true })).toHaveCount(0);
    const after = await (await page.request.post(`${base}/api/_testing/publication`)).json();
    expect(after.post_calls).toBe(initial.post_calls + 1);
    expect(after.comments).toBe(initial.comments + 1);
  });
}
