import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = process.env.DEPENDENCIES_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --dependencies and isolated Vite");

for (const username of ["app_owner", "app_qa"]) {
  test(`${username} filters paired runtime dependencies and exports published Apps`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", {
      data: { username, password: "test-password" },
    })).ok()).toBeTruthy();
    await page.goto(base + "/dependencies");
    const selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector.locator("option")).toHaveText([
      "全部组件", "sdk（2）", "torch 2.4 + py310（2）", "torch 2.8 + py312（2）",
    ]);
    await expect(page.locator(".dependency-group")).toHaveCount(3);
    await expect(page.locator(".dependency-group[open]")).toHaveCount(0);
    const selected = "torch 2.8 + py312";
    await selector.selectOption(selected);
    const panel = page.getByTestId(`dependency-group-${selected}`);
    const group = page.getByTestId(`component-${selected}`);
    await expect(group).not.toBeVisible();
    await panel.locator("summary").click();
    await expect(group).toBeVisible();
    await expect(group.locator("tbody tr")).toHaveCount(2);
    await expect(group).toContainText("runtime-published");
    await expect(group).toContainText("runtime-unpublished");
    await expect(page.getByTestId("dependencies-page")).not.toContainText("runtime-stopped");
    await expect(page.getByTestId("component-sdk")).toHaveCount(0);
    await expect(group.getByRole("columnheader", { name: "最新hpc release版本", exact: true })).toBeVisible();
    await expect(group.getByRole("columnheader", { name: "当前正式版", exact: true })).toHaveCount(0);
    const published = group.getByTestId("dependency-app-runtime-published");
    await expect(published.getByTestId("dependency-release")).toHaveText("runtime-published-cycle");
    await expect(published.locator(".delivery-image-item")).toHaveCount(5);
    await expect(published.getByTestId("dependency-docker-command")).toHaveCount(5);
    await expect(published.getByTestId("dependency-docker-command").filter({ hasText: "@sha256:" })).toHaveCount(4);
    await expect(published.getByText("未填写对应交付镜像，命令使用镜像模板。", { exact: true })).toHaveCount(1);
    await expect(published.getByTestId("delivery-images")).toContainText("centos9");
    await expect(published.getByTestId("delivery-images")).toContainText("ARM");
    await expect(published.getByTestId("dependency-test-commands")).toContainText("ubuntu22.04");
    await expect(published.getByTestId("dependency-test-commands")).toContainText("runtime-published-maca-dbg@sha256:");
    await expect(published.getByTestId("dependency-test-commands")).toContainText("-v /pde_hpc/dataset:/hpc_dataset:ro");

    const exported = page.waitForEvent("download");
    await page.getByTestId("compat-csv-link").click();
    const csv = await readFile((await (await exported).path())!, "utf8");
    expect(csv).toContain("runtime-published");
    expect(csv).toContain(selected);
    expect(csv).not.toContain("runtime-unpublished");
    expect(csv).not.toContain("runtime-stopped");
    expect(csv).toContain("sanity");
    expect(csv.split(/\r?\n/)[0]).toContain("最新hpc release版本,OS,CPU 架构,镜像类型");
    expect(csv.split(/\r?\n/)[0]).not.toContain(",版本,");
    expect(csv).toContain("centos9");
    expect(csv).toContain("镜像模板（未填写交付镜像）");

    await selector.selectOption("");
    const sdk = page.getByTestId("dependency-group-sdk");
    await expect(sdk).not.toHaveAttribute("open", "");
    await sdk.locator("summary").click();
    await expect(page.getByTestId("component-sdk").locator("tbody tr")).toHaveCount(2);
    await expect(page.getByRole("navigation", { name: "QA", exact: true })
      .getByRole("link", { name: "组件依赖", exact: true })).toHaveClass(/active/);
    await page.setViewportSize({ width: 390, height: 844 });
    await selector.selectOption(selected);
    // Returning to the same mounted group keeps the user's disclosure state.
    await expect(panel).toHaveAttribute("open", "");
    await panel.locator("summary").click();
    await expect(panel).not.toHaveAttribute("open", "");
    await panel.locator("summary").click();
    await expect(group).toBeVisible();
    await page.screenshot({ path: `/tmp/dependencies-${username}.png`, fullPage: true });
  });
}
