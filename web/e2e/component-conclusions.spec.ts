import { test, expect } from "@playwright/test";

const base = process.env.COMPONENT_CONCLUSIONS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated tests.run_app_info_site --dependencies and Vite");

test("driver / SDK creates one joint record for every eligible App and retries failed scope queries", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  const inventory = await (await page.request.get(base + "/api/lookup/latest-running")).json();
  const ids = [...new Set(inventory.rows.map((row: { app_id: string }) => row.app_id))].sort();
  await page.route("**/api/component-compatibility/components", route => route.fulfill({
    status: 502, contentType: "application/json",
    body: JSON.stringify({ error: "无法生成完整组件清单：openfoam：Harbor 查询失败，请重试" }),
  }), { times: 1 });
  await page.goto(base + "/dependencies?tab=components");
  const pane = page.getByTestId("component-conclusions-pane");
  await pane.getByRole("button", { name: /新建/ }).click();
  const form = pane.getByRole("form", { name: "新建组件兼容性结论" });
  await expect(form.getByRole("alert")).toContainText("openfoam：Harbor 查询失败");
  await form.getByRole("button", { name: "重试", exact: true }).click();
  await expect(form.getByRole("option", { name: `driver / SDK（全部 ${ids.length} 个 App）`, exact: true })).toHaveCount(1);
  await expect(form.locator('option[value="driver"], option[value="sdk"]')).toHaveCount(0);
  await form.getByRole("combobox", { name: "组件", exact: true }).selectOption("driver-sdk");
  const version = `browser-${Date.now()}`;
  await form.getByLabel("组件版本（MACA 版本）", { exact: true }).fill(version);
  const created = page.waitForResponse(response => response.url().endsWith("/api/component-compatibility") && response.request().method() === "POST");
  await form.getByRole("button", { name: "创建并生成 App 清单" }).click();
  const response = await created;
  expect(response.ok(), await response.text()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({ component: "driver-sdk", component_version: version, preview_id: expect.any(String) });
  const report = await response.json();
  expect(report.component).toBe("driver-sdk");
  expect(report.apps.map((app: { app_id: string }) => app.app_id).sort()).toEqual(ids);
  await expect(pane.getByTestId("component-result-runtime-unpublished")).toHaveCount(0);
  await expect(pane.getByTestId("component-result-runtime-unknown")).toBeVisible();
  await expect(pane.getByLabel("查看已有记录").locator("option:checked")).toHaveText(`driver / SDK / ${version}`);
  const row = pane.getByTestId("component-result-runtime-published");
  await row.getByRole("combobox").selectOption("pass");
  await row.getByRole("textbox").fill("driver 与 SDK 联合验证通过");
  await row.getByRole("button", { name: /保存.*兼容性结论/ }).click();
  await expect(row).toContainText("app_qa");
  for (const alias of ["driver", "sdk"]) {
    const duplicate = await page.request.post(base + "/api/component-compatibility", {
      data: { component: alias, component_version: version },
    });
    expect(duplicate.status()).toBe(409);
  }
  await page.reload();
  await expect(pane.locator(".component-conclusion-summary")).toContainText(`driver / SDK · ${version}`);
  await expect(row.getByRole("combobox")).toHaveValue("pass");
  await expect(row.getByRole("textbox")).toHaveValue("driver 与 SDK 联合验证通过");
  const saved = await (await page.request.get(`${base}/api/component-compatibility/${report.id}`)).json();
  expect(saved.apps.find((app: { app_id: string }) => app.app_id === "runtime-published").qa_comment).toBe("driver 与 SDK 联合验证通过");
  await page.screenshot({ path: "/tmp/component-conclusions-driver-sdk.png", fullPage: true });
});

