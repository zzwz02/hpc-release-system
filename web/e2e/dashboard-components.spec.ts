import { test, expect } from "@playwright/test";

const base = process.env.DASHBOARD_COMPONENTS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --signup and isolated Vite");

test("dashboard components follow selected cycles and explicit refresh while the deadline strip remains", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_rm", password: "test-password" } })).ok()).toBeTruthy();
  const state = await (await page.request.get(base + "/api/state")).json();
  const releases = Object.fromEntries(state.releases.map((release: { name: string; id: string }) => [release.name, release.id]));
  await page.goto(base + "/");
  const version = page.getByRole("combobox", { name: "选择版本", exact: true });
  await version.selectOption(releases["signup-open-a"]);
  const panel = page.getByTestId("dashboard-components");
  for (const text of ["signup-open-a", "Driver", "driver-0", "SDK", "sdk-0", "PyTorch", "torch-0", "驱动说明"]) {
    await expect(panel).toContainText(text);
  }
  await expect(page.getByRole("heading", { name: "发布时间线", exact: true })).toHaveCount(0);
  await expect(panel.getByRole("button")).toHaveCount(0);
  const deadlines = page.getByRole("region", { name: "当前版本时间节点" });
  for (const label of ["报名截止", "QA 截止", "发布日"]) await expect(deadlines).toContainText(label);
  expect((await page.request.post(base + "/api/releases/environment", {
    data: { release_id: releases["signup-open-a"], rows: [
      { component: "Driver", version: "driver-revised", note: "RM 更新的组件说明" },
      { component: "SDK", version: "sdk-revised", note: "" },
      { component: "PyTorch", version: "torch-revised", note: "" },
    ] },
  })).ok()).toBeTruthy();
  await expect(panel).toContainText("driver-0");
  await page.getByTestId("refresh-btn").click();
  await expect(panel).toContainText("driver-revised");
  await expect(panel).toContainText("RM 更新的组件说明");
  await version.selectOption(releases["signup-open-b"]);
  await expect(panel).toContainText("signup-open-b");
  await expect(panel).toContainText("driver-1");
  await expect(panel).not.toContainText("driver-revised");
  await page.screenshot({ path: "/tmp/dashboard-components.png", fullPage: true });
});

for (const username of ["app_owner", "app_qa", "app_guest"]) {
  test(`${username} sees component versions read-only`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    await page.goto(base + "/");
    const panel = page.getByTestId("dashboard-components");
    await expect(panel).toContainText("组件信息");
    await expect(panel).toContainText("SDK");
    await expect(panel.locator("button, input, select")).toHaveCount(0);
    await expect(page.getByRole("region", { name: "当前版本时间节点" })).toBeVisible();
  });
}
