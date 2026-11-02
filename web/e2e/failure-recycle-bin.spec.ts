import { expect, test, type Locator, type Page } from "@playwright/test";
import type { FailureLifecycleAction, FailureRecordDetail } from "../src/features/cicdAgent/cicdAgentApi";

test.use({
  baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:5176",
  launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : undefined,
});

async function mockFailures(page: Page, role = "RM") {
  const records: FailureRecordDetail[] = [
    { id: 101, job_name: "Hpc_Example_Release", build_number: 88, normalized_stage: "build_image",
      build_url: "https://jenkins.invalid/job/Hpc_Example_Release/88/",
      official_name: "Example", code_owner: "alice", owner_account: "bob", owner_role: "DevOps",
      record_state: "active", created_at: "2026-11-01T10:00:00", reason_summary: "Dependency failure",
      responsibility_status: "disputed", responsibility_events: [] },
    { id: 202, job_name: "Hpc_Older_Release", build_number: 77, normalized_stage: "build_image",
      official_name: "Older application", code_owner: "bob", owner_account: "bob", owner_role: "Code Owner",
      record_state: "trashed", created_at: "2025-01-01T10:00:00", reason_summary: "Duplicate build",
      record_state_updated_by: "rm", record_state_updated_at: "2026-11-02T10:00:00",
      record_state_reason: "Duplicate failure", responsibility_events: [] },
  ];
  const writes: { path: string; body: unknown }[] = [];
  const reads: URL[] = [];
  const errors: string[] = [];
  const blocked = new Set<number>();
  let conflict = false;
  page.on("pageerror", error => errors.push(error.message));
  await page.context().route(url => url.pathname.startsWith("/api/"), async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() !== "GET") {
      const body = request.postDataJSON() as { reason?: string; action: FailureLifecycleAction; record_ids: number[] };
      writes.push({ path, body });
      if (path === "/api/cicd-agent/failures/batch/record-state" && role === "RM") {
        const results = body.record_ids.map(id => {
          const record = records.find(item => item.id === id);
          if (!record) return { id, success: false, status_code: 404, error: "记录不存在" };
          if (blocked.has(id)) return { id, success: false, status_code: 409, error: "本条记录有未结束的复议" };
          if (record.record_state !== (body.action === "trash" ? "active" : "trashed")) {
            return { id, success: false, status_code: 409, error: "记录状态已变化" };
          }
          record.record_state = body.action === "trash" ? "trashed" : body.action === "restore" ? "active" : "deleted";
          record.record_state_reason = body.reason || "";
          record.record_state_updated_by = "alice";
          record.record_state_updated_at = "2026-11-02T12:00:00";
          return { id, success: true, status_code: 200 };
        });
        const succeeded = results.filter(item => item.success).length;
        await route.fulfill({ json: { results, succeeded, failed: results.length - succeeded } });
        return;
      }
      const actionMatch = path.match(/^\/api\/cicd-agent\/failures\/(\d+)\/(trash|restore|purge)$/);
      if (!actionMatch || role !== "RM") {
        await route.fulfill({ status: 403, json: { error: "Unexpected mutation" } });
        return;
      }
      const record = records.find(item => item.id === Number(actionMatch[1]))!;
      const action = actionMatch[2] as FailureLifecycleAction;
      if (conflict) {
        conflict = false;
        record.record_state = "active";
        await route.fulfill({ status: 409, json: { error: "Record state changed; refresh and retry" } });
        return;
      }
      const expectedState = action === "trash" ? "active" : "trashed";
      if (record.record_state !== expectedState) {
        await route.fulfill({ status: 409, json: { error: "Record state conflicts with action" } });
        return;
      }
      record.record_state = action === "trash" ? "trashed" : action === "restore" ? "active" : "deleted";
      record.record_state_updated_by = "alice";
      record.record_state_updated_at = "2026-11-02T12:00:00";
      record.record_state_reason = body.reason || "";
      await route.fulfill({ json: { id: record.id, record_state: record.record_state,
        record_state_updated_by: record.record_state_updated_by,
        record_state_updated_at: record.record_state_updated_at, record_state_reason: record.record_state_reason } });
      return;
    }
    reads.push(url);
    const state = url.searchParams.get("record_state") || "active";
    const matches = records.filter(record => record.record_state === state
      && (!url.searchParams.get("code_owner") || record.code_owner === url.searchParams.get("code_owner")));
    let data: unknown = {};
    if (path === "/api/me") data = { user: { username: "alice", display_name: "Alice", role } };
    else if (path === "/api/state") data = { apps: [], releases: [], user_display_names: { alice: "Alice", bob: "Bob" } };
    else if (path.endsWith("/preferences")) data = { enabled: false, send_time: "09:45", timezone: "Asia/Shanghai" };
    else if (path.endsWith("/filter-options")) data = { owners: [{ code_owner: "alice" }, { code_owner: "bob" }], owner_accounts: ["bob"] };
    else if (path.endsWith("/summary")) data = { total_records: matches.length, groups: [] };
    else if (path.endsWith("/failures")) data = { records: matches, total: matches.length, page: 1, page_size: 20, total_pages: matches.length ? 1 : 0 };
    else if (/\/failures\/\d+$/.test(path)) {
      const record = matches.find(item => item.id === Number(path.split("/").at(-1)));
      if (!record) { await route.fulfill({ status: 404, json: { error: "Record not found" } }); return; }
      data = record;
    }
    await route.fulfill({ json: data });
  });
  return { records, reads, writes, errors, blocked, conflictNext: () => { conflict = true; } };
}

