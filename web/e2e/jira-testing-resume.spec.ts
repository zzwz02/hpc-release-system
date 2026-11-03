/** Uses the isolated test site; no real models, Jira or GPU jobs. */
import { test, expect, type Page } from "@playwright/test";
const base = process.env.JIRA_TESTING_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated testing site");

async function failed(page: Page, title: string) {
  expect((await page.request.post(base + "/api/login", { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  expect((await page.request.post(base + "/api/jira-agent/testing/cases", { data: {
    title, criteria: "完成分析并交付验证记录", input: { issue: { key: "TEST-1", summary: title, description: "原始测试输入" }, note: "复现并定位问题", files: [] },
  } })).ok()).toBeTruthy();
  await page.request.post(base + "/api/_testing/plan?plan=stream_error");
  await page.goto(base + "/jira-agent?view=testing");
  await page.getByRole("checkbox", { name: "选择 " + title, exact: true }).check();
  await page.getByRole("button", { name: /^开始测试(?:（\d+）)?$/ }).click();
  const rows = page.getByRole("table", { name: "测试记录", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("执行失败", { timeout: 20000 });
  await expect(rows.first()).toContainText("未形成分析结果");
  const expand = rows.first().getByRole("button", { name: "展开", exact: true });
  if (await expand.isVisible()) await expand.click();
  const detail = page.getByRole("region", { name: "测试详情", exact: true });
  await expect(detail.getByRole("button", { name: "继续执行", exact: true })).toBeVisible();
  return { rows, detail };
}

test("continue a failed test in place and show one result with the recovery count", async ({ page }) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  const { rows, detail } = await failed(page, "模型中断后继续分析");
  const error = detail.locator("details.jira-agent-warning");
  await expect(error).not.toHaveAttribute("open");
  await error.locator("summary").click();
  await expect(error).toContainText("stream disconnected before completion");
  await error.locator("summary").click();
  await detail.screenshot({ path: "/tmp/jira-resume-ui/failed.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(detail.getByRole("button", { name: "继续执行", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.request.post(base + "/api/_testing/plan?plan=complete");
  const request = page.waitForRequest(request => request.method() === "POST" && request.url().endsWith("/resume"));
  await detail.getByRole("button", { name: "继续执行", exact: true }).click();
  expect(Object.keys((await request).postDataJSON())).toEqual(["turn_id"]);
  await expect(rows.first()).toContainText("通过 · AI · 恢复 1 次", { timeout: 20000 });
  await expect(rows).toHaveCount(1);
  await expect(page.locator(".test-suite-meta")).toContainText("1 个用例 · 已完成");
  await expect(detail.getByRole("button", { name: "继续执行", exact: true })).toHaveCount(0);
  await expect(detail.getByTestId("jira-agent-result")).toHaveCount(1);
  await detail.screenshot({ path: "/tmp/jira-resume-ui/completed.png" });
  await page.reload();
  await expect(rows.first()).toContainText("通过 · AI · 恢复 1 次");
  expect(errors).toEqual([]);
});

test("a manually resumed test can be cancelled without an automatic restart", async ({ page }) => {
  const { rows, detail } = await failed(page, "恢复过程中取消测试");
  await page.request.post(base + "/api/_testing/plan?plan=hold");
  await detail.getByRole("button", { name: "继续执行", exact: true }).click();
  await expect(rows.first()).toContainText("执行中");
  await expect(detail.getByRole("button", { name: "继续执行", exact: true })).toHaveCount(0);
  await detail.getByRole("button", { name: "取消测试", exact: true }).click();
  await expect(rows.first()).toContainText("已取消");
  await expect(rows).toHaveCount(1);
  await expect(detail.getByRole("button", { name: "继续执行", exact: true })).toHaveCount(0);
});
