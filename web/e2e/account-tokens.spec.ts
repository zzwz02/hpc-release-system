import { expect, test } from "@playwright/test";

const base = process.env.API_TOKEN_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated tests.run_api_token_site");
test.use({ launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : undefined });

test("account token lifecycle, real authorization and compact responsive dialog", async ({ page, context }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  expect((await page.request.post(base + "/api/login", { data: { username: "token_owner", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/apps");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.getByRole("button", { name: "账号设置", exact: true }).click();
    const menu = page.getByRole("menu", { name: "账号设置" });
    const bounds = await menu.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    const icon = await page.getByRole("button", { name: "账号设置", exact: true }).locator("svg").boundingBox();
    expect(icon!.width).toBeGreaterThanOrEqual(18);
    await expect(menu.getByRole("menuitem", { name: "退出登录" })).toBeVisible();
    await page.screenshot({ path: info.outputPath(`account-menu-${width}.png`), fullPage: true, animations: "disabled" });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "账号设置" })).toBeFocused();
  }
  await page.getByRole("button", { name: "账号设置", exact: true }).click();
  await page.getByRole("menuitem", { name: "账号信息", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "账号信息" })).toContainText("Owner");
  await page.screenshot({ path: info.outputPath("account-profile-390.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("button", { name: "关闭账号信息" }).click();
  await page.getByRole("button", { name: "账号设置", exact: true }).click();
  await page.getByRole("menuitem", { name: "API Token", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "API Token", exact: true });
  const tokenName = `Jenkins browser check ${Date.now()}`;
  await dialog.getByLabel("名称", { exact: true }).fill(tokenName);
  const response = page.waitForResponse(r => r.url().endsWith("/api/account/tokens") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "生成 Token", exact: true }).click();
  const created = (await (await response).json()).token;
  try {
    expect((await page.request.delete(base + `/api/account/tokens/${created.id}`)).status()).toBe(409);
    await expect(dialog.getByLabel("完整 API Token")).toHaveValue(created.token);
    await dialog.getByRole("button", { name: "复制 Token", exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(created.token);
    const headers = { Authorization: `Bearer ${created.token}` };
    expect((await page.request.get(base + "/api/apps/owner-lookup?git_url=hpc_token_fixture&branch=maca", { headers })).ok()).toBeTruthy();
    await dialog.getByRole("button", { name: "已保存", exact: true }).click();
    await dialog.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(dialog).toContainText("Owner 查询");
    await expect(dialog.getByLabel("完整 API Token")).toHaveCount(0);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      const bounds = await dialog.boundingBox();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
      expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBeTruthy();
      expect(await page.locator(".topbar").evaluate(element => element.scrollHeight <= element.clientHeight + 1 && element.scrollWidth <= element.clientWidth + 1)).toBeTruthy();
      await expect(dialog.getByRole("button", { name: "生成 Token", exact: true })).toBeVisible();
      await page.screenshot({ path: info.outputPath(`api-token-${width}.png`), fullPage: true, animations: "disabled" });
    }
    await dialog.getByRole("button", { name: `撤销 ${tokenName}`, exact: true }).click();
    await page.getByTestId("confirm-cancel").click();
    await expect(dialog.getByRole("button", { name: `撤销 ${tokenName}`, exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: `撤销 ${tokenName}`, exact: true }).click();
    await page.getByTestId("confirm-ok").click();
    await expect(dialog.getByRole("button", { name: `撤销 ${tokenName}`, exact: true })).toHaveCount(0);
    expect((await page.request.get(base + "/api/apps/owner-lookup?git_url=hpc_token_fixture&branch=maca", { headers })).status()).toBe(401);
    await page.reload();
    await page.getByRole("button", { name: "账号设置", exact: true }).click();
    await page.getByRole("menuitem", { name: "API Token", exact: true }).click();
    await expect(dialog).toContainText(tokenName);
    await expect(dialog.getByLabel("完整 API Token")).toHaveCount(0);
    const metadata = await page.request.get(base + "/api/account/tokens");
    expect(await metadata.text()).not.toContain(created.token);
    await dialog.getByRole("button", { name: `删除 ${tokenName}`, exact: true }).click();
    await page.getByTestId("confirm-cancel").click();
    await expect(dialog).toContainText(tokenName);
    await dialog.getByRole("button", { name: `删除 ${tokenName}`, exact: true }).click();
    await page.getByTestId("confirm-ok").click();
    await expect(dialog.getByRole("button", { name: `删除 ${tokenName}`, exact: true })).toHaveCount(0);
    expect((await page.request.get(base + "/api/apps/owner-lookup?git_url=hpc_token_fixture&branch=maca", { headers })).status()).toBe(401);
    await page.reload();
    await page.getByRole("button", { name: "账号设置", exact: true }).click();
    await page.getByRole("menuitem", { name: "API Token", exact: true }).click();
    await expect(dialog).not.toContainText(tokenName);
    await dialog.getByRole("button", { name: "关闭 Token 管理" }).click();
    expect(errors).toEqual([]);
  } finally {
    await page.request.post(base + `/api/account/tokens/${created.id}/revoke`);
    await page.request.delete(base + `/api/account/tokens/${created.id}`);
  }
  await page.getByRole("button", { name: "账号设置", exact: true }).click();
  await page.getByRole("menuitem", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "账号设置" })).toHaveCount(0);
  expect((await (await page.request.get(base + "/api/me")).json()).user).toBeNull();
});
