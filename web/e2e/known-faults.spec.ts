import { expect, test } from "@playwright/test";

test.use({ baseURL: process.env.KNOWN_FAULT_UI_URL || "http://127.0.0.1:8033",
  launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : undefined });

for (const width of [1440, 390]) {
  test(`known fault create, preview and selected application at ${width}px`, async ({ page }, info) => {
    await page.setViewportSize({ width, height: 960 });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    const record = { id: 10, job_name: "Hpc_Demo_Release", build_number: 99, official_name: "Demo",
      code_owner: "rm", owner_role: "DevOps", owner_account: "m00930", normalized_stage: "build image",
      record_source: "pipeline_diagnosis", record_state: "active", responsibility_status: "ai_suggested",
      key_error_snippet: "libibumad-dev : Depends: libibumad3 but a different version is to be installed",
      reason_summary: "依赖安装失败", created_at: "2026-11-23T09:00:00" };
    let rules: any[] = [];
    const writes: { path: string; body: any }[] = [];
    // All requests are intercepted: this scenario cannot modify development data or send messages.
    await page.route("**/api/**", async route => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const body = request.method() === "GET" ? null : request.postDataJSON();
      if (body) writes.push({ path, body });
      let data: any = {};
      if (path === "/api/me") data = { user: { username: "rm", display_name: "RM Test", role: "RM" } };
      else if (path === "/api/state") data = { apps: [], releases: [], user_display_names: {} };
      else if (path.endsWith("/tasks")) data = { tasks: [] };
      else if (path.endsWith("/requests")) data = { requests: [] };
      else if (path.endsWith("/deliveries")) data = { deliveries: [] };
      else if (path.endsWith("/filter-options")) data = { owners: [], owner_accounts: [] };
      else if (path.endsWith("/summary")) data = { total_records: 1, groups: [] };
      else if (path.endsWith("/failures")) data = { records: [record], total: 1, page: 1, page_size: 20, total_pages: 1 };
      else if (path.endsWith("/failures/10")) data = { record };
      else if (path.endsWith("/known-fault-rules")) {
        if (request.method() === "POST") {
          const rule = { id: 1, revision: 1, state: body.config.enabled ? "active" : "disabled",
            config: body.config, updated_by: "rm", updated_at: "2026-11-23T10:00:00" };
          rules = [rule]; data = rule;
        } else data = { items: rules };
      } else if (path.endsWith("/preview")) data = { preview_id: "a".repeat(32), rule_id: 1, revision: 1,
        rule_active: true, expires_at: "2026-11-23T10:15:00", items: [{ id: 10, app: "Demo", job: record.job_name,
          build: 99, before: { role: "DevOps", account: "m00930", summary: "原诊断" },
          after: { role: "Code Owner", account: "rm", summary: "通信库调整，暂挂 Code Owner" },
          eligible: true, manual: false, skip_reason: "", evidence: { source: "saved_key_error", quotes: [record.key_error_snippet] } }] };
      else if (path.endsWith("/apply")) data = { count: 1, applied_ids: [10] };
      await route.fulfill({ json: data });
    });
    await page.goto("/cicd");
    await page.getByRole("button", { name: "已知故障规则", exact: true }).click();
    await page.getByRole("button", { name: "新增规则", exact: true }).click();
    await page.getByLabel("规则名称", { exact: true }).fill("通信库调整导致 libibumad 依赖冲突");
    await page.getByLabel("关键错误（全部匹配）", { exact: true }).fill("libibumad-dev*Depends:*libibumad3*but*is to be installed\nUnable to correct problems");
    await page.getByLabel("原因摘要", { exact: true }).fill("通信库调整 libibverbs 等库的安装导致，暂且挂靠 Code Owner。");
    await page.getByLabel("处理建议", { exact: true }).fill("联系通信库团队核对离线 deb 包版本；确认前不要自行替换依赖。");
    await page.getByLabel("启用规则", { exact: true }).check();
    await expect(page.getByLabel("基础镜像版本（可选）")).toHaveValue("");
    const form = page.locator(".known-fault-editor");
    expect(await form.evaluate(e => e.scrollWidth <= e.clientWidth + 1)).toBeTruthy();
    await page.screenshot({ path: info.outputPath(`editor-${width}.png`), fullPage: true });
    await page.getByRole("button", { name: "保存规则", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("已保存 #1 v1");
    await page.getByRole("button", { name: "预览匹配", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认应用 1 条", exact: true })).toBeEnabled();
    await page.screenshot({ path: info.outputPath(`preview-${width}.png`), fullPage: true });
    expect(writes.filter(w => w.path.endsWith("/apply"))).toHaveLength(0);

    await page.goto("/jenkins-failures");
    await page.getByRole("checkbox", { name: "选择记录 #10", exact: true }).check();
    await page.getByRole("button", { name: "故障规则 / 批量应用", exact: true }).click();
    const modal = page.getByRole("dialog", { name: "已知故障规则", exact: true });
    await expect(modal).toBeVisible();
    const close = modal.getByRole("button", { name: "关闭规则管理", exact: true });
    const initialCloseBox = await close.boundingBox();
    const iconBox = await close.locator("svg").boundingBox();
    expect(iconBox?.width).toBeGreaterThanOrEqual(18);
    expect(iconBox?.height).toBeGreaterThanOrEqual(18);
    const scroller = modal.locator(".known-fault-dialog-body");
    await scroller.evaluate(element => { element.scrollTop = element.scrollHeight; });
    expect(await scroller.evaluate(element => element.scrollTop)).toBeGreaterThan(0);
    const scrolledCloseBox = await close.boundingBox();
    expect(scrolledCloseBox?.y).toBe(initialCloseBox?.y);
    await expect(close).toBeInViewport();
    await page.screenshot({ path: info.outputPath(`close-scrolled-${width}.png`), animations: "disabled" });
    await close.click();
    await expect(modal).toHaveCount(0);
    await page.getByRole("button", { name: "故障规则 / 批量应用", exact: true }).click();
    await modal.getByRole("button", { name: "取消编辑", exact: true }).click();
    await modal.getByRole("button", { name: "历史匹配", exact: true }).click();
    await modal.getByRole("button", { name: "预览匹配", exact: true }).click();
    await modal.getByRole("button", { name: "确认应用 1 条", exact: true }).click();
    const confirm = page.locator(".confirm-card");
    await expect(confirm).toBeVisible();
    await page.screenshot({ path: info.outputPath(`confirmation-${width}.png`), fullPage: true, animations: "disabled" });
    await confirm.getByRole("button", { name: "确认应用", exact: true }).click();
    await expect(modal.getByRole("status")).toContainText("已应用到 1 条记录");
    expect(writes.filter(w => w.path.endsWith("/apply"))).toHaveLength(1);
    expect(writes.filter(w => w.path.endsWith("/preview")).at(-1)?.body.record_ids).toEqual([10]);
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
