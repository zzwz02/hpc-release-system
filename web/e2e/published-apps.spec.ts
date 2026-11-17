import { test, expect } from "@playwright/test";

const base = process.env.PUBLISHED_APPS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --releases and isolated Vite");

for (const username of ["app_rm", "app_owner", "app_qa"]) {
  test(`${username} browses published Apps by frozen version with exact images and QA results`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    const optionsResponse = page.waitForResponse(r => new URL(r.url()).pathname === "/api/lookup/releases");
    const latestResponse = page.waitForResponse(r => new URL(r.url()).pathname === "/api/lookup/release");
    await page.goto(base + "/lookup");
    const options = await (await optionsResponse).json();
    expect(options.releases.map((release: { name: string }) => release.name)).toEqual(["history-3.10.0", "history-3.9.0", "history-empty"]);
    const latest = await (await latestResponse).json();
    expect(latest.release.name).toBe("history-3.10.0");
    expect(latest.apps.map((app: { app_id: string }) => app.app_id)).toEqual(["release-issues", "release-missing", "release-pass"]);
    const select = page.getByRole("combobox", { name: "发布版本", exact: true });
    await expect(select.locator("option")).toHaveText(["history-3.10.0", "history-3.9.0", "history-empty"]);
    await expect(page.getByRole("button", { name: "发布app列表", exact: true })).toHaveClass(/active/);
    await expect(page.getByRole("button", { name: "按 App", exact: true })).toHaveCount(0);
    const table = page.getByTestId("published-apps-table");
    await expect(table.getByTestId("published-app-row")).toHaveCount(3);
    const passed = table.getByTestId("published-app-row").filter({ hasText: "release-pass" });
    await expect(passed.locator("td").nth(1)).toHaveText("2.0");
    await expect(passed.locator(":scope > td").nth(2)).toContainText("harbor.sh.mxcr.io/hpc-release/release-pass:v2@sha256:" + "2".repeat(64));
    await expect(passed.locator(":scope > td").nth(3)).toHaveText("通过");
    const issues = table.getByTestId("published-app-row").filter({ hasText: "release-issues" });
    await expect(issues).toContainText("存在问题");
    await expect(issues).toContainText("版本 2 的 QA 说明");
    await expect(table.getByTestId("published-app-row").filter({ hasText: "release-missing" })).toContainText("尚未填写交付镜像");
    await expect(table).not.toContainText("release-failed");
    await expect(table).not.toContainText("release-withdrawn");
    const filter = page.getByRole("textbox", { name: "筛选发布 App", exact: true });
    await filter.fill("存在问题");
    await expect(table.getByTestId("published-app-row")).toHaveCount(1);
    await expect(issues).toBeVisible();
    await filter.fill("");
    await expect(table.getByTestId("published-app-row")).toHaveCount(3);
    const older = options.releases.find((release: { name: string }) => release.name === "history-3.9.0");
    const change = page.waitForResponse(r => r.url().includes(`/api/lookup/release?release_id=${older.id}`));
    await select.selectOption(older.id);
    expect((await change).ok()).toBeTruthy();
    await expect(passed.locator("td").nth(1)).toHaveText("1.0");
    await expect(passed.locator(":scope > td").nth(2)).toContainText("harbor.sh.mxcr.io/hpc-release/release-pass:v1@sha256:" + "1".repeat(64));
    await expect(issues).toContainText("版本 1 的 QA 说明");
    await page.screenshot({ path: `/tmp/published-apps-${username}.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(select).toBeVisible();
    await expect(passed).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await select.selectOption(options.releases.find((release: { name: string }) => release.name === "history-empty").id);
    await expect(table).toContainText("该版本没有发布成功的 App");
    await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "完整镜像名", exact: true })).toBeVisible();
  });
}
