import { test, expect } from "@playwright/test";

const base = process.env.QA_DELIVERY_DISPLAY_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated --platforms --qa-delivery-display backend and Vite");

test("QA report displays a real tag while comment saves preserve the original delivery reference", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  const state = await (await page.request.get(base + "/api/state")).json();
  const release = state.releases.find((row: { name: string }) => row.name === "browser-cycle-a");
  const repository = "harbor.sh.mxcr.io/hpc-release/hpcg-maca";
  const digest = "sha256:" + "a".repeat(64);
  const bare = repository + "@" + digest;
  const tagged = repository + ":0.9.0-abcdef1-ubuntu20.04-amd64@" + digest;
  const comment = `只修改 QA comment ${Date.now()}`;
  const reads: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (["/api/state", "/api/qa/report", "/api/qa/failure-summary", "/api/qa/delivery-images", "/api/app-audit"].includes(path)) reads.push(path);
  });
  await page.goto(base + "/qa");
  await expect(page.getByTestId("qa-failure-summary")).toBeVisible();
  expect(reads).toEqual(["/api/qa/report"]);
  await page.getByLabel("选择 release").selectOption(release.id);
  const card = page.locator('[data-qa-app="platform-check"]');
  await expect(card.locator(".qa-card-b > :first-child")).toContainText("QA 状态");
  await expect(card.getByTestId("qa-app-failures-platform-check")).toHaveJSProperty("open", false);
  const deliverySection = card.getByTestId("qa-app-delivery-platform-check");
  await expect(deliverySection).toHaveJSProperty("open", false);
  await expect(deliverySection.locator("summary")).toHaveText("交付镜像（1/3）");
  await expect(card.getByText(tagged, { exact: true })).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 900 });
  await card.screenshot({ path: "/tmp/qa-delivery-collapsed.png" });
  await deliverySection.locator("summary").click();
  await expect(card.getByText(tagged, { exact: true })).toBeVisible();
  const delivery = card.getByTestId("delivery-images");
  await expect(delivery.locator("tbody")).toHaveCount(3);
  await expect(delivery.getByText("未填写交付镜像", { exact: true })).toHaveCount(2);
  await expect(delivery.getByRole("cell", { name: "kylinv11", exact: true })).toBeVisible();
  await expect(delivery.getByRole("cell", { name: "ARM", exact: true })).toBeVisible();
  await expect(delivery.getByRole("columnheader")).toHaveCount(4);
  await expect(delivery.locator("tbody").first().locator("tr")).toHaveCount(2);
  await expect(delivery.locator(".delivery-reference-row td[colspan='4']").filter({ hasText: tagged })).toHaveCount(1);
  for (const width of [650, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => delivery.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    if (width === 650) await card.screenshot({ path: "/tmp/qa-delivery-second-row.png" });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("button", { name: /修改（填写 QA 结论/ }).first().click();
  await page.setViewportSize({ width: 390, height: 900 });
  await expect.poll(() => delivery.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await card.getByRole("textbox", { name: "HPCG QA comment", exact: true }).fill(comment);
  const saving = page.waitForResponse(response => response.url().endsWith("/api/qa/status-batch"));
  await page.getByRole("button", { name: "保存 QA 状态", exact: true }).first().click();
  const saved = await saving;
  expect(saved.ok(), await saved.text()).toBeTruthy();
  const item = saved.request().postDataJSON().items.find((row: { app_id: string }) => row.app_id === "platform-check");
  expect(item).not.toHaveProperty("deliverable_images");
  const readback = await (await page.request.get(base + `/api/state?release_id=${release.id}`)).json();
  const entry = readback.release.entries.find((row: { app_id: string }) => row.app_id === "platform-check");
  expect(entry.qa_note).toBe(comment);
  expect(entry.deliverable_images[0].reference).toBe(bare);
  await page.reload();
  await page.getByLabel("选择 release").selectOption(release.id);
  await expect(deliverySection).toHaveJSProperty("open", false);
  await deliverySection.locator("summary").click();
  await expect(card.getByText(tagged, { exact: true })).toBeVisible();
  await page.route("**/api/qa/report?*", async route => {
    const response = await route.fetch();
    const payload = await response.json();
    await route.fulfill({ response, json: { ...payload, qa_report: { ...payload.qa_report,
      delivery_images: null, errors: { delivery_images: "Harbor 读取失败" } } } });
  });
  await page.getByRole("button", { name: "刷新当前页" }).first().click();
  await expect(page.getByRole("alert")).toContainText("交付镜像 tag 读取失败");
  await expect(card.getByText(bare, { exact: true })).toBeVisible();
  await expect(card.getByText(tagged, { exact: true })).toHaveCount(0);
  await page.getByLabel("选择 release").selectOption({ label: "browser-cycle-b" });
  await expect(deliverySection).toHaveJSProperty("open", false);
  await expect(deliverySection.locator("summary")).toHaveText("交付镜像（0/3）");
  await deliverySection.locator("summary").click();
  await expect(card.getByText(bare, { exact: true })).toHaveCount(0);
  await expect(card.getByText(tagged, { exact: true })).toHaveCount(0);
  await expect(delivery.getByText("未填写交付镜像", { exact: true })).toHaveCount(3);
});
