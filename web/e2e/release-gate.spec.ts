import { expect, test } from "@playwright/test";

const base = process.env.RELEASE_GATE_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated tests.run_release_gate_site and Vite");

test("RM manages exact release admission; SPD reads only; compact desktop/mobile layout", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const login = async (username: string) => {
    const response = await page.request.post(base + "/api/login", { data: { username, password: "test-password" } });
    expect(response.ok(), await response.text()).toBeTruthy();
  };
  await login("gate_rm");
  await page.goto(base + "/cicd");
  await page.getByRole("button", { name: "Release 构建准入", exact: true }).click();
  const panel = page.locator(".cicd-pane").filter({ has: page.getByRole("heading", { name: "Release 构建准入" }) });
  await expect(panel).toContainText("3.10.2.11");
  await panel.getByLabel("MACA_VERSION", { exact: true }).fill("3.10.2.12");
  await panel.getByRole("combobox", { name: "发布版本", exact: true }).selectOption({ label: "3.10.2" });
  await panel.getByLabel("备注", { exact: true }).fill("第二轮构建验证");
  const saveResponse = page.waitForResponse(r => r.url().endsWith("/api/cicd/release-gate/versions") && r.request().method() === "POST");
  await panel.getByRole("button", { name: "新增", exact: true }).click();
  const saved = await saveResponse;
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const row = panel.getByRole("row").filter({ hasText: "3.10.2.12" });
  await expect(row).toBeVisible();
  await row.getByRole("checkbox").click();
  await page.getByRole("button", { name: "停用", exact: true }).click();
  await expect(row.getByRole("checkbox")).not.toBeChecked();
  await row.getByRole("button", { name: "编辑 3.10.2.12" }).click();
  await panel.getByLabel("备注").fill("重新检查后启用");
  await panel.getByRole("button", { name: "保存", exact: true }).click();
  await expect(row).toContainText("重新检查后启用");
  await row.getByRole("checkbox").click();
  await page.getByRole("button", { name: "启用", exact: true }).click();
  await expect(row.getByRole("checkbox")).toBeChecked();
  await page.reload();
  await page.getByRole("button", { name: "Release 构建准入", exact: true }).click();
  await expect(row.getByRole("checkbox")).toBeChecked();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(panel.getByRole("button", { name: "新增", exact: true })).toBeVisible();
    const form = panel.locator("form");
    expect(await form.evaluate(e => e.scrollWidth <= e.clientWidth + 1)).toBeTruthy();
    await page.screenshot({ path: info.outputPath(`release-gate-${width}.png`), fullPage: true });
  }
  await page.request.post(base + "/api/logout");
  await login("gate_spd");
  await page.reload();
  await page.getByRole("button", { name: "Release 构建准入", exact: true }).click();
  await expect(row.getByRole("checkbox")).toBeDisabled();
  await expect(panel.getByRole("button", { name: "新增", exact: true })).toHaveCount(0);
  await expect(row.getByRole("button")).toHaveCount(0);
  expect(errors).toEqual([]);
});
