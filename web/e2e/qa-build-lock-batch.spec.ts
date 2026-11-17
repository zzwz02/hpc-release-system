import { test, expect } from "@playwright/test";

const base = process.env.QA_BUILD_LOCK_BATCH_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated --platforms backend and Vite");

test("bulk lock controls change only the selected cycle and reject stale snapshots atomically", async ({ page }) => {
  async function login(username: string) {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
  }
  async function state(releaseId: string) {
    return (await (await page.request.get(base + `/api/state?release_id=${releaseId}`)).json()).release;
  }
  await login("app_qa");
  const initial = await (await page.request.get(base + "/api/state")).json();
  const first = initial.releases.find((row: { name: string }) => row.name === "browser-cycle-a");
  const second = initial.releases.find((row: { name: string }) => row.name === "browser-cycle-b");
  await page.goto(base + "/qa");
  await page.getByLabel("选择 release").selectOption(first.id);
  const heading = page.getByTestId("qa-failure-summary").locator(".panel-head");
  const lockAll = heading.getByRole("button", { name: "全部锁定", exact: true });
  const unlockAll = heading.getByRole("button", { name: "全部解锁", exact: true });
  await expect(lockAll).toBeEnabled();
  await expect(unlockAll).toBeDisabled();
  const saved = page.waitForResponse(response => response.url().endsWith("/api/qa/build-lock-batch"));
  await lockAll.click();
  const response = await saved;
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = response.request().postDataJSON();
  expect(body.release_id).toBe(first.id);
  expect(body.locked).toBe(true);
  expect(body.items.length).toBeGreaterThan(1);
  await expect(unlockAll).toBeEnabled();
  await expect(lockAll).toBeDisabled();
  expect((await state(first.id)).entries.every((entry: { build_locked: boolean }) => entry.build_locked)).toBe(true);
  expect((await state(second.id)).entries.every((entry: { build_locked: boolean }) => !entry.build_locked)).toBe(true);
  const audit = page.locator("#qaChangeLogPanel");
  await audit.locator("summary").click();
  await expect(audit.locator(".cl-row")).toHaveCount(1);
  await expect(audit).toContainText(`批量（${body.items.length} 个 App）`);
  await audit.locator(".cl-row").click();
  await expect(audit.locator(".cl-detail").getByRole("cell", { name: "未锁定", exact: true })).toHaveCount(body.items.length);
  await expect(audit.locator(".cl-detail").getByRole("cell", { name: "已锁定", exact: true })).toHaveCount(body.items.length);
  await expect(audit.locator(".cl-detail")).toContainText("platform-check");
  await audit.screenshot({ path: "/tmp/qa-build-lock-batch-audit.png" });

  for (const width of [1440, 650, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(lockAll).toBeVisible();
    await expect(unlockAll).toBeVisible();
    await expect.poll(() => heading.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await heading.screenshot({ path: `/tmp/qa-build-lock-batch-${width}.png` });
  }
  const concurrent = await page.request.post(base + "/api/qa/build-lock", { data: {
    release_id: first.id, app_id: "platform-check", locked: false, expected_revision: 1,
  } });
  expect(concurrent.ok()).toBeTruthy();
  const conflict = page.waitForResponse(result => result.url().endsWith("/api/qa/build-lock-batch"));
  await unlockAll.click();
  expect((await conflict).status()).toBe(409);
  await expect(page.getByText("本版本报名或构建锁定状态已变化，未保存任何改动，请刷新后重试", { exact: true })).toBeVisible();
  const unchanged = await state(first.id);
  expect(unchanged.entries.some((entry: { build_locked: boolean }) => entry.build_locked)).toBe(true);
  expect(unchanged.entries.some((entry: { build_locked: boolean }) => !entry.build_locked)).toBe(true);
  await heading.getByRole("button", { name: "刷新当前页", exact: true }).click();
  await expect(unlockAll).toBeEnabled();
  await unlockAll.click();
  await expect(unlockAll).toBeDisabled();
  await expect(lockAll).toBeEnabled();
  expect((await state(first.id)).entries.every((entry: { build_locked: boolean }) => !entry.build_locked)).toBe(true);
  await expect(audit.locator(".cl-row").filter({ hasText: /全部锁定|全部解锁/ })).toHaveCount(2);
  await expect(audit.locator(".cl-row")).toHaveCount(3); // Two batch operations and one individual operation.
  await page.getByRole("button", { name: /修改（填写 QA 结论/ }).first().click();
  await expect(lockAll).toBeDisabled();
  await expect(unlockAll).toBeDisabled();
  await page.getByRole("button", { name: "取消编辑", exact: true }).first().click();

  await page.request.post(base + "/api/logout");
  await login("app_owner");
  await page.reload();
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lockAll).toHaveCount(0);
  await expect(unlockAll).toHaveCount(0);
  expect((await page.request.post(base + "/api/qa/build-lock-batch", { data: body })).status()).toBe(403);
  await page.request.post(base + "/api/logout");
  await login("app_rm");
  expect((await page.request.post(base + "/api/releases/update", { data: { release_id: first.id, name: first.name,
    signup_deadline: "2000-01-01 23:59", qa_deadline: "2000-01-26 23:59", release_date: "2000-01-31" } })).ok()).toBeTruthy();
  expect((await page.request.post(base + "/api/releases/publish", { data: { release_id: first.id } })).ok()).toBeTruthy();
  await page.reload();
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lockAll).toBeDisabled();
  await expect(unlockAll).toBeDisabled();
  expect((await page.request.post(base + "/api/qa/build-lock-batch", { data: { ...body, locked: false } })).status()).toBe(400);
});
