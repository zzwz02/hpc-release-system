/** Uses the real API and isolated source peers, never production Jira or Codex. */
import { test, expect, type Page } from "@playwright/test";
const base = process.env.JIRA_TESTING_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated testing site");
const api = `${base}/api/jira-agent/testing`;
const comments = [
  { id: "c1", author: "qa", created: "2026-10-28 10:20", body: "执行命令：./saxpy 16777217\n运行结果：末尾一个元素计算错误，预期输出 PASS。\n环境：C500，使用工单中的测试镜像。\n已知结论：尾部元素的处理范围不正确。" },
  { id: "c2", author: "owner", created: "2026-10-28 14:30", body: "已完成定位，修复补丁见附件。" },
  { id: "c3", author: "qa", created: "2026-10-28 15:10", body: "补充运行数据和执行脚本，见第二轮上传文件。" },
];
async function create(page: Page, key: string, issue: Record<string, unknown>, uploads = false) {
  expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  await page.request.post(`${base}/api/_testing/plan`);
  await page.request.post(`${base}/api/_testing/import-materials`, { data: { key, issue } });
  const response = await page.request.post(`${base}/api/jira-agent/conversations`, { data: { issue_key: key, post_comment: false, new_conversation: true,
    note: "复现计算错误并定位原因", request_id: crypto.randomUUID(),
    files: uploads ? [{ filename: "运行说明.txt", content_base64: btoa("Original instructions") }] : [] } });
  expect(response.ok()).toBeTruthy();
  const conversation = (await response.json()).conversation;
  const url = `${base}/api/jira-agent/conversations/${conversation.id}`;
  await expect.poll(async () => (await (await page.request.get(url)).json()).conversation.state, { timeout: 20000 }).toBe("waiting_review");
  if (uploads) {
    expect((await page.request.post(url + "/messages", { data: { text: "补充运行文件", post_comment: false,
      files: [{ filename: "run.sh", content_base64: btoa("./saxpy 16777217") }, { filename: "修复说明.txt", content_base64: btoa("SECRET_FILE_ANSWER") }] } })).ok()).toBeTruthy();
    await expect.poll(async () => (await (await page.request.get(url)).json()).conversation.state, { timeout: 20000 }).toBe("waiting_review");
  }
  return conversation.id as string;
}

