import { test, expect, type Page } from "@playwright/test";

const base = process.env.APP_INFO_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site and isolated Vite");

async function login(page: Page, username: string) {
  expect((await page.request.post(base + "/api/login", {
    data: { username, password: "test-password" },
  })).ok()).toBeTruthy();
  await page.goto(base + "/apps");
  await page.getByTestId("app-row-info-check").click();
  await expect(page.getByTestId("basic-info-section")).toBeVisible();
}

test("RM edits metadata, preserves drafts, syncs and sees scope warnings across cycles and tabs", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await login(page, "app_rm");
  const basic = page.getByTestId("basic-info-section");
  await expect(page.getByTestId("detail-tab-signup")).toHaveCount(0);
  await expect(page.getByTestId("detail-tab-app_info")).toHaveText("基本信息和app_info");
  await expect(page.getByTestId("app-detail-header").getByTestId("app-info-sync")).toBeVisible();
  expect(await page.getByTestId("app-info-pane").locator(":scope > :first-child").getAttribute("data-testid"))
    .toBe("basic-info-section");
  await page.screenshot({ path: "/tmp/app-info-basic-check.png", fullPage: true });
  await basic.getByRole("button", { name: "✎ 修改" }).click();
  await basic.getByLabel("官方名称", { exact: true }).fill("App browser check");
  await basic.getByLabel("Owner", { exact: true }).fill("app_owner, app_other");
  const saved = page.waitForResponse(r => r.url().endsWith("/api/apps/profile") && r.request().method() === "POST");
  await basic.getByRole("button", { name: "保存", exact: true }).click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({
    app_id: "info-check", fields: {
      official_name: "App browser check", owners: "app_owner, app_other", doc_target: "manual", type: "性能基准测试",
    },
  });
  await expect(page.getByTestId("app-detail-header")).toContainText("App browser check");
  const state = await (await page.request.get(base + "/api/state")).json();
  const app = state.apps.find((item: { id: string }) => item.id === "info-check");
  expect(app.owners).toEqual(["app_other", "app_owner"]);
  expect(app.description).toBe("保留的手册描述");

  await basic.getByRole("button", { name: "✎ 修改" }).click();
  await basic.getByLabel("官方名称", { exact: true }).fill("Unsubmitted draft");
  await expect(page.getByTestId("app-info-sync")).toBeDisabled();
  await page.getByTestId("detail-tab-profile").click();
  await page.getByTestId("confirm-cancel").click();
  await expect(basic.getByLabel("官方名称", { exact: true })).toHaveValue("Unsubmitted draft");
  await page.getByTestId("refresh-btn").click();
  await page.getByTestId("confirm-cancel").click();
  const releases = page.getByRole("combobox", { name: "选择版本" });
  const currentRelease = await releases.inputValue();
  const nextRelease = state.releases.find((release: { id: string }) => release.id !== currentRelease).id;
  await releases.selectOption(nextRelease);
  await page.getByTestId("confirm-cancel").click();
  await expect(releases).toHaveValue(currentRelease);
  await expect(basic.getByLabel("官方名称", { exact: true })).toHaveValue("Unsubmitted draft");
  await basic.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByTestId("detail-tab-profile").click();
  await expect(page.getByLabel("官方名称", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("官方 URL", { exact: true })).toHaveValue("https://example.test");
  const syncResponse = page.waitForResponse(r => r.url().endsWith("/api/app-info/sync"));
  await page.getByTestId("app-info-sync").click();
  expect((await (await syncResponse).json()).status).toBe("updated");
  await expect(page.getByTestId("app-scope-warning")).toContainText("待 QA 审批");
  await expect(page.getByTestId("app-scope-warning")).toContainText("C600");
  await releases.selectOption(nextRelease);
  await expect(page.getByTestId("app-scope-warning")).toContainText("最新 app_info 已同步");
  await page.getByTestId("detail-tab-cicd").click();
  await expect(page.getByTestId("app-scope-warning")).toBeVisible();
  await page.screenshot({ path: "/tmp/app-info-scope-check.png", fullPage: true });

  await page.getByTestId("app-row-info-error").click();
  await page.getByTestId("app-info-sync").click();
  await expect(page.getByTestId("app-info-sync-error")).toContainText("Gerrit 测试连接失败");
  await page.getByTestId("detail-tab-profile").click();
  await expect(page.getByTestId("app-info-sync-error")).toBeVisible();
  expect(errors).toEqual([]);
});

test("Owner saves types and manual content separately; another Owner has read-only access", async ({ page }) => {
  await login(page, "app_owner");
  const basic = page.getByTestId("basic-info-section");
  await basic.getByRole("button", { name: "✎ 修改" }).click();
  await expect(basic.getByLabel("官方名称", { exact: true })).toBeDisabled();
  await expect(basic.getByLabel("Owner", { exact: true })).toBeDisabled();
  await basic.getByRole("combobox", { name: "类型", exact: true }).selectOption("ai4sci");
  await basic.getByRole("combobox", { name: "App 类型", exact: true }).selectOption("蛋白质与生物分子");
  await basic.getByRole("button", { name: "保存", exact: true }).click();
  await expect(basic.getByRole("combobox", { name: "类型", exact: true })).toBeDisabled();
  await page.reload();
  await page.getByTestId("app-row-info-check").click();
  await expect(basic.getByRole("combobox", { name: "App 类型", exact: true })).toHaveValue("蛋白质与生物分子");
  await page.getByTestId("detail-tab-profile").click();
  await page.getByRole("button", { name: "✎ 修改" }).click();
  await page.getByLabel("官方 URL", { exact: true }).fill("https://updated.example.test");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByLabel("官方 URL", { exact: true })).toBeDisabled();
  const state = await (await page.request.get(base + "/api/state")).json();
  const app = state.apps.find((item: { id: string }) => item.id === "info-check");
  expect(app.doc_target).toBe("ai4sci");
  expect(app.type).toBe("蛋白质与生物分子");
  expect(app.official_url).toBe("https://updated.example.test");
  const denied = await page.request.post(base + "/api/apps/profile", {
    data: { app_id: "info-check", fields: { official_name: "Forbidden name" } },
  });
  expect(denied.status()).toBe(403);
  await page.request.post(base + "/api/logout");
  await login(page, "app_other");
  // info-error belongs only to app_owner, even after RM adds app_other to info-check.
  await page.getByTestId("own-only-checkbox").uncheck();
  await page.getByTestId("app-row-info-error").click();
  await expect(page.getByTestId("basic-info-section").getByRole("button", { name: "✎ 修改" })).toHaveCount(0);
  await expect(page.getByTestId("app-info-sync")).toHaveCount(0);
  const syncDenied = await page.request.post(base + "/api/app-info/sync", { data: { app_id: "info-error", force: true } });
  expect(syncDenied.status()).toBe(403);
});
