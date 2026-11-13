import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

const base = process.env.LATEST_RUNNING_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated tests.run_app_info_site --latest-running and Vite");

for (const username of ["app_rm", "app_qa", "app_owner"]) {
  test(`${username} uses one compatibility entry for all running Apps and exports filtered platforms`, async ({ page }) => {
    expect((await page.request.post(base + "/api/login", { data: { username, password: "test-password" } })).ok()).toBeTruthy();
    await page.goto(base + "/lookup");
    await expect(page.getByRole("button", { name: "兼容性测试基线", exact: true })).toHaveCount(0);
    await page.getByRole("link", { name: "兼容性验证", exact: true }).click();
    const pane = page.getByTestId("compatibility-pane");
    await expect(pane.getByTestId("compat-app-amber")).toContainText("amber-3.9");
    await expect(pane.getByTestId("compat-app-amber")).not.toContainText("amber-3.10");
    await expect(pane.getByTestId("compat-app-hpl")).toContainText("hpl-3.10");
    await expect(pane.getByTestId("compat-app-stopped")).toHaveCount(0);
    const pending = pane.getByTestId("compatibility-pending");
    await pending.getByRole("button", { name: "展开 info-check 的镜像和测试命令", exact: true }).click();
    await expect(pending.getByTestId("compat-detail-info-check")).toContainText("尚无成功发布记录");
    await expect(pane.getByTestId("compat-detail-amber")).toHaveCount(0);
    await pane.getByRole("button", { name: "展开 HPL 的镜像和测试命令", exact: true }).click();
    const detail = pane.getByTestId("compat-detail-hpl");
    await expect(detail).toContainText("ubuntu22.04");
    await expect(detail).toContainText("centos9");
    await expect(detail).toContainText("ARM");
    await expect(detail).toContainText("x86");
    await expect(detail).toContainText("@sha256:");
    await expect(detail).toContainText("docker run");
    const ubuntu22 = detail.locator("tbody tr").filter({ hasText: "ubuntu22.04" });
    await expect(ubuntu22.first().locator("td").nth(2)).toHaveText("C600");
    const arm = detail.locator("tbody tr").filter({ hasText: "ARM" });
    await expect(arm.first().locator("td").nth(2)).toHaveText("C500");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("AMBER");
    await expect(pane.getByTestId("compat-app-hpl")).toHaveCount(0);
    const download = page.waitForEvent("download");
    await pane.getByRole("button", { name: "导出当前测试清单 CSV" }).click();
    const csv = await readFile((await (await download).path())!, "utf8");
    for (const value of ["HPC version", "支持芯片", "C500", "C600", "C588", "amber-3.9", "ARM", "x86", "@sha256:"]) expect(csv).toContain(value);
    expect(csv).not.toContain("amber-3.10");
    expect(csv).not.toContain("hpl-3.10");
    await pane.getByRole("button", { name: "清除筛选" }).click();
    await pane.getByRole("combobox", { name: "按GPU 芯片筛选" }).selectOption("C600");
    await pane.getByRole("button", { name: "展开 AMBER 的镜像和测试命令", exact: true }).click();
    const amberDetail = pane.getByTestId("compat-detail-amber");
    await expect(amberDetail).toContainText("ubuntu22.04");
    await expect(amberDetail).not.toContainText("ubuntu24.04");
    await pane.getByRole("textbox", { name: "筛选兼容性验证" }).fill("does-not-exist");
    await expect(pane).toContainText("没有匹配的记录。");
    await pane.getByRole("button", { name: "清除筛选" }).click();
    await pane.getByRole("button", { name: "刷新当前页" }).click();
    await expect(pane.getByTestId("compat-app-hpl")).toContainText("hpl-3.10");
    await page.setViewportSize({ width: 2560, height: 1000 });
    await pane.getByRole("button", { name: "展开 HPL 的镜像和测试命令", exact: true }).click();
    const commandBounds = await detail.getByRole("columnheader", { name: "测试命令", exact: true }).boundingBox();
    expect(commandBounds!.width).toBeGreaterThanOrEqual(400);
    await page.screenshot({ path: `/tmp/compatibility-${username}.png`, fullPage: true });
  });
}