function row(page: Page, id: number) {
  return page.getByRole("row").filter({ has: page.getByText(`#${id}`, { exact: true }) });
}

async function expectNarrowActions(record: Locator) {
  const dimensions = await record.locator(".cicd-agent-row-actions").evaluate(element => {
    const buttons = Array.from(element.querySelectorAll("button")).map(button => {
      const rect = button.getBoundingClientRect();
      return { left: rect.left, top: rect.top, bottom: rect.bottom, width: rect.width,
        fits: button.scrollWidth <= button.clientWidth + 1 };
    });
    return { width: element.getBoundingClientRect().width, buttons };
  });
  expect(dimensions.width).toBeLessThanOrEqual(120);
  dimensions.buttons.forEach((button, index) => {
    expect(button.fits).toBe(true);
    expect(button.width).toBeLessThanOrEqual(120);
    if (index) {
      expect(button.left).toBe(dimensions.buttons[index - 1].left);
      expect(button.top).toBeGreaterThanOrEqual(dimensions.buttons[index - 1].bottom);
    }
  });
}

for (const width of [1366, 1920]) {
  test(`RM delete, read-only trash detail, restore and purge at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 950 });
    const fixture = await mockFailures(page);
    await page.goto("/jenkins-failures?date_from=2026-10-01&date_to=2026-11-02");
    await page.getByRole("checkbox", { name: "只看我的" }).check();
    await expectNarrowActions(row(page, 101));
    await expect(row(page, 101).locator(".cicd-agent-row-actions").getByRole("button")).toHaveCount(2);
    await expect(row(page, 101).getByRole("button", { name: "删除", exact: true })).toHaveCount(0);
    await row(page, 101).getByRole("button", { name: "查看详情", exact: true }).click();
    const initialDetail = page.getByRole("dialog", { name: "记录详情" });
    const trashButton = initialDetail.getByRole("button", { name: "移入回收站", exact: true });
    await expect(trashButton).toBeEnabled();
    await expect(trashButton).toHaveCSS("color", "rgb(193, 53, 42)");
    await expect(trashButton.locator("svg")).toHaveAttribute("width", "18");
    const buttons = await initialDetail.locator(".cicd-agent-detail-actions > *").evaluateAll(elements =>
      elements.map(element => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom,
          fits: element.scrollWidth <= element.clientWidth + 1 };
      }));
    buttons.forEach((button, index) => {
      expect(button.fits).toBe(true);
      expect(button.left).toBeGreaterThanOrEqual(0);
      expect(button.right).toBeLessThanOrEqual(width);
      if (index) expect(button.left >= buttons[index - 1].right || button.top >= buttons[index - 1].bottom).toBe(true);
    });
    await page.screenshot({ path: info.outputPath(`detail-delete-entry-${width}.png`), fullPage: true, animations: "disabled" });
    await trashButton.click();
    const deletion = page.getByRole("alertdialog", { name: "移入回收站" });
    const detailRoot = page.locator('[role="dialog"][aria-labelledby="failure-detail-title"]');
    await expect(detailRoot).toHaveAttribute("inert", "");
    await deletion.getByRole("button", { name: "取消" }).click();
    await expect(trashButton).toBeFocused();
    expect(fixture.writes).toEqual([]);
    await trashButton.click();
    await page.keyboard.press("Escape");
    await expect(deletion).toHaveCount(0);
    await expect(initialDetail).toBeVisible();
    await expect(trashButton).toBeFocused();
    await trashButton.click();
    await page.locator(".cicd-agent-lifecycle-backdrop").click({ position: { x: 5, y: 5 } });
    await expect(deletion).toHaveCount(0);
    await expect(initialDetail).toBeVisible();
    await expect(trashButton).toBeFocused();
    await trashButton.click();
    await expect(deletion).toContainText("Hpc_Example_Release");
    await expect(deletion).toContainText("#88");
    await expect(deletion).toContainText("build_image");
    await expect(deletion.getByRole("button", { name: "移入回收站" })).toBeEnabled();
    await deletion.getByLabel("备注（选填）").fill("Duplicate failure");
    await expect(deletion).toHaveCSS("opacity", "1");
    await expect(deletion).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await expect(page.locator(".cicd-agent-lifecycle-backdrop")).toHaveCSS("z-index", "130");
    await page.screenshot({ path: info.outputPath(`trash-confirm-${width}.png`), fullPage: true, animations: "disabled" });
    await deletion.getByRole("button", { name: "移入回收站" }).click();
    await expect(deletion).toHaveCount(0);
    await expect(detailRoot).toHaveCount(0);
    await expect(row(page, 101)).toHaveCount(0);

    await page.getByRole("tab", { name: "回收站", exact: true }).click();
    await expect(row(page, 101)).toBeVisible();
    await expect(row(page, 202)).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "删除原因", exact: true })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "删除时间", exact: true })).toBeVisible();
    await expect(row(page, 101)).toContainText("删除人：Alice (alice)");
    await expect(row(page, 101)).toContainText("2026-11-02 12:00:00");
    await expect(row(page, 101)).not.toContainText("Dependency failure");
    await expect(page.getByLabel("开始时间")).toHaveValue("");
    await expect(page.getByLabel("结束时间")).toHaveValue("");
    await expect(page.getByRole("checkbox", { name: "只看我的" })).not.toBeChecked();
    for (const suffix of ["", "/summary", "/filter-options"]) {
      await expect.poll(() => fixture.reads.some(url => url.pathname === `/api/cicd-agent/failures${suffix}`
        && url.searchParams.get("record_state") === "trashed"
        && !url.searchParams.has("date_from") && !url.searchParams.has("date_to")
        && !url.searchParams.has("code_owner"))).toBe(true);
    }
    const trashQuery = fixture.reads.filter(url => url.pathname === "/api/cicd-agent/failures").at(-1)!;
    expect(trashQuery.searchParams.get("sort_by")).toBe("record_state_updated_at");
    await expectNarrowActions(row(page, 101));
    await row(page, 101).getByRole("button", { name: "彻底删除" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath(`recycle-bin-${width}.png`), fullPage: true, animations: "disabled" });
    await row(page, 101).getByRole("button", { name: "查看详情" }).click();
    const detail = page.getByRole("dialog", { name: "记录详情" });
    await expect(detail).toContainText("回收站（只读）");
    await expect(detail).toContainText("Duplicate failure");
    await expect(detail).toContainText("Dependency failure");
    for (const name of ["责任反馈", "处理责任", "复议诊断"]) await expect(detail.getByRole("button", { name })).toHaveCount(0);
    expect(fixture.reads.some(url => url.pathname.endsWith("/repro-tasks"))).toBe(false);
    await page.screenshot({ path: info.outputPath(`trash-detail-${width}.png`), fullPage: true, animations: "disabled" });
    await detail.getByRole("button", { name: "关闭" }).click();
    await row(page, 101).getByRole("button", { name: "恢复", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "恢复记录" }).click();
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
    await expect(row(page, 101)).toHaveCount(0);
    await page.getByRole("tab", { name: "正常失败" }).click();
    await expect(page.getByLabel("开始时间")).toHaveValue("2026-10-01");
    await expect(page.getByLabel("结束时间")).toHaveValue("2026-11-02");
    await expect(page.getByRole("checkbox", { name: "只看我的" })).toBeChecked();
    await expect(row(page, 101)).toBeVisible();
    await row(page, 101).getByRole("button", { name: "查看详情" }).click();
    await expect(detail.getByRole("button", { name: "责任反馈" })).toBeVisible();
    await expect(detail.getByText("回收站（只读）")).toHaveCount(0);
    await detail.getByRole("button", { name: "关闭" }).click();

    await page.getByRole("tab", { name: "回收站", exact: true }).click();
    await row(page, 202).getByRole("button", { name: "彻底删除" }).click();
    const purge = page.getByRole("alertdialog", { name: "彻底删除" });
    await expect(purge).toContainText("无法在界面恢复");
    await expect(purge).toContainText("归档仍按保留策略留存");
    await expect(purge.getByRole("button", { name: "彻底删除" })).toBeDisabled();
    await purge.getByLabel("输入“彻底删除 #202”确认").fill("彻底删除 #101");
    await expect(purge.getByRole("button", { name: "彻底删除" })).toBeDisabled();
    await purge.getByLabel("输入“彻底删除 #202”确认").fill("彻底删除 #202");
    await expect(purge.getByLabel("输入“彻底删除 #202”确认")).toHaveAttribute("type", "text");
    await expect(purge).toHaveCSS("opacity", "1");
    await expect(purge).toHaveCSS("background-color", "rgb(255, 255, 255)");
    await page.screenshot({ path: info.outputPath(`purge-confirm-${width}.png`), fullPage: true, animations: "disabled" });
    await purge.getByRole("button", { name: "彻底删除" }).click();
    await expect(purge).toHaveCount(0);
    await expect(row(page, 202)).toHaveCount(0);
    expect(fixture.writes).toEqual([
      { path: "/api/cicd-agent/failures/101/trash", body: { reason: "Duplicate failure" } },
      { path: "/api/cicd-agent/failures/101/restore", body: { reason: "" } },
      { path: "/api/cicd-agent/failures/202/purge", body: { reason: "" } },
    ]);
    expect(fixture.records.find(record => record.id === 202)?.record_state).toBe("deleted");
    expect(fixture.errors).toEqual([]);
  });
}

for (const role of ["Owner", "SPD"]) {
  test(`${role} has no recycle bin or lifecycle controls`, async ({ page }) => {
    const fixture = await mockFailures(page, role);
    await page.goto("/jenkins-failures?record_state=trashed");
    await expect(row(page, 101)).toBeVisible();
    await expect(page.getByRole("tab", { name: "回收站" })).toHaveCount(0);
    for (const name of ["删除", "恢复", "彻底删除"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
    await row(page, 101).getByRole("button", { name: "查看详情" }).click();
    const detail = page.getByRole("dialog", { name: "记录详情" });
    await expect(detail).toContainText("Dependency failure");
    await expect(detail.getByRole("button", { name: "移入回收站" })).toHaveCount(0);
    expect(fixture.reads.some(url => url.searchParams.get("record_state") === "trashed")).toBe(false);
    expect(fixture.writes).toEqual([]);
    expect(fixture.errors).toEqual([]);
  });
}

for (const width of [1366, 1920]) {
  test(`RM batch actions, partial results and confirmation at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 950 });
    const fixture = await mockFailures(page);
    fixture.records.push({ ...fixture.records[0], id: 303, job_name: "Hpc_Long_Application_Name_Release", build_number: 99 });
    fixture.blocked.add(303);
    await page.goto("/jenkins-failures");
    const selectAll = page.getByRole("checkbox", { name: "选择当前页全部记录" });
    await selectAll.check();
    await expect(page.getByText("已选 2 条", { exact: true })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "批量删除", exact: true }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("批量移入回收站（2 条）");
    await expect(dialog.getByRole("button", { name: "批量移入回收站" })).toBeEnabled();
    await expect(dialog).toHaveCSS("opacity", "1");
    await page.screenshot({ path: info.outputPath(`batch-trash-${width}.png`), fullPage: true, animations: "disabled" });
    await dialog.getByRole("button", { name: "批量移入回收站" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("成功 1 条，未完成 1 条");
    await expect(page.getByRole("status")).toContainText("#303：本条记录有未结束的复议");
    await expect(page.getByRole("checkbox", { name: "选择记录 #303" })).toBeChecked();
    await page.screenshot({ path: info.outputPath(`batch-partial-${width}.png`), fullPage: true, animations: "disabled" });

    await page.getByRole("tab", { name: "回收站", exact: true }).click();
    await expect(page.getByText("已选 0 条", { exact: true })).toBeVisible();
    await selectAll.check();
    await page.getByRole("button", { name: "批量恢复", exact: true }).click();
    await dialog.getByRole("button", { name: "批量恢复记录" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("成功 2 条，未完成 0 条");

    await page.getByRole("tab", { name: "正常失败" }).click();
    await page.getByRole("checkbox", { name: "只看我的" }).uncheck();
    fixture.blocked.clear();
    await expect(row(page, 202)).toBeVisible();
    await selectAll.check();
    await page.getByRole("button", { name: "批量删除", exact: true }).click();
    await dialog.getByRole("button", { name: "批量移入回收站" }).click();
    await expect(dialog).toHaveCount(0);
    await page.getByRole("tab", { name: "回收站", exact: true }).click();
    await selectAll.check();
    await page.getByRole("button", { name: "批量彻底删除", exact: true }).click();
    const confirm = dialog.getByLabel("输入“彻底删除 3 条”确认");
    await confirm.fill("彻底删除 2 条");
    await expect(dialog.getByRole("button", { name: "批量彻底删除" })).toBeDisabled();
    await confirm.fill("彻底删除 3 条");
    await expect(dialog).toHaveCSS("opacity", "1");
    const rect = await dialog.boundingBox();
    expect(rect!.x).toBeGreaterThanOrEqual(0);
    expect(rect!.y).toBeGreaterThanOrEqual(0);
    expect(rect!.x + rect!.width).toBeLessThanOrEqual(width);
    expect(rect!.y + rect!.height).toBeLessThanOrEqual(950);
    await page.screenshot({ path: info.outputPath(`batch-purge-${width}.png`), fullPage: true, animations: "disabled" });
    await dialog.getByRole("button", { name: "批量彻底删除" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("成功 3 条，未完成 0 条");
    expect(fixture.writes.at(-1)?.body).toEqual({ record_ids: [101, 202, 303], action: "purge", reason: "" });
    expect(fixture.records.every(record => record.record_state === "deleted")).toBe(true);
    expect(fixture.errors).toEqual([]);
  });
}

test("twenty selected records fit a 1366x768 confirmation dialog", async ({ page }, info) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  const fixture = await mockFailures(page);
  for (let id = 203; id < 222; id++) {
    fixture.records.push({ ...fixture.records[1], id, job_name: `Hpc_Long_Application_Name_For_Batch_Test_${id}_Release` });
  }
  await page.goto("/jenkins-failures");
  await page.getByRole("tab", { name: "回收站", exact: true }).click();
  await page.getByRole("checkbox", { name: "选择当前页全部记录" }).check();
  await page.getByRole("button", { name: "批量彻底删除", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("批量彻底删除（20 条）");
  await dialog.getByLabel("输入“彻底删除 20 条”确认").fill("彻底删除 20 条");
  const list = dialog.getByLabel("本次选中的记录");
  expect(await list.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  await list.evaluate(element => { element.scrollTop = element.scrollHeight; });
  await expect(list.getByText("#221", { exact: true })).toBeInViewport();
  await expect(dialog.getByRole("button", { name: "批量彻底删除" })).toBeInViewport();
  await expect(dialog).toHaveCSS("opacity", "1");
  await page.screenshot({ path: info.outputPath("batch-twenty-1366x768.png"), animations: "disabled" });
  await dialog.getByRole("button", { name: "取消" }).click();
  expect(fixture.writes).toEqual([]);
  expect(fixture.errors).toEqual([]);
});

test("409 stale state can refresh without retaining trash detail", async ({ page }) => {
  const fixture = await mockFailures(page);
  await page.goto("/jenkins-failures");
  await page.getByRole("tab", { name: "回收站", exact: true }).click();
  await row(page, 202).getByRole("button", { name: "查看详情" }).click();
  const detail = page.getByRole("dialog", { name: "记录详情" });
  await expect(detail).toContainText("回收站（只读）");
  await detail.getByRole("button", { name: "关闭" }).click();
  await row(page, 202).getByRole("button", { name: "恢复", exact: true }).click();
  fixture.conflictNext();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "恢复记录" }).click();
  await expect(dialog.getByRole("alert")).toContainText("Record state changed");
  await expect(row(page, 202)).toBeVisible();
  await dialog.getByRole("button", { name: "刷新记录" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(row(page, 202)).toHaveCount(0);
  await page.getByRole("tab", { name: "正常失败" }).click();
  await row(page, 202).getByRole("button", { name: "查看详情" }).click();
  await expect(detail.getByRole("button", { name: "责任反馈" })).toBeVisible();
  await expect(detail.getByText("回收站（只读）")).toHaveCount(0);
  expect(fixture.writes).toHaveLength(1);
  expect(fixture.errors).toEqual([]);
});
