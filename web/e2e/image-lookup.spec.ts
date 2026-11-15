import { test, expect } from "@playwright/test";

const base = process.env.IMAGE_LOOKUP_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup and isolated Vite");
const image = "harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32);

test("image digest resolves historical app_info and assembles commands with the exact immutable image", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_owner", password: "test-password" } });
  await page.goto(base + "/lookup");
  await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
  const input = page.getByRole("textbox", { name: "完整镜像名", exact: true });
  await input.fill(`docker pull ${image}`);
  await expect(input).toHaveValue(image);
  const request = page.waitForResponse(r => r.url().endsWith("/api/lookup/images"));
  await page.getByTestId("lookup-search").click();
  const response = await request;
  expect(response.ok()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({ images: [image] });
  const batch = await response.json();
  const data = batch.images[0];
  expect(data.commit_id).toBe("bb46c60");
  expect(data.app_version).toBe("3.9.0.27");
  expect(batch.rows).toHaveLength(2);
  const result = page.getByTestId("lookup-image-result");
  await expect(result.getByTestId("lookup-image-source")).toContainText("根据镜像历史配置实时生成");
  await expect(result.locator("summary")).toContainText("ABACUS 3.9.0.27");
  await result.locator("summary").click();
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
  for (const command of batch.rows) {
    expect(command.docker_cmd).toContain(image);
    expect(command.docker_cmd).toContain("-v /pde_hpc/dataset:/hpc_dataset:ro");
    await expect(result.getByText(command.docker_cmd, { exact: true })).toBeVisible();
  }
  await expect(result.getByText("3.9.0.27-bb46c60-ubuntu22.04-amd64-3012", { exact: true })).toBeVisible();
  await page.screenshot({ path: "/tmp/image-lookup-result.png", fullPage: true });

  for (const [digest, message] of [["f", "没有有效 commit tag"], ["e", "未找到该镜像"], ["d", "Harbor 查询失败"]]) {
    await input.fill(image.replace("8d".repeat(32), digest.repeat(64)));
    await page.getByTestId("lookup-search").click();
    await expect(page.getByRole("alert")).toContainText(message);
    await expect(result).toHaveCount(0);
  }
  await page.getByRole("button", { name: "发布app列表", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByTestId("published-apps-pane")).toBeVisible();
  await expect(page.getByRole("button", { name: "按 App", exact: true })).toHaveCount(0);
});

test("QA can query images and denied roles cannot call the image endpoint", async ({ page }) => {
  await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } });
  await page.goto(base + "/lookup");
  await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
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

test("HPCC and MACA resolve the same App while preserving actual image and release/dbg scope", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  const hpcc = image.replace("abacus-maca", "abacus-hpcc");
  const debug = image.replace("abacus-maca", "abacus-hpcc-dbg");
  await page.goto(base + "/lookup");
  await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
  await page.getByLabel("完整镜像名", { exact: true }).fill([hpcc, debug, image].join("\n"));
  const returned = page.waitForResponse(r => r.url().endsWith("/api/lookup/images"));
  await page.getByTestId("lookup-search").click();
  const response = await returned;
  expect(response.ok()).toBeTruthy();
  const data = await response.json();
  expect(data.errors).toEqual([]);
  expect(data.duplicate_count).toBe(0);
  expect(data.images.map((source: { image: string }) => source.image)).toEqual([hpcc, debug, image]);
  expect(data.images.every((source: { app_name: string }) => source.app_name === "ABACUS")).toBe(true);
  expect(data.rows).toHaveLength(5);
  expect(data.rows.slice(0, 2).every((row: { docker_cmd: string }) => row.docker_cmd.includes(hpcc))).toBe(true);
  expect(data.rows[2].test_name).toBe("debug-sanity");
  expect(data.rows[2].docker_cmd).toContain(debug);
  expect(data.rows.slice(3).every((row: { docker_cmd: string }) => row.docker_cmd.includes(image))).toBe(true);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("table", { name: "测试命令", exact: true }).locator("tbody tr")).toHaveCount(5);
});
