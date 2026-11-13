import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = process.env.DEPENDENCIES_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --dependencies and isolated Vite");

for (const username of ["app_owner", "app_qa"]) {
  test(`${username} filters the merged inventory using release dependencies and keeps unknown scope visible`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", {
      data: { username, password: "test-password" },
    })).ok()).toBeTruthy();
    await page.goto(base + "/dependencies");
    const pane = page.getByTestId("compatibility-pane");
    let selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector.locator("option")).toHaveText([
      "全部组件", "sdk（1）", "torch 2.4 + py310（2）", "torch 2.8 + py312（2）",
    ]);
    // The shared layout initializes cycle state; compatibility controls must not reload it.
    await page.waitForLoadState("networkidle");
    const stateRequests: string[] = [];
    page.on("request", (request) => { if (request.url().includes("/api/state")) stateRequests.push(request.url()); });
    const selected = "torch 2.8 + py312";
    await selector.selectOption(selected);
    const published = pane.getByTestId("compat-app-runtime-published");
    const toggle = published.getByRole("button");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(published).toContainText("runtime-published-cycle");
    await expect(published).toContainText("5 个镜像");
    await expect(published).toContainText("1 个测试项 / 5 条命令");
    await toggle.click();
    const detail = pane.getByTestId("compat-detail-runtime-published");
    await expect(detail.getByRole("columnheader", { name: "commit id", exact: true })).toBeVisible();
    await expect(detail.getByTestId("compat-app-info-commit").first()).toHaveText("0123456789ab");
    await expect(detail.getByTestId("compat-app-info-commit").first()).toHaveAttribute("title", "0123456789abcdef0123456789abcdef01234567");
    await expect(detail.getByTestId("compat-command")).toHaveCount(6);
    await expect(detail.getByTestId("compat-command").filter({ hasText: "@sha256:" })).toHaveCount(4);
    await expect(detail).toContainText("缺少对应交付镜像，命令为模板");
    await expect(detail).toContainText("无对应启用测试命令");
    await expect(detail).toContainText("centos9");
    await expect(detail).toContainText("ARM");
    await expect(detail).toContainText("runtime-published-maca-dbg@sha256:");
    await expect(detail).toContainText("-v /pde_hpc/dataset:/hpc_dataset:ro");
    await toggle.click();
    await expect(detail).toHaveCount(0);
    await toggle.press("Enter");
    await expect(detail).toBeVisible();
    await selector.selectOption("sdk");
    await expect(pane.getByTestId("compatibility-pending")).toContainText("runtime-unknown");
    const exported = page.waitForEvent("download");
    await pane.getByRole("button", { name: "导出当前测试清单 CSV" }).click();
    const csv = await readFile((await (await exported).path())!, "utf8");
    for (const value of ["runtime-published", "runtime-unknown", "范围待确认", selected, "sanity", "centos9", "命令为模板"]) expect(csv).toContain(value);
    for (const value of ["runtime-stopped", "development-sdk", "py313"]) expect(csv).not.toContain(value);
    expect(csv).toContain("app_info commit id");
    expect(csv).toContain("0123456789abcdef0123456789abcdef01234567");
    expect(csv).not.toContain("development-runtime");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("0123456789abcdef0123456789abcdef01234567");
    await expect(published).toBeVisible();
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("development-runtime");
    await expect(pane).toContainText("没有匹配的记录。");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("");
    // No-publication Apps are retained for scope confirmation, with no development commands.
    expect(csv).toContain("runtime-unpublished");
    await selector.selectOption(selected);
    await published.getByRole("button").click();
    for (const [label, value] of [["CPU 架构", "arm64"], ["OS", "ubuntu20.04"], ["GPU 芯片", "C500"], ["Python 配置", "py312"], ["PyTorch 配置", "torch2.8"]]) {
      await pane.getByRole("combobox", { name: `按${label}筛选`, exact: true }).selectOption(value);
    }
    await expect(detail.locator("tbody tr")).toHaveCount(1);
    await expect(detail).toContainText("ARM");
    await expect(detail).not.toContainText("centos9");
    const filteredExport = page.waitForEvent("download");
    await pane.getByRole("button", { name: "导出当前测试清单 CSV" }).click();
    const filteredCsv = await readFile((await (await filteredExport).path())!, "utf8");
    expect(filteredCsv).toContain("ARM");
    expect(filteredCsv).not.toContain("x86");
    expect(filteredCsv).not.toContain("centos9");

    await page.getByRole("button", { name: "开发态依赖", exact: true }).click();
    await expect(pane).toHaveCount(0);
    await expect(page.getByRole("button", { name: "导出当前测试清单 CSV" })).toHaveCount(0);
    selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector).toHaveValue("");
    await expect(selector.locator("option")).toContainText(["全部组件", "development-sdk（1）"]);
    await selector.selectOption("torch 2.10 + py313");
    const development = page.getByTestId("dependency-group-torch 2.10 + py313");
    await development.locator("summary").click();
    await expect(development).toContainText("runtime-published");
    await expect(development).toContainText("development-sdk");
    await expect(development).not.toContainText("docker run");
    await selector.selectOption(selected);
    const group = page.getByTestId(`dependency-group-${selected}`);
    await group.locator("summary").click();
    await expect(group).toContainText("runtime-unpublished");
    await expect(group).not.toContainText("runtime-published-cycle");
    await page.getByRole("button", { name: "兼容性验证", exact: true }).click();
    await expect(page.getByRole("combobox", { name: "按组件筛选" })).toHaveValue("");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await page.setViewportSize({ width: 390, height: 844 });
    await toggle.press("Space");
    await expect(detail).toBeVisible();
    expect(stateRequests).toEqual([]);
    await page.screenshot({ path: `/tmp/dependencies-${username}.png`, fullPage: true });
  });
}

test("refresh failure hides the previous inventory and disables export", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/dependencies");
  const pane = page.getByTestId("compatibility-pane");
  await expect(pane.getByTestId("compat-app-runtime-published")).toBeVisible();
  await page.route("**/api/lookup/latest-running", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "读取发布清单失败" }) }));
  await pane.getByRole("button", { name: "刷新当前页" }).click();
  await expect(pane.getByRole("alert")).toBeVisible();
  await expect(pane.getByTestId("compat-app-runtime-published")).toHaveCount(0);
  await expect(pane.getByRole("button", { name: "导出当前测试清单 CSV" })).toBeDisabled();
});
