import { test, expect, type Locator, type Page } from "@playwright/test";
import type { ReproTask } from "../src/features/cicdAgent/FailureReproPanel";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033" });

const historicalTask: ReproTask = {
  id: "history-review", failure_id: 2188, status: "completed", requested_by: "hajiang",
  reason: "复核历史日志中的依赖冲突", node_name: null, created_at: "2026-10-23T09:00:00",
  error: null, stale: false, resolved_by: null, cancel_requested: false,
  history: [{ status: "analyzing_history", at: "2026-10-23T09:01:00", detail: "分析已归档的 Jenkins 日志" }],
  report: {
    outcome: "historical_log_confirmed", source_commit: "", arch: "amd64", verified: false,
    base_digests_match: false, environment_error: "", suggested_owner_account: "m00859", builds: [], patches: [],
    build_skipped_reason: "历史日志已明确显示固定依赖版本冲突",
    log_review: {
      decision: "log_sufficient", reason: "安装器明确报告 protobuf 与 grpcio-tools 的固定版本不兼容。",
      matched_rule_id: "python-dependency-conflict", policy_version: "history-v1",
      evidence: [{ start_line: 421, end_line: 423, quote: "ERROR: Cannot install protobuf and grpcio-tools\nERROR: ResolutionImpossible" }],
    },
    verification: {
      execution_mode: "historical_logs", build_skipped_reason: "历史日志证据充分", passed: false,
      summary: "历史日志诊断完成，未执行构建", original_failure_reproduced: false,
      scope: "仅依据 Jenkins 历史日志认定，未执行构建或基础镜像核对。",
      targets: [], builds: [], installed_packages: [],
    },
    diagnosis: {
      summary: "原始 Jenkins 日志已确认依赖版本冲突，建议由 Code Owner 调整版本约束。",
      suggested_role: "Code Owner", responsibility_reason: "protobuf 与 grpcio-tools 的固定版本范围不兼容。",
      action_suggestion: "核对依赖约束后提交修正，重新运行流水线验证。",
      original_evidence: "The conflict is caused by grpcio-tools requiring a different protobuf version.",
      reproduction_evidence: "", limitations: ["未执行构建，未验证补丁或运行功能。"],
    },
  },
};

const failureRecord = {
  id: 2188, job_name: "Hpc_Diffdock_Release", build_number: 1543, normalized_stage: "build image",
  owner_account: "m00859", owner_role: "Code Owner", code_owner: "m00859", official_name: "DiffDock",
  responsibility_status: "ai_suggested", reason_summary: "Python 固定依赖版本冲突",
  action_suggestion: "检查依赖兼容范围", created_at: "2026-10-23T09:00:00", responsibility_events: [],
};

async function mockReport(page: Page, task = historicalTask, role = "RM", recordOverrides: Partial<typeof failureRecord> = {}) {
  let currentTask = structuredClone(task);
  const writes: string[] = [];
  const logReads: string[] = [];
  const errors: string[] = [];
  const record = { ...failureRecord, id: task.failure_id, ...recordOverrides };
  page.on("pageerror", (error) => errors.push(error.message));
  await page.context().route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes.push(path);
    let data: unknown = {};
    if (path.endsWith("/logs")) {
      logReads.push(path);
      data = { logs: [
        { name: "original.log", label: "Jenkins 原始日志", bytes: 9000 },
        { name: "history-review.log", label: "历史日志审查", bytes: 2400 },
        { name: "evidence-preparation.log", label: "证据准备", bytes: 1200 },
      ] };
    } else if (path.includes("/logs/")) {
      logReads.push(path);
      const content = path.endsWith("/history-review.log") ? "历史日志审查完成：log_sufficient；execution_mode=historical_logs"
        : path.endsWith("/evidence-preparation.log") ? "已读取归档 Jenkins 日志；未检查、未下载源码。"
          : `Archived Jenkins output\n${currentTask.report?.diagnosis?.original_evidence || "ERROR: ResolutionImpossible"}`;
      data = { content, start_line: 301, end_line: 600, total_lines: 600, previous_before: 300 };
    } else if (path === "/api/me") data = { user: { username: "hajiang", display_name: "Haiyang", role } };
    else if (path === "/api/state") data = { user_display_names: { m00859: "Jiazhen Gong" }, releases: [], apps: [] };
    else if (path.endsWith("/summary")) data = { total_records: 1, groups: [] };
    else if (path.endsWith("/repro-tasks")) data = { tasks: [currentTask], enabled: true, worker_online: true, evidence_archive: { status: "complete" } };
    else if (path.endsWith(`/failures/${record.id}`)) data = record;
    else if (path.endsWith("/failures")) data = { records: [record], total: 1, page: 1, page_size: 20, total_pages: 1 };
    await route.fulfill({ json: data });
  });
  await page.goto("/jenkins-failures?date_from=2026-10-23&date_to=2026-10-23");
  await page.getByRole("button", { name: "复议诊断", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "记录详情" });
  const panel = dialog.getByRole("region", { name: "复议诊断", exact: true });
  await expect(panel.getByLabel("复议记录")).toBeVisible();
  return { dialog, panel, writes, logReads, errors, setTask: (next: ReproTask) => { currentTask = next; } };
}

