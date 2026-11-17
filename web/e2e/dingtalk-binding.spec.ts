import { test, expect } from "@playwright/test";

for (const width of [390, 1366]) {
  test(`DingTalk binding and unsubscribe at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    let bound = false, enabled = false;
    const writes: unknown[] = [];
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let data: unknown = {};
      if (path === "/api/me") data = { user: { username: "hajiang", display_name: "Haiyang Jiang", role: "Owner" } };
      else if (path === "/api/state") data = { releases: [], apps: [], user_display_names: {} };
      else if (path.endsWith("/failures")) data = { records: [], total: 0, page: 1, page_size: 20, total_pages: 1 };
      else if (path.endsWith("/summary")) data = { total_records: 0, groups: [] };
      else if (path.endsWith("/preferences")) data = { enabled: true };
      else if (path.endsWith("/dingtalk/code")) {
        writes.push(route.request().postDataJSON());
        data = { command: "绑定提醒 00112233445566778899aabbccddeeff", group_name: "test", expires_at: new Date(Date.now() + 600000).toISOString() };
      } else if (path.endsWith("/dingtalk")) {
        if (route.request().method() === "POST") {
          const payload = route.request().postDataJSON();
          writes.push(payload);
          if (payload.action === "disable") enabled = false;
          if (payload.action === "enable") enabled = true;
          if (payload.action === "unbind") { bound = false; enabled = false; }
        }
        data = { available: true, bound, enabled, account: "hajiang", worker_online: true,
          group_name: "test", nickname: "Haiyang Jiang", user_id_hint: "****3472" };
      }
      await route.fulfill({ json: data });
    });
    await page.goto("/jenkins-failures");
    await page.getByRole("button", { name: /钉钉提醒/ }).click();
    const dialog = page.getByRole("dialog", { name: "钉钉个人提醒" });
    await dialog.getByRole("button", { name: "生成绑定码", exact: true }).click();
    await expect(dialog.getByLabel("一次性绑定命令")).toHaveValue(/绑定提醒 /);
    expect(writes).toEqual([{}]);
    const overflow = await dialog.evaluate(el => {
      const box = el.getBoundingClientRect();
      return box.left < 0 || box.right > window.innerWidth || el.scrollWidth > el.clientWidth + 1;
    });
    expect(overflow).toBeFalsy();
    await page.screenshot({ path: testInfo.outputPath(`binding-code-${width}.png`), fullPage: true });
    bound = true; enabled = true;
    await dialog.getByRole("button", { name: "刷新状态" }).click();
    await expect(dialog.getByText("提醒已开启", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel("一次性绑定命令")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath(`binding-success-${width}.png`), fullPage: true });
    await dialog.getByRole("button", { name: "暂停提醒" }).click();
    await expect(dialog.getByText("提醒已暂停", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "恢复提醒" }).click();
    await dialog.getByRole("button", { name: "解除绑定" }).click();
    await expect(dialog.getByRole("button", { name: "确认解绑" })).toBeVisible();
    await dialog.getByRole("button", { name: "确认解绑" }).click();
    await expect(dialog.getByRole("button", { name: "生成绑定码", exact: true })).toBeVisible();
    expect(writes).toEqual([{}, { action: "disable" }, { action: "enable" }, { action: "unbind" }]);
    expect(errors).toEqual([]);
  });
}
