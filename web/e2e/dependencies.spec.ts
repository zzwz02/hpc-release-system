import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { parseCsvRows } from "../src/lib/csv";

const base = process.env.DEPENDENCIES_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --dependencies and isolated Vite");

for (const username of ["app_owner", "app_qa"]) {
  test(`${username} filters exact components and uses all published Apps for SDK and driver`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", {
      data: { username, password: "test-password" },
    })).ok()).toBeTruthy();
    await page.goto(base + "/dependencies");
    const pane = page.getByTestId("compatibility-pane");
    let selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector.locator("option")).toHaveText([
      "全部组件", "driver / SDK（2）", "torch 2.4 + py310（2）", "torch 2.8 + py312（2）",
    ]);
    await expect(pane).toContainText("成功发布过一次");
    await expect(pane.getByTestId("compat-app-runtime-unpublished")).toHaveCount(0);
    await expect(pane.getByTestId("compat-app-runtime-never")).toHaveCount(0);
    await expect(pane.getByTestId("compat-app-info-error")).toHaveCount(0);
    // The shared layout initializes cycle state; compatibility controls must not reload it.
    await page.waitForLoadState("networkidle");
    const stateRequests: string[] = [];
    page.on("request", (request) => { if (request.url().includes("/api/state")) stateRequests.push(request.url()); });
    const selected = "torch 2.8 + py312";
    await selector.selectOption(selected);
    const category = pane.getByTestId(`compatibility-group-${selected}`);
    await expect(category.locator("summary")).toContainText("2 个 App");
    await expect(category).toHaveAttribute("open", "");
    await expect(pane.getByTestId("compatibility-pending")).toHaveCount(0);
    await expect(pane.getByRole("combobox", { name: "按Python 配置筛选" })).toHaveCount(0);
    await expect(pane.getByRole("combobox", { name: "按PyTorch 配置筛选" })).toHaveCount(0);
    const published = category.getByTestId("compat-app-runtime-published");
    await expect(pane.getByRole("columnheader", { name: "app_id", exact: true }).first()).toBeVisible();
    await expect(published.locator("td").nth(1)).toHaveText("runtime-published");
    const toggle = published.getByRole("button");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(published).toContainText("runtime-published-cycle");
    await expect(published).toContainText("4 个镜像");
    await expect(published).toContainText("1 个测试项 / 5 条命令");
    await toggle.click();
    const detail = category.getByTestId("compat-detail-runtime-published");
    await expect(detail.getByRole("columnheader", { name: "commit id", exact: true })).toBeVisible();
    await expect(detail.getByTestId("compat-app-info-commit").first()).toHaveText("abcdef1");
    await expect(detail.getByTestId("compat-app-info-commit").first()).toHaveAttribute("title", "abcdef1");
    await expect(detail.getByTestId("compat-command")).toHaveCount(6);
    await expect(detail.getByTestId("compat-command").filter({ hasText: "@sha256:" })).toHaveCount(4);
    await expect(detail.getByRole("columnheader", { name: "GPU", exact: true })).toBeVisible();
    const combinedGpu = detail.getByRole("cell", { name: "C500, C500X", exact: true });
    await expect(combinedGpu).toBeVisible();
    await expect(combinedGpu.locator("..")).toContainText("dbg");
    const c500x = detail.locator("tbody tr").filter({ has: page.locator("td:nth-child(3)", { hasText: /^C500X$/ }) });
    await expect(c500x).toHaveCount(1);
    await expect(c500x).toContainText("3.10.0.7 · fail · 原因：性能回退 8%");
    await expect(combinedGpu.locator("..")).toContainText("无匹配测试结果");
    await pane.getByRole("combobox", { name: "按GPU 芯片筛选", exact: true }).selectOption("C500X");
    await expect(detail.locator("tbody tr")).toHaveCount(2);
    await expect(detail.locator("tbody tr td:nth-child(3)")).toHaveText(["C500X", "C500X"]);
    await pane.getByRole("combobox", { name: "按GPU 芯片筛选", exact: true }).selectOption("");
    await expect(combinedGpu).toBeVisible();
    await detail.screenshot({ path: `/tmp/compatibility-gpu-merged-${username}.png` });
    await expect(detail).toContainText("note: 测试镜像与 QA report 交付镜像不同，仅作历史参考");
    await expect(detail).not.toContainText("原因：回归通过");
    await expect(detail).not.toContainText("QA command must not replace delivery command");
    await expect(detail).toContainText("缺少对应交付镜像，命令为模板");
    await expect(detail).toContainText("无对应启用测试命令");
    await expect(detail).toContainText("centos9");
    await expect(detail).toContainText("ARM");
    await expect(detail).toContainText("runtime-published-maca-dbg:1.0-abcdef1-torch2.8-py312-ubuntu20.04-amd64@sha256:");
    await expect(detail).toContainText("-v /pde_hpc/dataset:/hpc_dataset:ro");
    await toggle.click();
    await expect(detail).toHaveCount(0);
    await toggle.press("Enter");
    await expect(detail).toBeVisible();
    await selector.selectOption("driver-sdk");
    const sdk = pane.getByTestId("compatibility-group-driver-sdk");
    await expect(sdk).toHaveAttribute("open", "");
    await expect(pane.locator(".dependency-group")).toHaveCount(1);
    await expect(sdk.locator("summary")).toContainText("2 个 App");
    await pane.getByRole("button", { name: "展开 runtime-unknown 的镜像和测试命令", exact: true }).click();
    await expect(pane.getByTestId("compat-detail-runtime-unknown")).toContainText("根据历史配置生成（未在发布时封存）");
    const exported = page.waitForEvent("download");
    await pane.getByRole("button", { name: "导出当前测试清单 CSV" }).click();
    const csv = await readFile((await (await exported).path())!, "utf8");
    const apiCsv = await page.request.get(base + "/api/lookup/latest-running/export?component=driver-sdk");
    expect(apiCsv.ok(), await apiCsv.text()).toBeTruthy();
    expect(apiCsv.headers()["content-type"]).toContain("text/csv");
    expect(parseCsvRows(await apiCsv.text())).toEqual(parseCsvRows(csv));
    expect(parseCsvRows(csv)[0]).toContain("镜像配置");
    expect(parseCsvRows(csv).some(row => row.includes("C500, C500X"))).toBe(true);
    for (const value of ["runtime-published", "runtime-unknown", selected, "sanity", "centos9", "命令为模板"]) expect(csv).toContain(value);
    expect(csv).not.toContain("范围待确认");
    for (const value of ["runtime-stopped", "runtime-never", "never-registered-component", "info-error", "development-sdk", "py313"]) expect(csv).not.toContain(value);
    expect(csv).toContain("app_info commit id");
    expect(csv.split("\n")[0]).toContain("App,app_id,Owner");
    expect(csv).toContain("abcdef1");
    expect(csv).not.toContain("development-runtime");
    expect(csv).toContain("发布时封存");
    expect(csv).toContain("根据历史配置生成");
    expect(csv).toContain("3.10.0.7 · fail · 原因：性能回退 8%");
    expect(csv).toContain("note: 测试镜像与 QA report 交付镜像不同，仅作历史参考");
    expect(csv).not.toContain("原因：回归通过");
    expect(csv).not.toContain("QA command must not replace delivery command");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("abcdef1");
    await expect(sdk.getByTestId("compat-app-runtime-published")).toBeVisible();
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("development-runtime");
    await expect(pane).toContainText("没有匹配的记录。");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("");
    // Only Apps with a successful publication belong to the compatibility export.
    expect(csv).not.toContain("runtime-unpublished");
    await selector.selectOption(selected);
    await published.getByRole("button").click();
    for (const [label, value] of [["CPU 架构", "arm64"], ["OS", "ubuntu20.04"], ["GPU 芯片", "C500"]]) {
      await pane.getByRole("combobox", { name: `按${label}筛选`, exact: true }).selectOption(value);
    }
    await expect(detail.locator("tbody tr")).toHaveCount(1);
    await expect(detail).toContainText("ARM");
    await expect(detail).not.toContainText("centos9");
    const filteredExport = page.waitForEvent("download");
    await pane.getByRole("button", { name: "导出当前测试清单 CSV" }).click();
    const filteredCsv = await readFile((await (await filteredExport).path())!, "utf8");
    const apiFiltered = await page.request.get(base + "/api/lookup/latest-running/export?" + new URLSearchParams({
      component: selected, arch: "arm64", os: "ubuntu20.04", chip: "C500",
    }));
    expect(apiFiltered.ok(), await apiFiltered.text()).toBeTruthy();
    expect(parseCsvRows(await apiFiltered.text())).toEqual(parseCsvRows(filteredCsv));
    expect(filteredCsv).toContain("ARM");
    expect(filteredCsv).not.toContain("x86");
    expect(filteredCsv).not.toContain("centos9");

    await page.getByRole("button", { name: "开发态依赖", exact: true }).click();
    await expect(pane).toHaveCount(0);
    await expect(page.getByRole("button", { name: "导出当前测试清单 CSV" })).toHaveCount(0);
    selector = page.getByRole("combobox", { name: "按组件筛选" });
    await expect(selector.locator("option").filter({ hasText: "never-registered-component" })).toHaveCount(0);
    await expect(page.getByTestId("dependency-app-runtime-never")).toHaveCount(0);
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
    await category.locator("summary").click();
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
  const category = pane.getByTestId("compatibility-group-driver-sdk");
  await category.locator("summary").click();
  await expect(category.getByTestId("compat-app-runtime-published")).toBeVisible();
  await page.route("**/api/lookup/latest-running", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "读取发布清单失败" }) }));
  await pane.getByRole("button", { name: "刷新当前页" }).click();
  await expect(pane.getByRole("alert")).toBeVisible();
  await expect(pane.getByTestId("compat-app-runtime-published")).toHaveCount(0);
  await expect(pane.getByRole("button", { name: "导出当前测试清单 CSV" })).toBeDisabled();
});
