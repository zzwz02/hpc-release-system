import { test, expect, type Page } from "@playwright/test";

const base = process.env.APP_SIGNUP_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --signup and isolated Vite");

async function login(page: Page, username = "app_owner") {
  expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/apps");
  await expect(page.getByTestId("batch-signup-btn")).toBeVisible();
  const state = await (await page.request.get(base + "/api/state")).json();
  return Object.fromEntries(state.releases.map((release: { name: string; id: string }) => [release.name, release.id]));
}

async function entries(page: Page, releaseId: string) {
  const state = await (await page.request.get(base + `/api/state?release_id=${releaseId}`)).json();
  return state.release.entries as Array<{ app_id: string; target_version: string; excluded_tests: string[] }>;
}

test("Owner batch signup: own running Apps, environment, cancellation, version changes and saved badges", async ({ page }) => {
  const releases = await login(page);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.getByTestId("batch-signup-btn").click();
  const dialog = page.getByRole("dialog", { name: "批量报名", exact: true });
  const version = dialog.getByRole("combobox", { name: "报名版本" });
  await version.selectOption(releases["signup-open-a"]);
  await expect(dialog.getByTestId("signup-environment")).toContainText("driver-0");
  await expect(dialog.getByTestId("signup-environment")).toContainText("sdk-0");
  await expect(dialog.getByTestId("signup-environment")).toContainText("torch-0");
  await expect(dialog.getByRole("checkbox", { name: "报名 other-ready 2.0", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("checkbox", { name: "报名 own-stopped 2.0", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("checkbox", { name: "报名 own-missing", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("checkbox", { name: "报名 own-stopping 2.0", exact: true })).toBeDisabled();
  await expect(dialog.getByRole("checkbox", { name: "报名 own-ready-b 2.0", exact: true })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "报名 own-ready-a 2.0", exact: true }).check();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect((await entries(page, releases["signup-open-a"])).map(entry => entry.app_id)).toEqual(["own-ready-b"]);

  await page.getByTestId("batch-signup-btn").click();
  await version.selectOption(releases["signup-open-a"]);
  await dialog.getByRole("checkbox", { name: "报名 own-ready-a 2.0", exact: true }).check();
  await version.selectOption(releases["signup-open-b"]);
  await expect(dialog.getByTestId("signup-environment")).toContainText("driver-1");
  await expect(dialog.getByRole("checkbox", { name: "报名 own-ready-a 2.0", exact: true })).not.toBeChecked();
  await expect(dialog.getByTestId("signup-confirm")).toBeDisabled();
  for (const name of ["own-ready-a", "own-ready-b"]) await dialog.getByRole("checkbox", { name: `报名 ${name} 2.0`, exact: true }).check();
  await dialog.locator(".signup-dialog-body").evaluate(element => { element.scrollTop = 0; });
  await expect(dialog.getByTestId("signup-environment").getByText("torch-1", { exact: true })).toBeVisible();
  await page.screenshot({ path: "/tmp/app-signup-batch.png", fullPage: true });
  const response = page.waitForResponse(response => response.url().endsWith("/api/entries/signup-batch"));
  await dialog.getByTestId("signup-confirm").click();
  expect((await response).ok()).toBeTruthy();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "选择版本" })).toHaveValue(releases["signup-open-b"]);
  for (const name of ["own-ready-a", "own-ready-b"]) await expect(page.getByTestId(`app-row-${name}`)).toContainText("已报名");
  const saved = await entries(page, releases["signup-open-b"]);
  expect(saved.map(entry => entry.app_id).sort()).toEqual(["own-ready-a", "own-ready-b"]);
  expect(saved.every(entry => entry.target_version === "2.0" && entry.excluded_tests.length === 0)).toBeTruthy();
  expect(errors).toEqual([]);
});

test("batch signup rolls back when an App becomes ineligible after the dialog opens", async ({ page }) => {
  const releases = await login(page);
  await page.getByTestId("batch-signup-btn").click();
  const dialog = page.getByRole("dialog", { name: "批量报名", exact: true });
  await dialog.getByRole("combobox", { name: "报名版本" }).selectOption(releases["signup-open-b"]);
  for (const name of ["info-error 1.0", "own-race 2.0"]) await dialog.getByRole("checkbox", { name: `报名 ${name}`, exact: true }).check();
  const stop = await page.request.post(base + "/api/cicd/requests/submit", {
    data: { task_id: "own-race", request_type: "modify", payload: { status: { old: "running", new: "stopped" } }, source: "app_workbench" },
  });
  expect(stop.ok()).toBeTruthy();
  await dialog.getByTestId("signup-confirm").click();
  await expect(dialog.getByRole("alert")).toContainText("own-race");
  await expect(dialog.getByRole("alert")).toContainText("停止 CICD 申请");
  expect((await entries(page, releases["signup-open-b"])).some(entry => entry.app_id === "info-error")).toBe(false);
  await expect(dialog.getByRole("checkbox", { name: "报名 info-error 1.0", exact: true })).toBeChecked();
  await dialog.getByRole("checkbox", { name: "报名 own-race 2.0", exact: true }).uncheck();
  await dialog.getByTestId("signup-confirm").click();
  await expect(dialog).toHaveCount(0);
  expect((await entries(page, releases["signup-open-b"])).some(entry => entry.app_id === "info-error")).toBe(true);
});

test("single App signup in the title bar lets RM choose a cycle and sign up another Owner's App", async ({ page }) => {
  const releases = await login(page, "app_rm");
  await page.getByTestId("batch-signup-btn").click();
  await expect(page.getByRole("dialog")).toContainText("你名下暂无 CICD 运行中的 App");
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.getByTestId("app-row-other-ready").click();
  const header = page.getByTestId("app-detail-header");
  await header.getByTestId("app-signup-btn").click();
  const dialog = page.getByRole("dialog", { name: "报名", exact: true });
  await dialog.getByRole("combobox", { name: "报名版本" }).selectOption(releases["signup-open-a"]);
  await expect(dialog.getByTestId("signup-environment")).toContainText("sdk-0");
  await expect(dialog.getByTestId("signup-app-summary")).toContainText("other-ready 2.0");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(dialog.getByTestId("signup-confirm")).toBeInViewport();
  const bounds = await dialog.boundingBox();
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "/tmp/app-signup-single-mobile.png", fullPage: true });
  await dialog.getByTestId("signup-confirm").click();
  await expect(dialog).toHaveCount(0);
  expect((await entries(page, releases["signup-open-a"])).some(entry => entry.app_id === "other-ready")).toBe(true);
});
