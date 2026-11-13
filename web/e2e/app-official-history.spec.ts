import { test, expect } from "@playwright/test";

const base = process.env.APP_HISTORY_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --legacy-history and isolated Vite");

test("App details show all migrated publications and replace the final record after corrections", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_rm", password: "test-password" } })).ok()).toBeTruthy();
  const state = await (await page.request.get(base + "/api/state")).json();
  const recent = state.releases.find((release: { name: string }) => release.name === "history-3.10.0");
  const older = state.releases.find((release: { name: string }) => release.name === "history-3.9.0");
  expect(state.apps.find((app: { id: string }) => app.id === "release-pass").official.release_name).toBe("history-3.10.0");
  await page.goto(base + "/apps");
  const detailResponse = page.waitForResponse(r => r.url().includes("/api/apps/detail?app_id=release-pass"));
  await page.getByTestId("app-row-release-pass").click();
  const data = await (await detailResponse).json();
  expect(data.official_versions.map((row: { release_name: string }) => row.release_name)).toEqual(["history-3.10.0", "history-3.9.0"]);
  expect(data.official_versions[1].id).toBeNull();
  expect(data.official_versions[1].entry_id).toBeGreaterThan(0);
  const history = page.getByTestId("official-version-history");
  const table = page.getByTestId("official-version-table");
  await expect(table.getByTestId("official-version-row")).toHaveCount(2);
  const rows = table.getByTestId("official-version-row");
  await expect(rows.nth(0).getByTestId("official-release-name")).toHaveText("history-3.10.0");
  await expect(rows.nth(1).getByTestId("official-release-name")).toHaveText("history-3.9.0");
  await expect(rows.nth(1).getByTestId("official-app-version")).toHaveText("1.0");
  await expect(rows.nth(1).getByTestId("delivery-images")).toContainText("harbor.sh.mxcr.io/hpc-release/release-pass:v1@sha256:" + "1".repeat(64));
  // The detail is App history, independent of the selected monthly cycle.
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(older.id);
  await expect(rows).toHaveCount(2);
  await page.getByTestId("app-row-release-failed").click();
  await expect(history).toContainText("尚无正式版");
  await page.getByTestId("app-row-release-withdrawn").click();
  await expect(history).toContainText("尚无正式版");
  await page.getByTestId("app-row-release-pass").click();
  expect((await page.request.post(base + "/api/releases/unlock", { data: { release_id: recent.id, reason: "历史显示验证更正" } })).ok()).toBeTruthy();
  await page.getByTestId("refresh-btn").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.filter({ hasText: "history-3.10.0" })).toHaveCount(0);
  expect((await page.request.post(base + "/api/releases/publish", { data: { release_id: recent.id } })).ok()).toBeTruthy();
  await page.getByTestId("refresh-btn").click();
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "已撤销" })).toHaveCount(0);
  await expect(rows.filter({ hasText: "history-3.9.0" })).toHaveCount(1);
  // Re-publishing the older cycle must not promote it above the newer cycle.
  expect((await page.request.post(base + "/api/releases/unlock", { data: { release_id: older.id, reason: "更正旧周期" } })).ok()).toBeTruthy();
  expect((await page.request.post(base + "/api/releases/publish", { data: { release_id: older.id } })).ok()).toBeTruthy();
  const correctedState = await (await page.request.get(base + "/api/state")).json();
  expect(correctedState.apps.find((app: { id: string }) => app.id === "release-pass").official.release_name).toBe("history-3.10.0");
  await page.getByTestId("refresh-btn").click();
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0).getByTestId("official-release-name")).toHaveText("history-3.10.0");
  await history.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "/tmp/app-official-history.png", fullPage: true });

  await page.request.post(base + "/api/logout");
  await page.request.post(base + "/api/login", { data: { username: "app_owner", password: "test-password" } });
  await page.reload();
  await page.getByTestId("app-row-release-pass").click();
  await expect(rows).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await history.scrollIntoViewIfNeeded();
  await expect(table).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
