import { test, expect } from "@playwright/test";

const base = process.env.MULTI_DELIVERY_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --platforms and isolated Vite");

test("Owner can cancel withdrawal or withdraw an unlocked cycle signup", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_owner", password: "test-password" } });
  const state = await (await page.request.get(base + "/api/state")).json();
  const release = state.releases.find((r: { name: string }) => r.name === "browser-cycle-a");
  await page.goto(base + "/apps");
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(release.id);
  await page.getByTestId("app-row-info-check").click();
  await page.getByTestId("app-withdraw-btn").click();
  await page.getByTestId("confirm-cancel").click();
  await expect(page.getByTestId("app-withdraw-btn")).toBeVisible();
  await page.getByTestId("app-withdraw-btn").click();
  const posted = page.waitForResponse(r => r.url().endsWith("/api/entries/withdraw"));
  await page.getByTestId("confirm-ok").click();
  expect((await posted).ok()).toBeTruthy();
  await expect(page.getByTestId("app-withdraw-btn")).toHaveCount(0);
  await expect(page.getByTestId("app-row-info-check")).toContainText("已撤回");
  const after = await (await page.request.get(base + `/api/state?release_id=${release.id}`)).json();
  expect(after.release.entries.find((e: { app_id: string }) => e.app_id === "info-check").status).toBe("withdrawn");
});

test("QA records separate OS/CPU images and publication/history retain both digests", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } });
  const state = await (await page.request.get(base + "/api/state")).json();
  const release = state.releases.find((r: { name: string }) => r.name === "browser-cycle-b");
  await page.goto(base + `/qa?release_id=${release.id}`);
  await page.getByRole("button", { name: /修改（上传 log/ }).first().click();
  await page.locator("#qaLogFile").setInputFiles({ name: "platform-log.txt", mimeType: "text/plain", buffer: Buffer.from("HPCG amd64 PASS\nHPCG arm64 PASS") });
  await page.getByRole("button", { name: "上传 log（覆盖）", exact: true }).click();
  const card = page.locator('[data-qa-app="platform-check"]');
  const table = card.getByTestId("delivery-images");
  const x86 = table.getByRole("row").filter({ hasText: "ubuntu20.04" }).filter({ has: page.getByRole("cell", { name: "x86", exact: true }) });
  const arm = table.getByRole("row").filter({ hasText: "ubuntu20.04" }).filter({ has: page.getByRole("cell", { name: "ARM", exact: true }) });
  const image = "harbor.sh.mxcr.io/hpc-release/hpcg-maca@sha256:";
  await x86.getByTestId("delivery-reference").fill(image + "b".repeat(64));
  await arm.getByTestId("delivery-reference").fill(image + "b".repeat(64));
  await card.getByTestId("qa-status-platform-check").selectOption("qa_passed");
  for (const [digit, error] of [["b", "实际 tags 与构建配置不匹配"], ["c", "Harbor 中未找到该镜像"]]) {
    await x86.getByTestId("delivery-reference").fill(image + digit.repeat(64));
    const failed = page.waitForResponse(r => r.url().endsWith("/api/qa/status-batch"));
    await page.getByRole("button", { name: "保存 QA 状态", exact: true }).first().click();
    const response = await failed;
    expect(response.status()).toBe(400);
    expect((await response.json()).error).toContain(error);
    await expect(page.getByText(error, { exact: false }).first()).toBeVisible();
    await expect(x86.getByTestId("delivery-reference")).toHaveValue(image + digit.repeat(64));
    const unchanged = await (await page.request.get(base + `/api/state?release_id=${release.id}`)).json();
    const entry = unchanged.release.entries.find((e: { app_id: string }) => e.app_id === "platform-check");
    expect(entry.qa_status).toBe("not_checked");
    expect(entry.deliverable_images).toHaveLength(0);
  }
  await x86.getByTestId("delivery-reference").fill("docker pull " + image + "a".repeat(64));
  const saved = page.waitForResponse(r => r.url().endsWith("/api/qa/status-batch"));
  await page.getByRole("button", { name: "保存 QA 状态", exact: true }).first().click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  const item = response.request().postDataJSON().items.find((i: { app_id: string }) => i.app_id === "platform-check");
  expect(item.deliverable_images).toHaveLength(2);
  expect(item.delivery_revision).toHaveLength(64);
  await page.reload();
  await expect(table.locator("tbody tr")).toHaveCount(2);
  await expect(x86).toContainText(image + "a".repeat(64));
  await expect(arm).toContainText(image + "b".repeat(64));
  await page.screenshot({ path: "/tmp/qa-multi-delivery.png", fullPage: true });

  await page.request.post(base + "/api/logout");
  await page.request.post(base + "/api/login", { data: { username: "app_rm", password: "test-password" } });
  expect((await page.request.post(base + "/api/releases/update", { data: { release_id: release.id, name: release.name,
    signup_deadline: "2000-01-01 23:59", qa_deadline: "2000-01-26 23:59", release_date: "2000-01-31" } })).ok()).toBeTruthy();
  expect((await page.request.post(base + "/api/releases/publish", { data: { release_id: release.id } })).ok()).toBeTruthy();
  await page.goto(base + "/apps");
  await page.getByTestId("app-row-platform-check").click();
  const history = page.getByTestId("official-version-row").first().getByTestId("delivery-images");
  await expect(history.locator("tbody tr")).toHaveCount(2);
  await expect(history).toContainText(image + "a".repeat(64));
  await expect(history).toContainText(image + "b".repeat(64));
  await history.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/history-multi-delivery.png", fullPage: true });
  await page.goto(base + "/lookup");
  const published = page.getByTestId("published-app-row").filter({ hasText: "HPCG" });
  await expect(published.getByTestId("delivery-images").locator(".delivery-image-item")).toHaveCount(2);
  await expect(published).toContainText(image + "a".repeat(64));
  await expect(published).toContainText(image + "b".repeat(64));
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await page.request.post(base + "/api/releases/unlock", { data: {
    release_id: release.id, reason: "隔离浏览器验证结束，恢复测试周期",
  } })).ok()).toBeTruthy();
});
