import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = process.env.DEPENDENCIES_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --dependencies and isolated Vite");

for (const username of ["app_owner", "app_qa"]) {
  test(`${username} keeps compatibility on official snapshots and separates development`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", {
      data: { username, password: "test-password" },
    })).ok()).toBeTruthy();
    await page.goto(base + "/dependencies");
    const selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector.locator("option")).toHaveText([
      "全部组件", "sdk（1）", "torch 2.4 + py310（1）", "torch 2.8 + py312（1）",
    ]);
    await expect(page.locator(".dependency-group[open]")).toHaveCount(0);
    const selected = "torch 2.8 + py312";
    await selector.selectOption(selected);
    const panel = page.getByTestId(`dependency-group-${selected}`);
    const group = page.getByTestId(`component-${selected}`);
    await expect(group).not.toBeVisible();
    await panel.locator("summary").click();
    await expect(group.locator("tbody tr")).toHaveCount(1);
    const published = group.getByTestId("dependency-app-runtime-published");
    const toggle = published.getByRole("button");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(published.getByTestId("dependency-release")).toHaveText("runtime-published-cycle");
    await expect(published.getByText("5 个镜像", { exact: true })).toBeVisible();
    await expect(published.getByText("5 条命令", { exact: true })).toBeVisible();
    await expect(published.getByTestId("delivery-images")).not.toBeVisible();
    await toggle.click();
    await expect(published.getByTestId("delivery-images")).toBeVisible();
    await expect(published.locator(".delivery-image-item")).toHaveCount(5);
    await expect(published.getByTestId("dependency-docker-command")).toHaveCount(5);
    await expect(published.getByTestId("dependency-docker-command").filter({ hasText: "@sha256:" })).toHaveCount(4);
    await expect(published.getByText("未填写对应交付镜像，命令使用镜像模板。", { exact: true })).toHaveCount(1);
    await expect(published.getByTestId("delivery-images")).toContainText("centos9");
    await expect(published.getByTestId("delivery-images")).toContainText("ARM");
    await expect(published.getByTestId("dependency-test-commands")).toContainText("runtime-published-maca-dbg@sha256:");
    await expect(published.getByTestId("dependency-test-commands")).toContainText("-v /pde_hpc/dataset:/hpc_dataset:ro");
    await toggle.click();
    await expect(published.getByTestId("dependency-test-commands")).not.toBeVisible();
    await toggle.press("Enter");
    await expect(published.getByTestId("dependency-test-commands")).toBeVisible();

    const exported = page.waitForEvent("download");
    await page.getByTestId("compat-csv-link").click();
    const csv = await readFile((await (await exported).path())!, "utf8");
    expect(csv).toContain("runtime-published");
    expect(csv).toContain(selected);
    expect(csv).not.toContain("runtime-unpublished");
    expect(csv).not.toContain("runtime-stopped");
    expect(csv).not.toContain("development-sdk");
    expect(csv).not.toContain("py313");
    expect(csv).toContain("sanity");
    expect(csv).toContain("centos9");
    expect(csv).toContain("镜像模板（未填写交付镜像）");

    await page.getByRole("button", { name: "开发态依赖", exact: true }).click();
    await expect(page.getByTestId("compat-csv-link")).toHaveCount(0);
    await expect(selector).toHaveValue("");
    await expect(selector.locator("option")).toContainText(["全部组件", "development-sdk（1）"]);
    await selector.selectOption("torch 2.10 + py313");
    const development = page.getByTestId("dependency-group-torch 2.10 + py313");
    await development.locator("summary").click();
    await expect(development).toContainText("runtime-published");
    await expect(development).toContainText("development-sdk");
    await expect(development.getByTestId("delivery-images")).toHaveCount(0);
    await expect(development.getByTestId("dependency-test-commands")).toHaveCount(0);
    await selector.selectOption("torch 2.8 + py312");
    await panel.locator("summary").click();
    await expect(group).toContainText("runtime-unpublished");
    await expect(group).not.toContainText("runtime-published-cycle");

    await page.getByRole("button", { name: "正式版兼容验证", exact: true }).click();
    await expect(selector).toHaveValue("");
    await expect(selector.locator("option")).not.toContainText(["development-sdk（1）"]);
    await selector.selectOption(selected);
    await panel.locator("summary").click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await page.setViewportSize({ width: 390, height: 844 });
    await toggle.press("Space");
    await expect(published.getByTestId("dependency-test-commands")).toBeVisible();
    await page.screenshot({ path: `/tmp/dependencies-${username}.png`, fullPage: true });
  });
}