async function expectDesktopLayout(panel: Locator) {
  const issues = await panel.evaluate((element) => {
    const problems: string[] = [];
    const overlaps = (a: Element, b: Element) => {
      const first = a.getBoundingClientRect();
      const second = b.getBoundingClientRect();
      return Math.min(first.right, second.right) - Math.max(first.left, second.left) > 1
        && Math.min(first.bottom, second.bottom) - Math.max(first.top, second.top) > 1;
    };
    for (const node of [element, ...element.querySelectorAll(".section-head-row, .cicd-repro-facts dd, table")]) {
      if (node.clientWidth && node.scrollWidth > node.clientWidth + 1) problems.push(`overflow: ${node.tagName}`);
    }
    for (const facts of element.querySelectorAll(".cicd-repro-facts")) {
      const rows = Array.from(facts.querySelectorAll("dt"));
      for (const [i, label] of rows.entries()) {
        const value = label.nextElementSibling!;
        if (overlaps(label, value)) problems.push(`label overlaps value: ${label.textContent}`);
        const next = rows[i + 1];
        if (next && overlaps(value, next.nextElementSibling!)) problems.push(`facts overlap: ${label.textContent}`);
      }
    }
    const outcome = element.querySelector(".cicd-repro-outcome")!;
    const carry = outcome.nextElementSibling;
    if (carry && overlaps(outcome, carry)) problems.push("outcome overlaps responsibility action");
    const header = element.closest(".cicd-agent-detail-dialog")!.querySelector(":scope > .section-head-row")!;
    for (const control of header.querySelectorAll("h3, button")) {
      const rect = control.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      if (!control.contains(hit)) problems.push(`dialog header obscured: ${control.textContent}`);
    }
    return problems;
  });
  expect(issues).toEqual([]);
}

