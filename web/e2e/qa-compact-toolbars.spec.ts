import { test, expect, type Locator } from "@playwright/test";

const base = process.env.QA_COMPACT_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup --qa-commands and isolated Vite");

async function sameLine(items: Locator[]) {
  const bounds = await Promise.all(items.map(item => item.boundingBox()));
  expect(bounds.every(Boolean)).toBe(true);
  const centers = bounds.map(box => box!.y + box!.height / 2);
  expect(Math.max(...centers) - Math.min(...centers)).toBeLessThan(3);
}

test("latest without any matching images displays and exports placeholder commands", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/qa?tab=test");
  await page.getByLabel("选择 release").selectOption({ label: "3.10.2" });
  const response = page.waitForResponse(r => r.url().includes("/api/qa/test-commands?"));
  await page.getByRole("button", { name: "查询测试命令", exact: true }).click();
  const resolved = await response;
  expect(resolved.status(), await resolved.text()).toBe(200);
  expect(new URL(resolved.url()).searchParams.get("hpc_version")).toBe("latest");
  const result = await resolved.json();
  expect(result.hpc_versions).toEqual([]);
  expect(result.errors).toEqual([]);
  expect(result.rows.length).toBeGreaterThan(0);
  expect(result.rows.every((row: { hpc_version: string; website_docker_cmd: string }) =>
    row.hpc_version === "" && row.website_docker_cmd.includes("<hpc_version>"))).toBeTruthy();
  await expect(page.getByText("未找到可用镜像，已显示占位符命令", { exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: /docker run.*<hpc_version>/ }).first()).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV" }).click();
  const csv = await download;
  expect(csv.suggestedFilename()).toBe("test_commands_latest.csv");
  const stream = await csv.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const content = Buffer.concat(chunks).toString("utf8");
  for (const row of result.rows) expect(content).toContain(row.website_docker_cmd.replaceAll('"', '""'));
  await page.screenshot({ path: "/tmp/qa-latest-placeholder-commands.png", fullPage: true });
});

test("QA queries latest without a version input and community toolbar submits its version", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/qa?tab=test");
  await page.getByLabel("选择 release").selectOption({ label: "3.10.1" });
  const query = page.locator(".qa-test-command-query");
  await expect(query.getByLabel("hpc_version", { exact: true })).toHaveCount(0);
  const submit = query.getByRole("button", { name: "查询测试命令", exact: true });
  const response = page.waitForResponse(r => r.url().includes("/api/qa/test-commands?"));
  await submit.click();
  const resolved = await response;
  expect(resolved.ok(), await resolved.text()).toBeTruthy();
  expect(new URL(resolved.url()).searchParams.get("hpc_version")).toBe("latest");
  await expect(query.getByRole("status")).toContainText("3.10.1.2");
  await sameLine([submit, query.getByRole("status")]);
  await page.screenshot({ path: "/tmp/qa-compact-test-commands.png", fullPage: true });

  await page.goto(base + "/qa?tab=community");
  const toolbar = page.locator(".community-artifact-toolbar");
  const sdk = toolbar.getByLabel("SDK 版本");
  const fetch = toolbar.getByRole("button", { name: "获取最新产物" });
  const download = toolbar.getByRole("button", { name: "下载全部" });
  await sameLine([toolbar.getByText("SDK 版本"), sdk, fetch, download]);
  // Keep the external artifact service out of this isolated browser check.
  await page.route("**/api/qa/community-artifacts", route => route.fulfill({
    contentType: "application/json", body: JSON.stringify({ sdkversion: "3.10.1.2", category: "ai4s", files: [] }),
  }));
  await sdk.fill("3.10.1.2");
  const request = page.waitForRequest(r => r.url().endsWith("/api/qa/community-artifacts") && r.method() === "POST");
  await fetch.click();
  expect((await request).postDataJSON()).toEqual({ sdkversion: "3.10.1.2", category: "ai4s" });
  await expect(fetch).toBeEnabled();
  await page.screenshot({ path: "/tmp/qa-compact-community.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(sdk).toBeVisible();
  await expect(fetch).toBeVisible();
  expect(await toolbar.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
});
