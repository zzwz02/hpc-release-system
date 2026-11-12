import { test, expect } from "@playwright/test";

const base = process.env.IMAGE_LOOKUP_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup and isolated Vite");
const image = "harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32);

test("image digest resolves historical app_info and assembles commands with the exact immutable image", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_owner", password: "test-password" } });
  await page.goto(base + "/lookup");
  const input = page.getByRole("textbox", { name: "完整镜像名", exact: true });
  await input.fill(`docker pull ${image}`);
  await expect(input).toHaveValue(image);
  const request = page.waitForResponse(r => r.url().includes("/api/lookup/image?"));
  await page.getByTestId("lookup-search").click();
  const response = await request;
  expect(response.ok()).toBeTruthy();
  expect(new URL(response.url()).searchParams.get("image")).toBe(image);
  const data = await response.json();
  expect(data.commit_id).toBe("bb46c60");
  expect(data.app_version).toBe("3.9.0.27");
  expect(data.test_commands).toHaveLength(2);
  const result = page.getByTestId("lookup-image-result");
  await expect(result.getByRole("heading")).toHaveText("abacus 3.9.0.27");
  const sourceLink = result.getByTestId("app-info-source-link");
  await expect(sourceLink).toContainText("bb46c60");
  await expect(sourceLink).toHaveAttribute("target", "_blank");
  const sourceUrl = new URL((await sourceLink.getAttribute("href"))!, base);
  expect(sourceUrl.pathname).toBe("/api/lookup/app-info");
  expect(sourceUrl.searchParams.get("image")).toBe(image);
  expect(sourceUrl.searchParams.get("commit_id")).toBe("bb46c60");
  const source = await page.request.get(sourceUrl.toString());
  expect(source.ok()).toBeTruthy();
  expect((await source.json()).app_version).toBe("3.9.0.27");
  expect(source.headers()["content-disposition"]).toBe('inline; filename="app_info.json"');
  const opened = page.context().waitForEvent("page");
  await sourceLink.click();
  const sourcePage = await opened;
  await sourcePage.waitForLoadState();
  await expect(sourcePage.locator("body")).toContainText("3.9.0.27");
  await sourcePage.close();
  await expect(result.locator("tbody tr")).toHaveCount(2);
  for (const command of data.test_commands) {
    expect(command.command).toContain(image);
    expect(command.command).toContain("-v /pde_hpc/dataset:/hpc_dataset:ro");
    await expect(result.getByText(command.command, { exact: true })).toBeVisible();
  }
  await result.locator("summary").click();
  await expect(result.getByText("3.9.0.27-bb46c60-ubuntu22.04-amd64-3012", { exact: true })).toBeVisible();
  await page.screenshot({ path: "/tmp/image-lookup-result.png", fullPage: true });

  for (const [digest, message] of [["f", "没有第二字段为 commit id"], ["e", "未找到该镜像"], ["d", "Harbor 查询失败"]]) {
    await input.fill(image.replace("8d".repeat(32), digest.repeat(64)));
    await input.press("Enter");
    await expect(page.getByRole("alert")).toContainText(message);
    await expect(result).toHaveCount(0);
  }
  await page.getByRole("button", { name: "按 App", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("combobox", { name: "选择 App", exact: true }).selectOption("info-check");
  await page.getByTestId("lookup-search").click();
  await expect(page.getByTestId("lookup-record").first()).toBeVisible();
});

test("QA can query images and denied roles cannot call the image endpoint", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } });
  await page.goto(base + "/lookup");
  await page.getByRole("textbox", { name: "完整镜像名", exact: true }).fill(image);
  await page.getByTestId("lookup-search").click();
  await expect(page.getByTestId("lookup-image-result")).toBeVisible();
  for (const username of ["app_guest", "app_spd"]) {
    await page.request.post(base + "/api/logout");
    await page.request.post(base + "/api/login", { data: { username, password: "test-password" } });
    expect((await page.request.get(base + `/api/lookup/image?image=${encodeURIComponent(image)}`)).status()).toBe(403);
    await page.goto(base + "/lookup");
    await expect(page.getByTestId("lookup-page")).toHaveCount(0);
  }
});
