import { test, expect } from "@playwright/test";

const base = process.env.AUTO_WEEKLY_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_auto_weekly_report_site and isolated Vite");

test("automatic weekly reports: generation, history, editing, conflict, cancellation, failure, roles and mobile", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  expect((await page.request.post(base + "/api/login", { data: { username: "weekly_rm", password: "test-password" } })).ok()).toBeTruthy();
  const initialReports = (await (await page.request.get(base + "/api/weekly-report/auto/reports")).json()).reports;
  await page.goto(base + "/weekly-report");
  await expect(page.getByRole("heading", { name: "周报检查", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "上传周报", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "自动周报", exact: true }).click();
  const panel = page.getByTestId("auto-weekly-report");
  await panel.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  const generated = page.waitForResponse(r => r.url().endsWith("/auto/generate") && r.request().method() === "POST");
  await panel.getByRole("button", { name: "生成周报", exact: true }).click();
  const first = await (await generated).json();
  expect(first.options.group).toBe("pde_hpc");
  await expect(panel.getByRole("status")).toContainText("自动周报已生成", { timeout: 20000 });
  await expect(panel.getByRole("heading", { name: "本周要点", exact: true })).toBeVisible({ timeout: 20000 });
  await expect(panel).toContainText("平台研发（PHS-10）");
  await expect(panel).toContainText("A100 的 1.5 倍");
  expect((await page.request.get(base + "/api/weekly-report/auto/reports")).ok()).toBeTruthy();
  const reports = (await (await page.request.get(base + "/api/weekly-report/auto/reports")).json()).reports;
  const reportId = reports[0].id;
  await panel.getByRole("button", { name: "编辑周报", exact: true }).click();
  const editor = panel.getByLabel("周报正文", { exact: true });
  await editor.fill((await editor.inputValue()) + "\n用户调整分类\n");
  await panel.getByRole("button", { name: "保存", exact: true }).click();
  await expect(panel).toContainText("用户调整分类");
  // Another writer wins; a stale save keeps the local draft visible.
  await panel.getByRole("button", { name: "编辑周报", exact: true }).click();
  await editor.fill((await editor.inputValue()) + "未保存的草稿");
  const current = await (await page.request.get(base + `/api/weekly-report/auto/reports/${reportId}`)).json();
  expect((await page.request.put(base + `/api/weekly-report/auto/reports/${reportId}`, { data: { markdown: current.markdown + "\n其他作者修改", notes: current.notes, revision: current.revision } })).ok()).toBeTruthy();
  await panel.getByRole("button", { name: "保存", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("已被其他人修改");
  await expect(editor).toHaveValue(/未保存的草稿/);
  await page.getByRole("button", { name: "周报检查", exact: true }).click();
  await expect(page.getByText("有未保存的周报修改，确认放弃？", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).last().click();
  await expect(editor).toBeVisible();
  await panel.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "放弃修改", exact: true }).click();
  const download = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载 Markdown", exact: true }).click();
  expect((await download).suggestedFilename()).toContain("简洁版.md");
  // A successful regeneration replaces the same record and updates its cached
  // detail even though the selected ID and route do not change.
  const beforeRegeneration = await (await page.request.get(base + `/api/weekly-report/auto/reports/${reportId}`)).json();
  const expectedCount = initialReports.some((item: { report_date: string }) => item.report_date === first.options.report_date) ? initialReports.length : initialReports.length + 1;
  const regeneratedDetail = page.waitForResponse(r => r.url().endsWith(`/auto/reports/${reportId}`) && r.request().method() === "GET");
  await panel.getByRole("button", { name: "生成周报", exact: true }).click();
  await regeneratedDetail;
  await expect(panel.getByRole("status")).toContainText("自动周报已生成");
  const afterRegeneration = await (await page.request.get(base + `/api/weekly-report/auto/reports/${reportId}`)).json();
  expect(afterRegeneration.id).toBe(reportId);
  expect(afterRegeneration.revision).toBe(beforeRegeneration.revision + 1);
  await expect(panel).not.toContainText("用户调整分类");
  expect((await (await page.request.get(base + "/api/weekly-report/auto/reports")).json()).reports).toHaveLength(expectedCount);
  // Reopening a running job reattaches its progress stream.
  await panel.getByLabel("JIRA 组", { exact: true }).fill("slow");
  await panel.getByRole("button", { name: "生成周报", exact: true }).click();
  await expect(panel.getByRole("button", { name: "中止生成", exact: true })).toBeVisible();
  await page.reload();
  await expect(panel.getByRole("button", { name: "中止生成", exact: true })).toBeVisible();
  await panel.getByRole("button", { name: "中止生成", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("已中止");
  await panel.getByLabel("JIRA 组", { exact: true }).fill("fail");
  await panel.getByRole("button", { name: "生成周报", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("测试 JIRA 读取失败");
  expect((await (await page.request.get(base + "/api/weekly-report/auto/reports")).json()).reports).toHaveLength(expectedCount);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(panel.getByRole("heading", { name: "本周要点", exact: true })).toBeVisible();
  expect(await panel.evaluate(el => el.scrollWidth <= window.innerWidth + 5)).toBeTruthy();
  // Sign in as another identity without clearing the browser's query cache.
  expect((await page.request.post(base + "/api/login", { data: { username: "weekly_owner", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + `/weekly-report?tab=auto&report=${reportId}`);
  await expect(panel.getByRole("heading", { name: "本周要点", exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "生成周报", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "编辑周报", exact: true })).toHaveCount(0);
  await panel.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(panel.getByRole("alert")).toHaveCount(0);
  expect((await page.request.post(base + "/api/weekly-report/auto/generate", { data: first.options })).status()).toBe(403);
  expect(errors).toEqual([]);
});


test("image evidence timeout shows the exact attachment and continues the weekly report", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "weekly_rm", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/weekly-report?tab=auto");
  const panel = page.getByTestId("auto-weekly-report");
  await panel.getByLabel("JIRA 组", { exact: true }).fill("image_timeout");
  const generation = page.waitForResponse(r => r.url().endsWith("/auto/generate") && r.request().method() === "POST");
  await panel.getByRole("button", { name: "生成周报", exact: true }).click();
  await generation;
  await expect(panel.getByRole("status")).toContainText("识别图片 PHS-1/result.png（最多 30 秒）");
  await expect(panel.getByRole("status")).toContainText("自动周报已生成", { timeout: 20000 });
  await panel.getByText("交付说明与来源", { exact: true }).click();
  await expect(panel).toContainText("result.png 未识别");
  await expect(panel).toContainText("next.png 未识别：本次任务图片识别已停用");
  await expect(panel.getByRole("heading", { name: "本周要点", exact: true })).toBeVisible();
});
