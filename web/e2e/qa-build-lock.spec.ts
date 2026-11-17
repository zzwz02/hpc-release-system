import { test, expect } from "@playwright/test";

const base = process.env.QA_BUILD_LOCK_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated --platforms backend and Vite");

test("QA/RM lock an App per cycle; final publication locks all builds and workbench shows the same state", async ({ page }) => {
  async function login(username: string) {
    const response = await page.request.post(base + "/api/login", { data: { username, password: "test-password" } });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
  await login("app_qa");
  const state = await (await page.request.get(base + "/api/state")).json();
  const first = state.releases.find((row: { name: string }) => row.name === "browser-cycle-a");
  const second = state.releases.find((row: { name: string }) => row.name === "browser-cycle-b");
  const card = page.locator('[data-qa-app="platform-check"]');
  const lock = card;
  await page.goto(base + "/qa");
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lock).toContainText("构建未锁定");
  for (const width of [1440, 650, 390]) {
    await page.setViewportSize({ width, height: 900 });
    const controls = card.getByTestId("qa-build-lock-platform-check");
    const label = await controls.getByText("QA 状态", { exact: true }).boundingBox();
    const select = await controls.getByRole("combobox", { name: "QA 状态", exact: true }).boundingBox();
    const button = await controls.getByRole("button", { name: "锁定构建", exact: true }).boundingBox();
    expect(label && select && button).toBeTruthy();
    expect(label!.x + label!.width).toBeLessThanOrEqual(select!.x);
    expect(select!.x + select!.width).toBeLessThanOrEqual(button!.x);
    expect(Math.abs(label!.y + label!.height / 2 - select!.y - select!.height / 2)).toBeLessThan(2);
    expect(Math.abs(button!.y + button!.height / 2 - select!.y - select!.height / 2)).toBeLessThan(2);
    const badges = card.locator(".qa-card-h .qa-cur .pill");
    await expect(badges).toHaveCount(2);
    const qa = await badges.nth(0).boundingBox();
    const build = await badges.nth(1).boundingBox();
    expect(qa!.x + qa!.width).toBeLessThanOrEqual(build!.x);
    expect(Math.abs(qa!.y - build!.y)).toBeLessThan(2);
    await expect.poll(() => card.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await card.screenshot({ path: `/tmp/qa-build-lock-layout-${width}.png` });
  }
  const saved = page.waitForResponse(response => response.url().endsWith("/api/qa/build-lock"));
  await lock.getByRole("button", { name: "锁定构建", exact: true }).click();
  const response = await saved;
  expect(response.ok(), await response.text()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({ release_id: first.id, app_id: "platform-check", locked: true, expected_revision: 0 });
  await expect(lock).toContainText("构建已锁定");
  await page.reload();
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lock.getByRole("button", { name: "解锁构建", exact: true })).toBeVisible();
  const concurrent = await page.request.post(base + "/api/qa/build-lock", { data: {
    release_id: first.id, app_id: "platform-check", locked: false, expected_revision: 1,
  } });
  expect(concurrent.ok()).toBeTruthy();
  const conflict = page.waitForResponse(result => result.url().endsWith("/api/qa/build-lock"));
  await lock.getByRole("button", { name: "解锁构建", exact: true }).click();
  expect((await conflict).status()).toBe(409);
  await expect(page.getByText("构建锁定状态已变化，请刷新后重试", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "刷新当前页", exact: true }).first().click();
  await expect(lock.getByRole("button", { name: "锁定构建", exact: true })).toBeEnabled();
  await lock.getByRole("button", { name: "锁定构建", exact: true }).click();
  await expect(lock).toContainText("构建已锁定");

  await page.goto(base + "/apps");
  await page.getByLabel("选择版本", { exact: true }).selectOption(first.id);
  const row = page.getByTestId("app-row-platform-check");
  await expect(row).toContainText("构建已锁定");
  await row.click();
  const header = page.getByTestId("app-detail-header");
  await expect(header).toContainText("构建已锁定");
  await expect(header.getByText("HPC", { exact: true })).toHaveCount(0);
  await expect(header.getByText(/^正式版 /)).toHaveCount(0);
  await page.getByLabel("选择版本", { exact: true }).selectOption(second.id);
  await expect(row).toContainText("构建未锁定");
  await expect(header).toContainText("构建未锁定");

  await page.goto(base + "/qa");
  await page.getByLabel("选择 release").selectOption(second.id);
  await lock.getByRole("button", { name: "锁定构建", exact: true }).click();
  await expect(lock.getByRole("button", { name: "解锁构建", exact: true })).toBeEnabled();
  await lock.getByRole("button", { name: "解锁构建", exact: true }).click();
  await expect(lock).toContainText("构建未锁定");
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lock).toContainText("构建已锁定");
  await page.setViewportSize({ width: 390, height: 900 });
  await expect.poll(() => card.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await card.screenshot({ path: "/tmp/qa-build-lock.png" });

  await page.request.post(base + "/api/logout");
  await login("app_owner");
  await page.reload();
  await page.getByLabel("选择 release").selectOption(first.id);
  await expect(lock).toContainText("构建已锁定");
  await expect(lock.getByRole("button")).toHaveCount(0);
  const denied = await page.request.post(base + "/api/qa/build-lock", { data: {
    release_id: first.id, app_id: "platform-check", locked: false, expected_revision: 3,
  } });
  expect(denied.status()).toBe(403);

  await page.request.post(base + "/api/logout");
  await login("app_rm");
  expect((await page.request.post(base + "/api/releases/update", { data: { release_id: second.id, name: second.name,
    signup_deadline: "2000-01-01 23:59", qa_deadline: "2000-01-26 23:59", release_date: "2000-01-31" } })).ok()).toBeTruthy();
  const published = await page.request.post(base + "/api/releases/publish", { data: { release_id: second.id } });
  expect(published.ok(), await published.text()).toBeTruthy();
  await page.reload();
  await page.getByLabel("选择 release").selectOption(second.id);
  await expect(lock).toContainText("构建已锁定");
  await expect(lock.getByRole("button")).toHaveCount(0);
  const readback = await (await page.request.get(base + `/api/state?release_id=${second.id}`)).json();
  expect(readback.release.entries.every((entry: { build_locked: boolean }) => entry.build_locked)).toBe(true);
  expect((await page.request.post(base + "/api/qa/build-lock", { data: {
    release_id: second.id, app_id: "platform-check", locked: false, expected_revision: 3,
  } })).status()).toBe(400);
  expect((await page.request.post(base + "/api/releases/unlock", { data: { release_id: second.id, reason: "隔离验证" } })).ok()).toBeTruthy();
  await page.reload();
  await page.getByLabel("选择 release").selectOption(second.id);
  await expect(lock.getByRole("button", { name: "解锁构建", exact: true })).toBeEnabled();
  await lock.getByRole("button", { name: "解锁构建", exact: true }).click();
  await expect(lock).toContainText("构建未锁定");
});