test("QA creates component versions, saves App verdicts, and Owners read fixed scope", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/dependencies?tab=components");
  const pane = page.getByTestId("component-conclusions-pane");
  await expect(pane.getByRole("heading", { name: "组件兼容性结论", exact: true })).toBeVisible();
  await pane.getByRole("button", { name: /新建/ }).click();
  const form = pane.getByRole("form", { name: "新建组件兼容性结论" });
  const component = "torch 2.8 + py312";
  const version = `3.10.${Date.now()}`;
  await form.getByRole("combobox", { name: "组件", exact: true }).selectOption(component);
  await form.getByLabel("组件版本（MACA 版本）", { exact: true }).fill(version);
  let created = page.waitForResponse(response => response.url().endsWith("/api/component-compatibility") && response.request().method() === "POST");
  await form.getByRole("button", { name: "创建并生成 App 清单" }).click();
  const first = await (await created).json();
  expect(first.apps).toHaveLength(2);
  const row = pane.getByTestId("component-result-runtime-published");
  await expect(row).toBeVisible();
  const name = first.apps.find((app: { app_id: string }) => app.app_id === "runtime-published").app_name;
  const scope = row.locator("xpath=following-sibling::tr[1]");
  await scope.locator("summary").click();
  await expect(scope.getByRole("columnheader")).toHaveText(["测试项", "OS", "CPU 架构", "支持芯片"]);
  await expect(scope).toContainText("C500");
  await row.getByRole("combobox", { name: `${name} 的结论`, exact: true }).selectOption("fail");
  await row.getByRole("textbox", { name: `${name} 的 comment`, exact: true }).fill("C600 测试失败（浏览器验证）");
  await row.getByRole("button", { name: `保存 ${name} 的兼容性结论`, exact: true }).click();
  await expect(row).toContainText("app_qa");
  let stored = await (await page.request.get(`${base}/api/component-compatibility/${first.id}`)).json();
  let app = stored.apps.find((item: { app_id: string }) => item.app_id === "runtime-published");
  expect(app.qa_status).toBe("fail");
  expect(app.qa_comment).toBe("C600 测试失败（浏览器验证）");
  await page.reload();
  await expect(row.getByRole("combobox", { name: `${name} 的结论`, exact: true })).toHaveValue("fail");
  await row.getByRole("combobox", { name: `${name} 的结论`, exact: true }).selectOption("pass");
  await row.getByRole("textbox", { name: `${name} 的 comment`, exact: true }).fill("");
  await row.getByRole("button", { name: `保存 ${name} 的兼容性结论`, exact: true }).click();
  await expect(pane).toContainText("pass 1");
  stored = await (await page.request.get(`${base}/api/component-compatibility/${first.id}`)).json();
  app = stored.apps.find((item: { app_id: string }) => item.app_id === "runtime-published");
  expect(app.qa_comment).toBe("");
  await row.getByRole("textbox", { name: `${name} 的 comment`, exact: true }).fill("当前页面草稿");
  expect((await page.request.post(`${base}/api/component-compatibility/${first.id}/apps/runtime-published`, {
    data: { qa_status: "fail", qa_comment: "另一位 QA 已更新", revision: app.revision },
  })).ok()).toBeTruthy();
  await row.getByRole("button", { name: `保存 ${name} 的兼容性结论`, exact: true }).click();
  await expect(pane.getByRole("alert")).toContainText("其他人修改");
  await expect(row.getByRole("textbox", { name: `${name} 的 comment`, exact: true })).toHaveValue("当前页面草稿");
  await page.getByRole("navigation", { name: "QA", exact: true }).getByRole("link", { name: "QA report", exact: true }).click();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(row.getByRole("textbox", { name: `${name} 的 comment`, exact: true })).toHaveValue("当前页面草稿");
  await pane.getByRole("button", { name: /新建/ }).click();
  await page.getByRole("button", { name: "放弃修改", exact: true }).click();
  await form.getByRole("combobox", { name: "组件", exact: true }).selectOption(component);
  await form.getByLabel("组件版本（MACA 版本）", { exact: true }).fill(version + ".1");
  created = page.waitForResponse(response => response.url().endsWith("/api/component-compatibility") && response.request().method() === "POST");
  await form.getByRole("button", { name: "创建并生成 App 清单" }).click();
  const second = await (await created).json();
  expect(second.id).not.toBe(first.id);
  expect(second.apps.every((item: { qa_status: string }) => !item.qa_status)).toBe(true);
  await pane.getByLabel("查看已有记录").selectOption(String(first.id));
  await expect(row.getByRole("textbox", { name: `${name} 的 comment`, exact: true })).toHaveValue("另一位 QA 已更新");
  await row.locator("xpath=following-sibling::tr[1]").locator("summary").click();
  await page.setViewportSize({ width: 390, height: 1000 });
  await expect(row).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/component-conclusions-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: "/tmp/component-conclusions-desktop.png", fullPage: true });
  expect((await page.request.post(base + "/api/login", { data: { username: "app_owner", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/dependencies?tab=components");
  await pane.getByLabel("查看已有记录").selectOption(String(first.id));
  await expect(row).toContainText("另一位 QA 已更新");
  await expect(pane.getByRole("button", { name: /新建/ })).toHaveCount(0);
  await expect(row.getByRole("textbox")).toHaveCount(0);
  const denied = await page.request.post(`${base}/api/component-compatibility/${first.id}/apps/runtime-published`, { data: { qa_status: "pass", qa_comment: "", revision: 0 } });
  expect(denied.status()).toBe(403);
});
