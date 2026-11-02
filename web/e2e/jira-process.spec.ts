/** Only use the temporary SQLite site and local Codex/JIRA test peers. */
import { test, expect } from "@playwright/test";

const base = process.env.JIRA_EXECUTION_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated execution site");

test.afterEach(async ({ page }) => {
  const id = new URL(page.url()).searchParams.get("conversation");
  if (!id || id === "new") return;
  const response = await page.request.get(`${base}/api/jira-agent/conversations/${id}`);
  if (response.ok() && ["queued", "running"].includes((await response.json()).conversation.state)) {
    await page.request.post(`${base}/api/jira-agent/conversations/${id}/cancel`, { data: {} });
  }
});

test("process folds by default while steering, results and errors remain visible", async ({ page }, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 960 });
  expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  await page.request.post(`${base}/api/_testing/plan?plan=hold`);
  await page.goto(`${base}/jira-agent?issue=MC3-${Date.now()}`);
  await page.getByLabel("交单说明", { exact: true }).fill("验证原始案例并保留日志");
  await page.getByRole("checkbox", { name: /本轮结论发布到 JIRA 评论/ }).uncheck();
  await page.getByRole("button", { name: "交给 agent", exact: true }).click();
  const processes = page.getByTestId("jira-agent-process");
  await expect(processes).toHaveCount(1);
  const first = processes.first();
  await expect(first.getByText("正在思考", { exact: true })).toBeVisible();
  await expect(first).not.toHaveAttribute("open");
  await expect(page.getByText("验证原始案例并保留日志", { exact: true })).toBeVisible();
  await expect(first.getByTestId("jira-agent-tool-group")).not.toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("collapsed.png"), fullPage: true });

  await first.locator(":scope > summary").click();
  await expect(first.getByTestId("jira-agent-tool-group")).toBeVisible();
  await first.getByTestId("jira-agent-tool-group").locator(":scope > summary").click();
  await page.waitForResponse(response => response.url().includes("/events?after="));
  await expect(first).toHaveAttribute("open");
  await page.screenshot({ path: testInfo.outputPath("expanded.png"), fullPage: true });
  await first.locator(":scope > summary").click();
  await page.getByLabel("补充信息", { exact: true }).fill("保留原参数继续定位");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("保留原参数继续定位", { exact: true })).toBeVisible();
  await expect(processes).toHaveCount(2);
  await expect(first).not.toHaveAttribute("open");
  await expect(processes.last().getByText("正在思考", { exact: true })).toBeVisible();
  await expect(page.getByText("正在思考", { exact: true })).toHaveCount(1);

  await page.getByRole("button", { name: "取消本轮", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "取消本轮", exact: true }).click();
  await expect(page.getByText("正在思考", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "取消本轮", exact: true })).toHaveCount(0);

  await page.request.post(`${base}/api/_testing/plan?plan=complete`);
  await page.getByLabel("补充信息", { exact: true }).fill("继续验证并交付结论");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByTestId("jira-agent-result")).toBeVisible({ timeout: 20000 });
  await expect(processes.last()).not.toHaveAttribute("open");
  await expect(page.getByText("开始复现", { exact: true })).not.toBeVisible();
  await expect(page.getByText("正在思考", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("completed.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await processes.last().locator(":scope > summary").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("开始复现", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("mobile-expanded.png"), fullPage: true });

  await page.request.post(`${base}/api/_testing/plan?plan=auth_error`);
  await page.getByLabel("补充信息", { exact: true }).fill("验证失败提示");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.locator(".jira-agent-event.error").last()).toContainText("Incorrect API key provided", { timeout: 20000 });
  await expect(page.locator(".jira-agent-event.error").last()).toBeVisible();
  await expect(processes.last()).not.toHaveAttribute("open");
  await expect(page.getByText("正在思考", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("jira-agent-result")).toBeVisible();
  expect(errors).toEqual([]);
});
