import { expect, test } from "@playwright/test";
import type { FailureRecordDetail } from "../src/features/cicdAgent/cicdAgentApi";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033",
  proxy: {server: "http://127.0.0.1:3576", bypass: "*"} });

for (const width of [1366, 1920]) {
  test(`test case summary and ECC details at ${width}`, async ({ page }, info) => {
    await page.setViewportSize({width, height: 950});
    const record: FailureRecordDetail = {id: 99001, job_name: "Hpc_DeePMDKit_Test", build_number: 2146,
      official_name: "DeePMD-kit", app_version: "v3.1.1", record_source: "test_case_result",
      normalized_stage: "test case results", owner_role: "Code Owner", owner_account: "alice", code_owner: "alice",
      responsibility_status: "ai_suggested", maca_project: "maca", chip: "c500", maca_version: "20261103-883",
      reason_summary: "ECC ON：通过 9/17，失败 1，超时 1，中止 6；ECC OFF：通过 15/17，失败 2",
      test_summary: {total: 34, counts: {}, expected_ecc_states: ["ON", "OFF"], reported_ecc_states: ["ON", "OFF"],
        groups: ["ON", "OFF"].map(ecc_state => ({ecc_state, chip: "c500", arch: "amd64", platform: "ub24_x86", image_alias: "default",
          maca_version: "20261103-883", total: 17, counts: {Passed: 9, Failed: 1, Timeout: 1, Aborted: 6, CANCEL: 0},
          cases: [{name: "DeePMD-kit.water-se_e2_a-train_kernel_selection", status: "Timeout", duration: "00:45:00",
            image: "mxcr.io/hpc-master/deepmd-kit-maca:v3.1.1-20261103-883-torch2.10-py312-ubuntu24.04-amd64", image_digest: ""}],
        }))}};
    const errors: string[] = [], writes: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") writes.push(path);
      let data: unknown = {};
      if (path === "/api/me") data = {user: {username: "alice", role: "RM"}};
      else if (path === "/api/state") data = {apps: [], releases: [], user_display_names: {alice: "Alice"}};
      else if (path.endsWith("/preferences")) data = {enabled: true};
      else if (path.endsWith("/filter-options")) data = {owners: [], owner_accounts: []};
      else if (path.endsWith("/summary")) data = {total_records: 1, groups: []};
      else if (path.endsWith("/99001")) data = record;
      else if (path.endsWith("/failures")) data = {records: [record], total: 1, page: 1, page_size: 20, total_pages: 1};
      await route.fulfill({json: data});
    });
    await page.goto("/jenkins-failures");
    await expect(page.getByRole("row").filter({hasText: "Hpc_DeePMDKit_Test"})).toHaveCount(1);
    await page.getByRole("button", {name: "查看详情"}).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("测试结果规则分配")).toBeVisible();
    await dialog.locator("summary").first().click();
    await dialog.locator(".cicd-case-results").scrollIntoViewIfNeeded();
    await expect(dialog.getByText("DeePMD-kit.water-se_e2_a-train_kernel_selection").first()).toBeVisible();
    const overflowing = await dialog.locator(".cicd-case-results, .cicd-case-table, .cicd-case-table td").evaluateAll(elements =>
      elements.filter(element => element.scrollWidth > element.clientWidth + 2).length);
    expect(overflowing).toBe(0);
    await page.screenshot({path: info.outputPath(`case-results-${width}.png`), fullPage: true});
    expect(errors).toEqual([]); expect(writes).toEqual([]);
  });
}