test("Jira import selects and edits comments before selecting files, preserving choices on back", async ({ page }) => {
  test.setTimeout(60000);
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const cid = await create(page, "MC3-9001", { summary: "GPU 计算结果异常", comments }, true);
  await page.goto(`${base}/jira-agent?view=testing&import=${cid}`);
  const comment = page.getByRole("dialog", { name: "选择评论", exact: true });
  const file = page.getByRole("dialog", { name: "选择附件", exact: true });
  await expect(comment).toBeVisible();
  await expect(comment.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await comment.getByLabel("带入评论 1", { exact: true }).check();
  const text = "执行命令：./saxpy 16777217\n运行结果：末尾一个元素计算错误，预期输出 PASS。\n环境：C500，使用工单中的测试镜像。";
  await comment.getByLabel("评论 1 的带入内容").fill(text);
  await page.screenshot({ path: "/tmp/jira-import-comments.png" });
  await comment.getByRole("button", { name: "下一步：选择附件" }).click();
  await expect(comment).not.toBeVisible(); await expect(file).toBeVisible();
  await expect(file.getByRole("checkbox")).toHaveCount(4);
  await expect(file.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await expect(file).toContainText("人工上传 · 第 2 轮");
  const downloading = page.waitForEvent("download");
  await file.getByRole("button", { name: "下载 run.sh", exact: true }).click();
  expect((await downloading).suggestedFilename()).toBe("run.sh");
  await expect(file.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await file.getByLabel("带入文件 run.sh", { exact: true }).check();
  await file.getByLabel("带入文件 saxpy.cu", { exact: true }).check();
  await file.getByRole("button", { name: "上一步" }).click();
  await expect(comment.getByLabel("评论 1 的带入内容")).toHaveValue(text);
  await comment.getByLabel("带入评论 1", { exact: true }).uncheck();
  await comment.getByLabel("带入评论 1", { exact: true }).check();
  await expect(comment.getByLabel("评论 1 的带入内容")).toHaveValue(text);
  await comment.getByRole("button", { name: "下一步：选择附件" }).click();
  await expect(file.getByRole("checkbox", { checked: true })).toHaveCount(2);
  await page.screenshot({ path: "/tmp/jira-import-files.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(file).toBeInViewport();
  expect(await file.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  await expect(file.getByRole("button", { name: "确认附件" })).toBeInViewport();
  await page.screenshot({ path: "/tmp/jira-import-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const prepared = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(`/import/${cid}`));
  await file.getByRole("button", { name: "确认附件" }).click();
  const draft = await (await prepared).json();
  expect(draft.input.issue.comments).toHaveLength(1);
  expect(draft.input.issue.comments[0].body).toBe(text);
  expect(JSON.stringify(draft)).not.toContain("SECRET_");
  expect(JSON.stringify(draft)).not.toContain("已知结论");
  expect(JSON.stringify(draft)).not.toContain("已完成定位");
  expect(draft.reference).toBeUndefined();
  await expect(file).not.toBeVisible();
  await expect(page.getByRole("heading", { name: "保存测试输入" })).toBeVisible();
  expect(await page.getByLabel("工单快照", { exact: true }).inputValue()).not.toContain("SECRET_");
  await page.getByLabel("用例名称", { exact: true }).fill("筛选后的计算错误用例");
  await page.getByRole("checkbox", { name: /已核对输入完整/ }).check();
  const saved = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/cases"));
  await page.getByRole("button", { name: "保存用例", exact: true }).click();
  const value = await (await saved).json();
  expect(value.versions[0].input).toEqual(draft.input);
  await expect(page.getByRole("button", { name: "修改用例", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "修改用例", exact: true }).click();
  await expect(page.getByRole("dialog", { name: /选择评论|选择附件/ })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("long material lists scroll inside dialogs while actions remain visible on narrow screens", async ({ page }) => {
  const cid = await create(page, "MC3-9005", {
    comments: Array.from({ length: 12 }, (_, index) => ({ id: String(index), author: "qa", body: "原始环境和运行命令\n".repeat(20) })),
    attachments: Array.from({ length: 12 }, (_, index) => ({ id: String(index), filename: `原始运行记录_${"long-name-".repeat(8)}${index}.log`, size: 12, content_url: `http://jira/attachment/${index}` })),
  });
  await page.setViewportSize({ width: 390, height: 700 });
  await page.goto(`${base}/jira-agent?view=testing&import=${cid}`);
  const comment = page.getByRole("dialog", { name: "选择评论", exact: true });
  await expect(comment).toBeVisible();
  expect(await comment.locator(".test-import-body").evaluate(element => element.scrollHeight > element.clientHeight)).toBeTruthy();
  await expect(comment.getByRole("button", { name: "下一步：选择附件" })).toBeInViewport();
  await comment.getByRole("button", { name: "下一步：选择附件" }).click();
  const file = page.getByRole("dialog", { name: "选择附件", exact: true });
  expect(await file.evaluate(element => element.scrollWidth <= element.clientWidth)).toBeTruthy();
  expect(await file.locator(".test-import-body").evaluate(element => element.scrollHeight > element.clientHeight)).toBeTruthy();
  await expect(file.getByRole("button", { name: "确认附件" })).toBeInViewport();
  await file.getByRole("button", { name: "取消", exact: true }).click();
});

test("failed selected attachment stays in selection; deselecting allows an empty import", async ({ page }) => {
  const cid = await create(page, "MC3-9002", { comments: [] });
  await page.request.post(`${base}/api/_testing/plan?missing_inputs=true`);
  try {
    await page.goto(`${base}/jira-agent?view=testing&import=${cid}`);
    const file = page.getByRole("dialog", { name: "选择附件", exact: true });
    await expect(file).toBeVisible();
    await file.getByLabel("带入文件 saxpy.cu", { exact: true }).check();
    await file.getByRole("button", { name: "确认附件" }).click();
    await expect(file.getByRole("alert")).toContainText("无法读取选中的文件：saxpy.cu");
    await expect(page.getByRole("heading", { name: "保存测试输入" })).toHaveCount(0);
    await file.getByLabel("带入文件 saxpy.cu", { exact: true }).uncheck();
    await file.getByRole("button", { name: "确认附件" }).click();
    await expect(page.getByRole("heading", { name: "保存测试输入" })).toBeVisible();
    const input = JSON.parse(await page.getByLabel("工单快照", { exact: true }).inputValue());
    expect(input.comments).toEqual([]); expect(input.attachments).toEqual([]);
  } finally { await page.request.post(`${base}/api/_testing/plan`); }
});

test("empty materials skip dialogs and a comment-only import can be cancelled without saving", async ({ page }) => {
  const empty = await create(page, "MC3-9003", { comments: [], attachments: [] });
  await page.goto(`${base}/jira-agent?view=testing&import=${empty}`);
  await expect(page.getByRole("heading", { name: "保存测试输入" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const cid = await create(page, "MC3-9004", { comments, attachments: [] });
  const before = await (await page.request.get(api + "/cases")).json();
  await page.goto(`${base}/jira-agent?view=testing&import=${cid}`);
  const comment = page.getByRole("dialog", { name: "选择评论", exact: true });
  await expect(comment).toBeVisible();
  await expect(comment.getByRole("button", { name: "确认评论" })).toBeVisible();
  await comment.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page).not.toHaveURL(/import=/);
  expect(await (await page.request.get(api + "/cases")).json()).toEqual(before);
  await page.goto(`${base}/jira-agent?view=testing&import=${cid}`);
  await expect(comment.getByRole("checkbox", { checked: true })).toHaveCount(0);
  await comment.getByRole("button", { name: "确认评论", exact: true }).click();
  await expect(page.getByRole("heading", { name: "保存测试输入" })).toBeVisible();
});
