import { expect, test } from "@playwright/test";
import type { FailureRecordDetail } from "../src/features/cicdAgent/cicdAgentApi";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033" });

for (const width of [1366, 1920]) {
  test(`personal reminder subscription and email detail link at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 950 });
    await page.clock.setFixedTime(new Date("2026-10-26T04:00:00Z"));
    let enabled = true;
    const writes: unknown[] = [];
    const errors: string[] = [];
    const record: FailureRecordDetail = {
      id: 101, job_name: "Hpc_DiffDock_Release", build_number: 88, official_name: "DiffDock",
      normalized_stage: "build image", code_owner: "alice", owner_account: "alice", owner_role: "Code Owner",
      responsibility_status: "ai_suggested", reason_summary: "应用依赖版本冲突",
      created_at: "2026-10-25T10:00:00", responsibility_events: [],
    };
    page.on("pageerror", error => errors.push(error.message));
    await page.context().route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      let data: unknown = {};
      if (route.request().method() === "POST") {
        expect(path).toBe("/api/cicd-agent/failure-notifications/preferences");
        const body = route.request().postDataJSON();
        expect(Object.keys(body)).toEqual(["enabled"]);
        writes.push(body);
        enabled = body.enabled;
      }
      if (path === "/api/me") data = { user: { username: "alice", display_name: "Alice", role: "RM" } };
      else if (path === "/api/state") data = { apps: [], releases: [], user_display_names: { alice: "Alice" } };
      else if (path.endsWith("/failure-notifications/preferences")) data = { account: "alice", enabled, send_time: "09:45", timezone: "Asia/Shanghai" };
      else if (path.endsWith("/filter-options")) data = { owners: [{ code_owner: "alice" }], owner_accounts: ["alice"] };
      else if (path.endsWith("/summary")) data = { total_records: 1, groups: [] };
      else if (path.endsWith("/failures")) data = { records: [record], total: 1, page: 1, page_size: 20, total_pages: 1 };
      else if (path.endsWith("/failures/101")) data = record;
      else if (path.endsWith("/repro-tasks")) data = { tasks: [], enabled: true, worker_online: true };
      await route.fulfill({ json: data });
    });

    await page.goto("/jenkins-failures");
    const toggle = page.getByRole("checkbox", { name: /每日邮件提醒/ });
    await expect(toggle).toBeEnabled();
    await expect(toggle).toBeChecked();
    expect(writes).toEqual([]);
    await toggle.click();
    await expect(toggle).not.toBeChecked();
    await expect(toggle).toBeEnabled();
    await page.getByRole("checkbox", { name: "只看我的" }).uncheck();
    await page.getByRole("button", { name: "重置", exact: true }).click();
    await expect(toggle).not.toBeChecked();
    expect(writes).toEqual([{ enabled: false }]);
    await page.reload();
    await expect(toggle).not.toBeChecked();
    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(toggle).toBeEnabled();
    await page.reload();
    await expect(toggle).toBeChecked();
    const setting = page.locator(".cicd-agent-notification-setting");
    const layout = await setting.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const next = element.nextElementSibling!.getBoundingClientRect();
      return { overflow: element.scrollWidth > element.clientWidth, gap: next.x - rect.right, sameRow: next.top >= rect.top - 10 };
    });
    expect(layout.overflow).toBe(false);
    expect(layout.gap).toBeGreaterThanOrEqual(0);
    await page.screenshot({ path: info.outputPath(`notification-setting-${width}.png`), fullPage: true });

    await page.goto("/jenkins-failures?date_from=2026-10-25&date_to=2026-10-25&owner_account=alice&owner_role=Code%20Owner&failure_id=101");
    const dialog = page.getByRole("dialog", { name: "记录详情" });
    await expect(dialog).toContainText("Hpc_DiffDock_Release #88");
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-25");
    await expect(page.getByRole("checkbox", { name: "只看我的" })).not.toBeChecked();
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).toHaveCount(0);
    expect(writes).toEqual([{ enabled: false }, { enabled: true }]);
    expect(errors).toEqual([]);
  });
}
