import { test, expect } from "@playwright/test";

test.use({ baseURL: process.env.REPRO_UI_URL || "http://127.0.0.1:8033" });

for (const width of [1366, 1920]) {
  test(`reconsideration report and RM prefill at ${width}px`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const record = {
      id: 2188, job_name: "Hpc_Diffdock_Release", build_number: 1543, normalized_stage: "build image",
      owner_account: "m00859", owner_role: "Code Owner", code_owner: "m00859", official_name: "DiffDock",
      responsibility_status: "ai_suggested", reason_summary: "Python 固定依赖版本冲突",
      action_suggestion: "检查 protobuf 与 grpcio-tools 的兼容范围", created_at: "2026-10-14T19:12:00",
      build_url: "https://jenkins.example/job/Hpc_Diffdock_Release/1543/", responsibility_events: [],
    };
    const task = {
      id: "f".repeat(32), failure_id: 2188, status: "completed", requested_by: "hajiang", reason: "复现构建并核验根因",
      node_name: "owner-x86-133", created_at: "2026-10-16T09:00:00", stale: false, resolved_by: null, cancel_requested: false,
      history: [{ status: "diagnosing", at: "2026-10-16T09:01:00", detail: "" }],
      report: {
        outcome: "fix_verified", source_commit: "acc2e75e79fb71a2a838d2c3053e2cfa68c7e8af", arch: "amd64",
        verified: true, base_digests_match: true, environment_error: "",
        suggested_owner_account: "m00859", builds: [], patches: [{ patch: "- grpcio-tools==1.83.0\n+ grpcio-tools", verified: true }], diagnosis: {
          summary: "原错误已在未修改副本中重现；补丁副本已重新执行 dbg 构建，退出码 0，基础镜像 digest 与历史一致。",
          suggested_role: "Code Owner", responsibility_reason: "原日志显示 protobuf 与 grpcio-tools 固定版本不兼容。",
          action_suggestion: "评审补丁并固定已验证版本，完成 Triton 服务和应用运行测试后再发布。", original_evidence: "ERROR: Cannot install protobuf and grpcio-tools",
          reproduction_evidence: "ERROR: Cannot install protobuf and grpcio-tools", limitations: ["仅验证 dbg 构建，未执行 release 或完整功能测试。"],
        },
        verification: { passed: true, summary: "原错误已复现，补丁重新构建通过", original_failure_reproduced: true,
          scope: "仅包含 dbg；不代表完整流水线、release 发布或应用功能测试通过。", targets: ["dbg"],
          builds: [{ attempt: 0, target: "dbg", exit_code: 1, log: "build-0-1.log" },
            { attempt: 3, target: "dbg", exit_code: 0, log: "build-3-1.log", method: "docker buildx build", no_cache: true, pull: true }],
          installed_packages: [{ name: "grpcio-tools", version: "1.48.2" }, { name: "protobuf", version: "3.20.0" }],
        },
      },
    };
    let writes = 0;
    await page.context().route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      let data: unknown = {};
      if (route.request().method() !== "GET") writes++;
      if (path.endsWith("/download")) {
        await route.fulfill({ body: "complete redacted log", contentType: "text/plain", headers: { "Content-Disposition": "attachment; filename=repro-log.txt" } });
        return;
      }
      if (path.endsWith("/logs")) data = { logs: [{ name: "build-0-1.log", label: "未修改副本构建", bytes: 1234 }, { name: "build-3-1.log", label: "补丁副本构建", bytes: 5678 }] };
      else if (path.includes("/logs/")) data = { content: "#13 Successfully installed grpcio-tools-1.48.2 protobuf-3.20.0\n#27 [dbg 21/21] WORKDIR /workspace/tests\n#27 DONE 1.0s", start_line: 301, end_line: 600, total_lines: 600, previous_before: 300 };
      else if (path === "/api/me") data = { user: { username: "hajiang", display_name: "Haiyang", role: "RM" } };
      else if (path === "/api/state") data = { user_display_names: { m00859: "Jiazhen Gong" }, releases: [], apps: [] };
      else if (path.endsWith("/filter-options")) data = {};
      else if (path.endsWith("/summary")) data = { total_records: 1, groups: [] };
      else if (path.endsWith("/repro-tasks")) data = { tasks: [task], enabled: true, worker_online: true };
      else if (path.endsWith("/2188")) data = record;
      else if (path.endsWith("/failures")) data = { records: [record], total: 1, page: 1, page_size: 20, total_pages: 1 };
      await route.fulfill({ json: data });
    });
    await page.goto("/jenkins-failures?date_from=2026-10-14&date_to=2026-10-16");
    await page.getByRole("button", { name: "复议诊断", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "记录详情" });
    await expect(dialog.getByText("补丁构建验证通过", { exact: true })).toBeVisible();
    await expect(dialog.getByText("Agent 建议")).toBeVisible();
    const overflow = await dialog.evaluate((element) => element.scrollWidth > element.clientWidth + 1);
    expect(overflow).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`repro-${width}.png`), fullPage: true });
    await dialog.getByText("验证过程与认定证据", { exact: true }).click();
    await expect(dialog.getByText("docker buildx build --no-cache --pull")).toBeVisible();
    await dialog.getByRole("button", { name: "查看补丁构建日志" }).click();
    await expect(dialog.getByLabel("日志内容")).toContainText("Successfully installed");
    await dialog.getByLabel("日志内容").scrollIntoViewIfNeeded();
    const headerOffset = await dialog.locator(".cicd-agent-detail-dialog").evaluate((element) => {
      const header = element.querySelector(":scope > .section-head-row")!;
      return header.getBoundingClientRect().top - element.getBoundingClientRect().top;
    });
    expect(headerOffset).toBeLessThan(3);
    expect(await dialog.evaluate((element) => element.scrollWidth > element.clientWidth + 1)).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`repro-logs-${width}.png`), fullPage: true });
    const downloadPromise = page.waitForEvent("download");
    await dialog.getByRole("link", { name: "下载完整脱敏日志" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe("repro-log.txt");
    await dialog.getByRole("button", { name: "带入责任处理" }).click();
    await expect(dialog.getByLabel("最终责任归属")).toHaveValue("Code Owner");
    expect(writes).toBe(0);
    expect(errors).toEqual([]);
    await dialog.getByRole("button", { name: "关闭", exact: true }).click();
    await expect(dialog).not.toBeVisible();
  });
}
