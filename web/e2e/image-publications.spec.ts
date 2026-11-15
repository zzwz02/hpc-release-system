import { test, expect } from "@playwright/test";

const base = process.env.IMAGE_PUBLICATIONS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup --image-publications and isolated Vite");

for (const username of ["app_owner", "app_qa", "app_rm"]) {
  test(`${username} resolves a published digest live and expands its actual test configuration`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    const releases = await (await page.request.get(base + "/api/lookup/releases")).json();
    const release = releases.releases.find((item: { name: string }) => item.name === "image-publication-2");
    const published = await (await page.request.get(base + `/api/lookup/release?release_id=${release.id}`)).json();
    const image = published.apps[0].images.find((item: { os: string; arch: string; image_target: string }) => item.os === "ubuntu20.04" && item.arch === "amd64" && item.image_target === "release").reference;
    await page.goto(base + "/lookup");
    await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
    const input = page.getByRole("textbox", { name: "完整镜像名", exact: true });
    await input.fill(image);
    const response = page.waitForResponse(r => r.url().endsWith("/api/lookup/images"));
    await page.getByTestId("lookup-search").click();
    const returned = await response;
    expect(returned.ok()).toBeTruthy();
    const data = await returned.json();
    expect(data.errors).toEqual([]);
    expect(data.images[0].commit_id).toBe("2".repeat(40));
    expect(data.images[0].hpc_version).toBe("3.10.0.2");
    expect(data.rows.map((row: { test_name: string }) => row.test_name)).toEqual(["sanity", "extra"]);
    const result = page.getByTestId("lookup-image-result");
    await expect(result.locator("tbody tr")).toHaveCount(2);
    await expect(result.locator("tbody")).toContainText("run-original-2");
    await expect(result.locator("tbody")).toContainText("extra-original-2");
    await expect(result.getByRole("combobox", { name: "选择发布记录" })).toHaveCount(0);
    await result.locator("summary").click();
    const sourceLink = result.getByTestId("app-info-source-link");
    const sourceUrl = new URL((await sourceLink.getAttribute("href"))!, base);
    expect(sourceUrl.searchParams.has("publication_key")).toBe(false);
    expect(sourceUrl.searchParams.get("commit_id")).toBe("2".repeat(40));
    const source = await page.request.get(sourceUrl.toString());
    expect(source.ok()).toBeTruthy();
    expect((await source.json()).app_version).toBe("2.0");
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/image-publications-${username}.png`, fullPage: true });
    await input.fill("harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32));
    await page.getByTestId("lookup-search").click();
    await expect(result.locator("summary")).toContainText("ABACUS 3.9.0.27");
    await expect(result.locator("tbody")).not.toContainText("run-original-2");
    await input.fill("harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "f".repeat(64));
    await page.getByTestId("lookup-search").click();
    await expect(page.getByRole("alert")).toBeVisible();
    await expect(result).toHaveCount(0);
  });
}
