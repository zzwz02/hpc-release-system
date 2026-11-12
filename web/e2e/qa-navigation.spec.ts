import { test, expect } from "@playwright/test";

const base = process.env.QA_NAV_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site and isolated Vite");

for (const username of ["app_owner", "app_qa"]) {
  test(`${username} opens history and dependencies through QA and direct links`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    await page.goto(base + "/qa");
    const tabs = page.locator("nav.tabs");
    const subnav = page.getByRole("navigation", { name: "QA", exact: true });
    await expect(subnav.getByRole("link")).toHaveText(["QA", "历史查询", "组件依赖"]);
    await expect(tabs.getByRole("link", { name: "历史查询", exact: true })).toHaveCount(0);
    await expect(tabs.getByRole("link", { name: "组件依赖", exact: true })).toHaveCount(0);
    await subnav.getByRole("link", { name: "历史查询", exact: true }).click();
    await expect(page.getByRole("heading", { name: "历史查询", exact: true })).toBeVisible();
    await expect(tabs.getByRole("link", { name: "QA", exact: true })).toHaveClass(/active/);
    await page.getByRole("button", { name: "按 App", exact: true }).click();
    await page.getByRole("combobox", { name: "选择 App", exact: true }).selectOption("info-check");
    await page.getByTestId("lookup-search").click();
    await expect(page.getByTestId("lookup-record").first()).toBeVisible();
    await subnav.getByRole("link", { name: "组件依赖", exact: true }).click();
    await expect(page.getByRole("heading", { name: "组件依赖", exact: true })).toBeVisible();
    expect((await page.request.get(base + "/api/dependencies/compat.csv")).ok()).toBeTruthy();
    await page.reload();
    await expect(subnav.getByRole("link", { name: "组件依赖", exact: true })).toHaveClass(/active/);
    await expect(tabs.getByRole("link", { name: "QA", exact: true })).toHaveClass(/active/);
    await page.screenshot({ path: `/tmp/qa-navigation-${username}.png`, fullPage: true });
  });
}

for (const username of ["app_guest", "app_spd"]) {
  test(`${username} cannot use history or dependencies direct links or APIs`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    for (const path of ["/lookup", "/dependencies"]) {
      await page.goto(base + path);
      await expect(page.getByRole("link", { name: "历史查询", exact: true })).toHaveCount(0);
      await expect(page.getByRole("link", { name: "组件依赖", exact: true })).toHaveCount(0);
      await expect(page.getByTestId(path === "/lookup" ? "lookup-page" : "dependencies-page")).toHaveCount(0);
    }
    for (const path of ["/api/lookup/app?app_id=info-check", "/api/dependencies", "/api/dependencies/compat.csv"]) {
      expect((await page.request.get(base + path)).status()).toBe(403);
    }
  });
}
