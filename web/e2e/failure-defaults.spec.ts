import { expect, test } from "@playwright/test";
import type { FailureRecordDetail } from "../src/features/cicdAgent/cicdAgentApi";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033" });

for (const width of [1366, 1920]) {
  test(`personal seven-day defaults and RM review at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 950 });
    await page.clock.setFixedTime(new Date("2026-10-26T04:00:00Z"));
    const records: FailureRecordDetail[] = ["alice", "bob"].map((owner, index) => ({
      id: index + 1, job_name: `Hpc_${owner}_Release`, build_number: 100 + index,
      official_name: index ? "Other application" : "My application", normalized_stage: "build image",
      code_owner: owner, owner_account: owner, owner_role: "Code Owner",
      responsibility_status: index ? "disputed" : "ai_suggested",
      reason_summary: "应用依赖安装失败", pending_rm_review: Boolean(index),
      created_at: "2026-10-25T10:00:00",
    }));
    const queries: URLSearchParams[] = [];
    const errors: string[] = [];
    const writes: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.context().route("**/api/**", async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let data: unknown = {};
      if (route.request().method() !== "GET") writes.push(path);
      if (path === "/api/me") {
        data = { user: { username: "alice", display_name: "Alice", role: "RM" } };
      } else if (path === "/api/state") {
        data = { apps: [], releases: [], user_display_names: { alice: "Alice", bob: "Bob" } };
      } else if (path.endsWith("/filter-options")) {
        data = { owners: [{ code_owner: "alice" }, { code_owner: "bob" }], owner_accounts: ["alice", "bob"] };
      } else if (path.endsWith("/summary") || path.endsWith("/failures")) {
        const matches = records.filter(record => (
          (!url.searchParams.get("code_owner") || record.code_owner === url.searchParams.get("code_owner"))
          && (url.searchParams.get("pending_rm_review") !== "true" || record.pending_rm_review)
        ));
        if (path.endsWith("/summary")) data = { total_records: matches.length, groups: [] };
        else {
          queries.push(url.searchParams);
          data = { records: matches, total: matches.length, page: 1, page_size: 20, total_pages: 1, pending_rm_review_count: 1 };
        }
      }
      await route.fulfill({ json: data });
    });

    await page.goto("/jenkins-failures");
    const mine = page.getByRole("checkbox", { name: "只看我的" });
    const pending = page.getByRole("checkbox", { name: /仅看待 RM 裁定/ });
    const ownRow = page.getByRole("row").filter({ hasText: "Hpc_alice_Release" });
    const otherRow = page.getByRole("row").filter({ hasText: "Hpc_bob_Release" });
    await expect(mine).toBeChecked();
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-20");
    await expect(page.getByLabel("结束时间")).toHaveValue("2026-10-26");
    await expect(ownRow).toBeVisible();
    await expect(otherRow).toHaveCount(0);
    expect(queries.at(-1)?.get("code_owner")).toBe("alice");
    await page.screenshot({ path: info.outputPath(`personal-defaults-${width}.png`), fullPage: true });

    await mine.uncheck();
    await expect(otherRow).toBeVisible();
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(mine).not.toBeChecked();
    await expect(ownRow).toBeVisible();

    await page.getByRole("button", { name: "重置", exact: true }).click();
    await expect(mine).toBeChecked();
    await expect(otherRow).toHaveCount(0);
    await pending.check();
    await expect(mine).not.toBeChecked();
    await expect(otherRow).toBeVisible();
    await expect(ownRow).toHaveCount(0);
    expect(queries.at(-1)?.get("pending_rm_review")).toBe("true");
    expect(queries.at(-1)?.has("code_owner")).toBe(false);

    await page.goto("/jenkins-failures?date_from=2026-08-31&date_to=2026-09-06");
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-08-31");
    await expect(page.getByLabel("结束时间")).toHaveValue("2026-09-06");
    await expect(mine).toBeChecked();
    await expect(ownRow).toBeVisible();
    await page.getByRole("button", { name: "重置", exact: true }).click();
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-20");
    await expect(page.getByLabel("结束时间")).toHaveValue("2026-10-26");
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
}
