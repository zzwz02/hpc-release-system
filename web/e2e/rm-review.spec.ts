import { test, expect } from "@playwright/test";
import type { FailureRecordDetail } from "../src/features/cicdAgent/cicdAgentApi";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033" });

for (const width of [1366, 1920]) {
  test(`RM filters, opens the correct evidence and resolves a report at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 950 });
    const states = ["none", "none", "pending_review", "running", "reviewed", "exception", "pending_review"] as const;
    const records: FailureRecordDetail[] = states.map((state, index) => ({
      id: index + 1, job_name: `Hpc_App${index + 1}_Release`, build_number: 1848,
      official_name: `APP ${index + 1}`, normalized_stage: "build image", owner_role: "DevOps",
      owner_account: "m00930", code_owner: "owner1", reason_summary: "系统源下载失败",
      responsibility_status: index === 1 || index === 6 ? "disputed" : "ai_suggested",
      responsibility_note: "请核对历史日志中的系统源错误", responsibility_events: [],
      repro_review_state: state, pending_rm_review: [1, 2, 6].includes(index),
      created_at: "2026-10-23T09:00:00",
    }));
    let task = { id: "b".repeat(32), failure_id: 3, status: "completed", requested_by: "owner1", reason: "核对源故障",
      created_at: "2026-10-23T10:00:00", resolved_by: null as string | null, stale: false, history: [],
      report: { outcome: "historical_log_confirmed", verified: false, source_commit: "", arch: "amd64",
        builds: [], patches: [], suggested_owner_account: "m00930",
        diagnosis: { summary: "历史日志确认系统源404", suggested_role: "DevOps", responsibility_reason: "Release 文件返回404",
          action_suggestion: "核查系统源", limitations: [], original_evidence: "Release file not found", reproduction_evidence: "" },
      },
    };
    const errors: string[] = [];
    const writes: string[] = [];
    const resolutions: unknown[] = [];
    const listQueries: URLSearchParams[] = [];
    page.on("pageerror", e => errors.push(e.message));
    await page.context().route("**/api/**", async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      let data: unknown = {};
      if (route.request().method() === "POST") {
        writes.push(path);
        if (path.endsWith("/responsibility-resolution")) {
          resolutions.push(route.request().postDataJSON());
          records[2] = { ...records[2], responsibility_status: "corrected", pending_rm_review: false, repro_review_state: "reviewed" };
          task.resolved_by = "rm";
          data = { record: records[2] };
        } else if (path.endsWith("/repro-tasks")) {
          task = { ...task, id: "c".repeat(32), status: "queued", resolved_by: null };
          records[2].repro_review_state = "running";
          data = task;
        }
      } else if (path === "/api/me") data = { user: { username: "rm", display_name: "RM", role: "RM" } };
      else if (path === "/api/state") data = { apps: [], releases: [], user_display_names: { m00930: "DevOps", owner1: "Owner" } };
      else if (path.endsWith("/filter-options")) data = { responsibility_statuses: ["ai_suggested", "disputed", "corrected"] };
      else if (path.endsWith("/summary")) data = { total_records: 7, groups: [] };
      else if (path.endsWith("/repro-tasks")) data = { tasks: [task], enabled: true, worker_online: true };
      else if (path.endsWith("/failures")) {
        listQueries.push(url.searchParams);
        const matches = url.searchParams.get("pending_rm_review") === "true" ? records.filter(r => r.pending_rm_review) : records;
        data = { records: matches, total: matches.length, page: 1, page_size: 20, total_pages: 1,
          pending_rm_review_count: records.filter(r => r.pending_rm_review).length };
      } else if (/\/failures\/\d+$/.test(path)) data = records.find(r => path.endsWith(`/${r.id}`));
      await route.fulfill({ json: data });
    });
    await page.goto("/jenkins-failures?date_from=2026-10-01&date_to=2026-10-23");
    const row = (id: number) => page.getByRole("row").filter({ hasText: `Hpc_App${id}_Release` });
    await expect(row(4).getByRole("button", { name: "复议中" })).toBeVisible();
    await expect(row(5).getByRole("button", { name: "复议已审定" })).toBeVisible();
    await expect(row(6).getByRole("button", { name: "复议异常" })).toBeVisible();
    const toggle = page.getByRole("checkbox", { name: /仅看待 RM 裁定/ });
    await expect(toggle.locator("..")).toContainText("3");
    await page.locator(".table-scroll").evaluate(element => { element.scrollLeft = element.scrollWidth; });
    await page.screenshot({ path: info.outputPath(`review-states-${width}.png`), fullPage: true });
    const layout = await page.locator(".cicd-agent-row-actions").evaluateAll(elements => elements.map(element => {
      const [detail, repro] = Array.from(element.querySelectorAll("button"));
      const first = detail.getBoundingClientRect();
      const second = repro.getBoundingClientRect();
      return {
        aligned: first.x === second.x && first.width === second.width && first.height === second.height,
        gap: second.top - first.bottom,
        width: first.width,
        overflow: [detail, repro].some(button => button.scrollWidth > button.clientWidth + 1),
        color: getComputedStyle(repro).backgroundColor,
      };
    }));
    expect(layout.every(item => item.aligned && item.gap === 6 && item.width === 120 && !item.overflow)).toBe(true);
    for (const index of [1, 3, 5]) expect(layout[index].color).toBe(layout[0].color);
    expect(layout[2].color).not.toBe(layout[0].color);
    expect(layout[4].color).not.toBe(layout[0].color);
    expect(layout[4].color).not.toBe(layout[2].color);
    expect(layout[6].color).toBe(layout[2].color);
    await row(5).getByRole("button", { name: "复议已审定" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`reviewed-state-${width}.png`), fullPage: true });
    await toggle.check();
    await expect(row(1)).toHaveCount(0);
    await expect(row(7).getByRole("button", { name: "有争议" })).toBeVisible();
    await expect(row(7).getByRole("button", { name: "复议待审" })).toBeVisible();
    expect(listQueries.at(-1)?.get("date_from")).toBe("2026-10-01");
    expect(listQueries.at(-1)?.get("pending_rm_review")).toBe("true");
    await row(2).getByRole("button", { name: "有争议" }).click();
    const dialog = page.getByRole("dialog", { name: "记录详情" });
    await expect(dialog.getByText("反馈建议责任人", { exact: true })).toBeVisible();
    await expect(dialog.getByLabel("反馈原因")).toHaveCount(0);
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await row(3).getByRole("button", { name: "复议待审" }).click();
    await expect(dialog.getByText("历史日志确认系统源404", { exact: true })).toBeVisible();
    await dialog.getByRole("button", { name: "带入责任处理" }).click();
    await expect(dialog.getByLabel("最终责任归属")).toHaveValue("DevOps");
    await expect(dialog.getByLabel("最终原因摘要")).toHaveValue("历史日志确认系统源404");
    await expect(dialog.getByLabel("最终处理建议")).toHaveValue("核查系统源");
    await expect(dialog.getByLabel("处理备注")).toHaveValue(`参考复议 ${task.id}: Release 文件返回404`);
    await dialog.getByRole("button", { name: "保存处理" }).click();
    await expect.poll(() => writes.length).toBe(1);
    expect(resolutions[0]).toMatchObject({
      action: "correct", final_owner_role: "DevOps", final_owner_account: "m00930",
      final_reason_summary: "历史日志确认系统源404", final_action_suggestion: "核查系统源",
      note: `参考复议 ${task.id}: Release 文件返回404`, source_repro_task_id: task.id,
    });
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(row(3)).toHaveCount(0);
    await expect(toggle.locator("..")).toContainText("2");
    await toggle.uncheck();
    await expect(row(3).getByRole("button", { name: "复议已审定" })).toBeVisible();
    await row(3).getByRole("button", { name: "复议已审定" }).click();
    await expect(dialog.getByLabel("复议记录")).toContainText("RM 已审定");
    await dialog.getByText("发起新的复议", { exact: true }).click();
    await dialog.getByLabel("复议原因").fill("补充新证据后复议");
    await dialog.getByRole("button", { name: "重新发起复议" }).click();
    await expect.poll(() => writes.length).toBe(2);
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(row(3).getByRole("button", { name: "复议中" })).toBeVisible();
    task.status = "completed";
    records[2].repro_review_state = "pending_review";
    records[2].pending_rm_review = true;
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(row(3).getByRole("button", { name: "复议待审" })).toBeVisible();
    expect(errors).toEqual([]);
  });
}
