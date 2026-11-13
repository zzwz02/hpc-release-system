import { test, expect } from "@playwright/test";

const base = process.env.IMAGE_PUBLICATIONS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup --image-publications and isolated Vite");
const image = "harbor.sh.mxcr.io/hpc-release/published-image-maca@sha256:" + "a".repeat(64);

for (const username of ["app_owner", "app_qa", "app_rm"]) {
  test(`${username} selects the publication for a digest and reads the exact local manifest`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    await page.goto(base + "/lookup");
    await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
    const input = page.getByRole("textbox", { name: "完整镜像名", exact: true });
    await input.fill(image);
    const response = page.waitForResponse(r => r.url().includes("/api/lookup/image?"));
    await page.getByTestId("lookup-search").click();
    const returned = await response;
    expect(returned.ok()).toBeTruthy();
    const data = await returned.json();
    expect(data.source).toBe("publications");
    expect(data.publications.map((record: { release: { name: string } }) => record.release.name)).toEqual(["image-publication-2", "image-publication-1"]);
    const result = page.getByTestId("lookup-publication-result");
    await expect(result.getByRole("heading")).toHaveText("Published Image 2.0");
    await expect(result.getByTestId("publication-qa")).toContainText("original-QA-2");
    await expect(result.getByTestId("test-scope-table").locator("tbody tr")).toHaveCount(10);
    await expect(result.getByTestId("delivery-images").locator("tbody tr")).toHaveCount(6);
    const selector = result.getByRole("combobox", { name: "选择发布记录" });
    await expect(selector.locator("option")).toHaveCount(2);
    await selector.selectOption(data.publications[1].key);
    await expect(result.getByRole("heading")).toHaveText("Published Image 1.0");
    await expect(result.getByTestId("publication-qa")).toContainText("original-QA-1");
    await expect(result.getByTestId("publication-excluded-tests")).toContainText("extra");
    await expect(result.getByTestId("test-scope-table").locator("tbody tr")).toHaveCount(5);
    await expect(result.getByTestId("test-scope-table")).not.toContainText("extra-original");
    await expect(result.getByTestId("test-scope-table")).toContainText("ARM");
    await expect(result.getByTestId("test-scope-table")).toContainText("ubuntu22.04");
    for (const command of data.publications[1].test_manifest.commands) {
      await expect(result.getByText(command.command, { exact: true })).toBeVisible();
    }
    const sourceLink = result.getByTestId("publication-app-info-link");
    const sourceUrl = new URL((await sourceLink.getAttribute("href"))!, base);
    expect(sourceUrl.searchParams.get("publication_key")).toBe(data.publications[1].key);
    const source = await page.request.get(sourceUrl.toString());
    expect(source.ok()).toBeTruthy();
    expect((await source.json()).app_version).toBe("1.0");
    const opened = page.context().waitForEvent("page");
    await sourceLink.click();
    const sourcePage = await opened;
    await sourcePage.waitForLoadState();
    await expect(sourcePage.locator("body")).toContainText("run-original-1");
    await sourcePage.close();
    await selector.selectOption(data.publications[0].key);
    await expect(result.getByTestId("publication-qa")).toContainText("original-QA-2");
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(selector).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/image-publications-${username}.png`, fullPage: true });
    // A subsequent no-record query must clear the prior publication and show fallback provenance.
    await input.fill("harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32));
    await input.press("Enter");
    await expect(result).toHaveCount(0);
    const historical = page.getByTestId("lookup-image-result");
    await expect(historical.getByTestId("lookup-image-source")).toContainText("根据历史配置生成");
    await input.fill(image.replace("a".repeat(64), "f".repeat(64)).replace("published-image", "abacus"));
    await input.press("Enter");
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(historical).toHaveCount(0);
    await expect(result).toHaveCount(0);
  });
}