for (const width of [1366, 1920]) {
  test(`AIModels historical DevOps result and public logs at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    // The task identifiers match the reported run; the evidence text is synthetic.
    const skippedReason = "历史日志已形成完整的责任判断依据，建议责任人为 DevOps（m00930）。本次复议仅审查已归档的 Jenkins 日志与节点 387 的历史记录，不检查或下载源码，不分配新的执行节点，也不运行复现构建、候选补丁验证或基础镜像核对。";
    const task: ReproTask = { ...historicalTask, id: "6e7c3cbaaea849c2b33e3345aec0fb3e", failure_id: 2211,
      reason: "复核 AIModels 历史日志", report: { ...historicalTask.report!, suggested_owner_account: "m00930",
        build_skipped_reason: skippedReason,
        log_review: { ...historicalTask.report!.log_review!, matched_rule_id: "",
          reason: "历史日志支持将本次失败归属 DevOps。",
          evidence: [{ start_line: 387, end_line: 388, quote: "Archived node: 387\nHistorical review: DevOps" }] },
        diagnosis: { ...historicalTask.report!.diagnosis!, suggested_role: "DevOps",
          summary: "历史日志诊断完成，建议由 DevOps 负责后续处理。",
          responsibility_reason: "历史日志支持将本次失败归属 DevOps。",
          action_suggestion: "由 DevOps 复核历史日志中的失败原因并跟进处理。",
          original_evidence: "Archived node: 387\nHistorical review: DevOps" },
      } };
    const { panel, dialog, writes, logReads, errors } = await mockReport(page, task, "RM", {
      job_name: "Hpc_AIModels_Release", build_number: 1848, official_name: "AIModels",
      owner_role: "DevOps", owner_account: "m00930", reason_summary: "历史日志责任复核", action_suggestion: "由 DevOps 跟进处理",
    });
    await expect(panel.getByText("历史日志诊断完成 · 未执行构建", { exact: true })).toBeVisible();
    await expect(panel.getByText("DevOps / m00930", { exact: true })).toBeVisible();
    await expect(panel.getByText("未执行构建：历史日志证据充分", { exact: true })).toBeVisible();
    await expect(panel.getByText(skippedReason, { exact: true })).toHaveCount(1);
    await expect(panel.getByText(skippedReason, { exact: true })).not.toBeVisible();
    await expect(panel.locator(".passed, .lucide-shield-check")).toHaveCount(0);
    expect(logReads).toEqual([]);
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`aimodels-history-${width}.png`), fullPage: true });

    await panel.getByText("历史日志与认定证据", { exact: true }).click();
    await expect(panel.getByText(skippedReason, { exact: true })).toBeVisible();
    await expect(panel.getByText("未分配（已跳过构建）")).toBeVisible();
    await expect(panel.getByText("未检查、未下载（已跳过源码准备）")).toBeVisible();
    await expect(panel.getByRole("row", { name: "构建验证 未执行", exact: true })).toBeVisible();
    await expect(panel.getByRole("row", { name: "基础镜像核对 未执行", exact: true })).toBeVisible();
    await panel.getByText("原始日志第 387-388 行").scrollIntoViewIfNeeded();
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`aimodels-evidence-${width}.png`), fullPage: true });

    await panel.getByRole("button", { name: "查看 Jenkins 原始日志" }).click();
    await expect(panel.getByLabel("日志内容")).toContainText("Archived node: 387");
    const logs = panel.getByRole("combobox", { name: "过程日志", exact: true });
    await logs.selectOption({ label: "历史日志审查" });
    await expect(panel.getByLabel("日志内容")).toContainText("历史日志审查完成");
    await expect(panel.getByRole("link", { name: "下载完整脱敏日志" })).toHaveAttribute("href", /history-review\.log\/download$/);
    await logs.selectOption({ label: "证据准备" });
    await expect(panel.getByLabel("日志内容")).toContainText("未检查、未下载源码");
    await expect(panel.getByRole("link", { name: "下载完整脱敏日志" })).toHaveAttribute("href", /evidence-preparation\.log\/download$/);
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`aimodels-logs-${width}.png`), fullPage: true });
    await panel.getByRole("button", { name: "带入责任处理" }).click();
    await expect(dialog.getByLabel("最终责任归属")).toHaveValue("DevOps");
    await expect(dialog.getByLabel("最终原因摘要")).toHaveValue(task.report!.diagnosis!.summary);
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test(`historical diagnosis, original log and RM prefill at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const { panel, dialog, writes, logReads, errors } = await mockReport(page);
    const outcome = panel.getByText("历史日志诊断完成 · 未执行构建", { exact: true });
    await expect(outcome).toBeVisible();
    await expect(outcome).not.toHaveClass(/passed/);
    await expect(outcome.locator(".lucide-file-text")).toHaveCount(1);
    await expect(panel.getByText("Code Owner / m00859")).toBeVisible();
    await expect(panel.getByText(historicalTask.report!.diagnosis!.responsibility_reason)).toBeVisible();
    await expect(panel.getByText(historicalTask.report!.diagnosis!.action_suggestion)).toBeVisible();
    await expect(panel.getByText(`未执行构建：${historicalTask.report!.build_skipped_reason}`)).toBeVisible();
    const details = panel.locator("details").filter({ has: page.locator("summary", { hasText: "历史日志与认定证据" }) });
    await expect(details).not.toHaveAttribute("open");
    expect(logReads).toEqual([]);
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`historical-report-${width}.png`), fullPage: true });

    await panel.getByText("历史日志与认定证据", { exact: true }).click();
    await expect(details.getByText("未检查、未下载（已跳过源码准备）")).toBeVisible();
    await expect(details.getByText("未分配（已跳过构建）")).toBeVisible();
    await expect(details.getByText("amd64", { exact: true })).toBeVisible();
    await expect(details.getByRole("row", { name: "构建验证 未执行", exact: true })).toBeVisible();
    await expect(details.getByRole("row", { name: "基础镜像核对 未执行", exact: true })).toBeVisible();
    await expect(details.getByText("原始日志第 421-423 行")).toBeVisible();
    await expect(details.getByText(historicalTask.report!.log_review!.evidence[0].quote, { exact: true })).toBeVisible();
    await expect(details.getByText("python-dependency-conflict")).toBeVisible();
    await expectDesktopLayout(panel);
    await details.getByText("原始日志第 421-423 行").scrollIntoViewIfNeeded();
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`historical-evidence-${width}.png`), fullPage: true });

    await details.getByRole("button", { name: "查看 Jenkins 原始日志" }).click();
    await expect(panel.getByLabel("日志内容")).toContainText("Archived Jenkins output");
    await expect(panel.getByRole("combobox", { name: "过程日志", exact: true })).toHaveValue("original.log");
    await expect(panel.getByRole("link", { name: "下载完整脱敏日志" })).toHaveAttribute("href", /original\.log\/download$/);
    expect(logReads).toContain("/api/cicd-agent/repro-tasks/history-review/logs/original.log");
    await expectDesktopLayout(panel);

    await panel.getByRole("button", { name: "带入责任处理" }).click();
    await expect(dialog.getByLabel("最终责任归属")).toHaveValue("Code Owner");
    await expect(dialog.getByLabel("最终原因摘要")).toHaveValue(historicalTask.report!.diagnosis!.summary);
    await expect(dialog.getByLabel("最终处理建议")).toHaveValue(historicalTask.report!.diagnosis!.action_suggestion);
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });

  test(`insufficient historical evidence stays available for manual review at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const insufficient: ReproTask = { ...historicalTask, report: { ...historicalTask.report!,
      outcome: "evidence_insufficient", suggested_owner_account: "", arch: "",
      build_skipped_reason: "缺少完整的失败上下文，需要人工补充证据",
      log_review: { ...historicalTask.report!.log_review!, decision: "insufficient_evidence", evidence: [] },
      diagnosis: { ...historicalTask.report!.diagnosis!, suggested_role: "Unresolved",
        summary: "历史日志不完整，暂不能认定责任。", responsibility_reason: "缺少失败命令与完整上下文。",
        action_suggestion: "补充原始日志后由 RM 人工复核。", original_evidence: "Build terminated", limitations: ["历史证据不足"] },
    } };
    const { panel, writes, errors } = await mockReport(page, insufficient);
    await expect(panel.getByText("历史证据不足，待人工复核", { exact: true })).toBeVisible();
    await expect(panel.getByText("Unresolved / 待人工判断")).toBeVisible();
    await expect(panel.getByRole("button", { name: "带入责任处理" })).toHaveCount(0);
    await expect(panel.getByRole("status")).toHaveCount(0);
    await expect(panel.locator(".passed, .lucide-shield-check")).toHaveCount(0);
    await expectDesktopLayout(panel);
    await page.screenshot({ path: testInfo.outputPath(`insufficient-evidence-${width}.png`), fullPage: true });
    await panel.getByText("历史日志与认定证据", { exact: true }).click();
    await expect(panel.getByRole("row", { name: "构建验证 未执行", exact: true })).toBeVisible();
    await expect(panel.getByRole("row", { name: "基础镜像核对 未执行", exact: true })).toBeVisible();
    await expect(panel.getByText("未知", { exact: true })).toBeVisible();
    await expectDesktopLayout(panel);
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test("historical analysis precedes source preparation and node selection", async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  const analyzing: ReproTask = { ...historicalTask, status: "analyzing_history", report: null };
  const { panel, setTask, errors } = await mockReport(page, analyzing);
  await expect(panel.getByRole("status")).toContainText("历史日志分析中");
  await expect(panel.getByRole("status")).toContainText("正在分析 Jenkins 原始日志");
  await expect(panel.getByText("等待节点分配")).toHaveCount(0);
  await expectDesktopLayout(panel);
  setTask({ ...analyzing, status: "preparing" });
  await expect(panel.getByRole("status")).toContainText("准备源码与证据");
  setTask({ ...analyzing, status: "selecting_node" });
  await panel.getByRole("button", { name: "刷新状态" }).click();
  await expect(panel.getByRole("status")).toContainText("选择复现节点");
  setTask(historicalTask);
  await panel.getByRole("button", { name: "刷新状态" }).click();
  await expect(panel.getByText("历史日志诊断完成 · 未执行构建", { exact: true })).toBeVisible();
  await expect(panel.getByRole("status")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("stale historical reports cannot prefill responsibility", async ({ page }) => {
  const { panel, writes } = await mockReport(page, { ...historicalTask, stale: true });
  await expect(panel.getByText("责任认定已变化，此报告不能覆盖新的认定。")).toBeVisible();
  await expect(panel.getByRole("button", { name: "带入责任处理" })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("historical report prefill remains RM only", async ({ page }) => {
  const { panel, writes } = await mockReport(page, historicalTask, "SPD");
  await expect(panel.getByText("历史日志诊断完成 · 未执行构建", { exact: true })).toBeVisible();
  await expect(panel.getByRole("button", { name: "带入责任处理" })).toHaveCount(0);
  expect(writes).toEqual([]);
});
