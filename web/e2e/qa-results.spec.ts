import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { parseCsvRows } from "../src/lib/csv";
import { commandTableAppearance } from "./helpers/commandTableAppearance";

const base = process.env.QA_RESULTS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup --qa-commands and isolated Vite");
const image = "harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32);
const taggedImage = image.replace("@", ":3.9.0.27-3.10.1.2-ubuntu22.04-amd64@");

function workbook(columns: string[], rows: Record<string, unknown>[]) {
  return execFileSync("python", ["-c", `
import sys, io, json
from openpyxl import Workbook
payload = json.load(sys.stdin)
book = Workbook()
sheet = book.active
sheet.title = 'test_commands'
sheet.append(payload['columns'])
for row in payload['rows']:
    sheet.append([row.get(column, '') for column in payload['columns']])
env = book.create_sheet('C500_server_1')
env.merge_cells('A1:D3')
env['A1'] = '环境内容原样保留'
env['A4'] = 'OS'
env['B4'] = 'ubuntu22.04'
env['A5'] = 'Driver'
env['B5'] = '3.100.0'
env['A7'] = 'Zero'
env['B7'] = 0
env['B8'] = '=1+2'
output = io.BytesIO()
book.save(output)
sys.stdout.buffer.write(output.getvalue())
`], { input: JSON.stringify({ columns, rows }) });
}

test("HPC version worksheet CSV -> QA Excel validation -> report and fresh blank QA cells", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/qa?tab=release");
  await page.getByLabel("选择 release").selectOption({ label: "3.10.1" });
  await page.getByRole("button", { name: "加载报告", exact: true }).click();
  await expect(page.getByRole("columnheader", { name: "app_id", exact: true })).toBeVisible();
  await expect(page.getByRole("cell", { name: "lookup-image", exact: true })).toBeVisible();
  await page.goto(base + "/qa?tab=test");
  await page.getByLabel("选择 release").selectOption({ label: "3.10.1" });
  await page.getByLabel("hpc_version", { exact: true }).fill("latest");
  const commandsResponse = page.waitForResponse(r => r.url().includes("/api/qa/test-commands?"));
  await page.getByRole("button", { name: "查询测试命令", exact: true }).click();
  const response = await commandsResponse;
  expect(response.ok(), await response.text()).toBeTruthy();
  const commands = await response.json();
  expect(commands.hpc_version).toBe("latest");
  expect(commands.hpc_versions).toEqual(["3.10.1.2"]);
  const expectedColumns = ["app_id", "git_branch", "hpc_version", "app_version", "arch", "OS", "GPU", "py_label", "torch_label", "test_name", "perf_golden", "perf_unit", "website_docker_cmd", "qa_digest", "qa_docker_cmd", "qa_result", "qa_env", "qa_status", "qa_comment"];
  expect(commands.columns).toEqual(expectedColumns);
  expect(commands.rows.every((row: Record<string, unknown>) => row.app_id === "lookup-image" && !("app_name" in row))).toBe(true);
  const qaColumns = expectedColumns.filter(column => column.startsWith("qa_"));
  expect(commands.rows.every((row: Record<string, unknown>) => qaColumns.every(column => row[column] === ""))).toBe(true);
  expect(commands.rows.some((row: Record<string, unknown>) => String(row.website_docker_cmd).includes("<hpc_version>"))).toBe(true);
  expect(commands.rows.filter((row: Record<string, unknown>) => !String(row.website_docker_cmd).includes("<hpc_version>")).every((row: Record<string, unknown>) => String(row.website_docker_cmd).includes(taggedImage))).toBe(true);
  const commandsPane = page.locator("#qaTestCmdPane");
  await expect(commandsPane.getByRole("heading", { name: "测试命令", exact: true })).toHaveCount(1);
  await expect(commandsPane.getByPlaceholder("全表关键词筛选…")).toHaveCount(0);
  await expect(commandsPane.locator("form").getByRole("status")).toContainText("3.10.1.2");
  const commandBar = await commandsPane.locator(".qa-report-bar").boundingBox();
  const exportButton = await commandsPane.getByRole("button", { name: "导出 CSV", exact: true }).boundingBox();
  expect(Math.abs(commandBar!.x + commandBar!.width - exportButton!.x - exportButton!.width)).toBeLessThan(2);
  await page.screenshot({ path: "/tmp/qa-test-commands-layout-desktop.png", fullPage: true });
  const referenceAppearance = await commandTableAppearance(page.locator("#qaTestCmdPane"));
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV", exact: true }).click();
  const parsed = parseCsvRows(await readFile((await (await exported).path())!, "utf8"));
  expect(parsed[0]).toEqual(expectedColumns);
  expect(parsed.slice(1)).toHaveLength(commands.rows.length);
  for (const [index, row] of parsed.slice(1).entries()) {
    expect(row.slice(-6)).toEqual(["", "", "", "", "", ""]);
    expect(row[12]).toBe(commands.rows[index].website_docker_cmd);
  }

  let index = 0;
  const rows = commands.rows.map((row: Record<string, unknown>) => String(row.website_docker_cmd).includes("<hpc_version>") ? row : ({
    ...row, qa_result: index++ === 0 ? 0 : "pass", qa_digest: image,
    qa_docker_cmd: row.website_docker_cmd + " # QA Excel command", qa_env: "C500_server_1", qa_status: "pass", qa_comment: "中文备注",
    website_docker_cmd: "=1+2",
  }));
  const content = workbook(expectedColumns, rows);
  await page.getByRole("navigation", { name: "QA", exact: true }).getByRole("link", { name: "QA log", exact: true }).click();
  async function selectReport(name: string, buffer: Buffer) {
    await page.getByLabel("上传测试结果 Excel").setInputFiles({ name, mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer });
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("heading", { name: "整份替换 3.10.1 的 QA log", exact: true })).toBeVisible();
    await expect(dialog).toContainText(name);
    await expect(dialog).toContainText("含各补丁版本");
    await expect(dialog).toContainText("未出现在本次文件中的旧测试项也会删除");
    return dialog;
  }
  async function uploadReport(name: string, buffer: Buffer) {
    const dialog = await selectReport(name, buffer);
    await dialog.getByRole("button", { name: "确认整份替换", exact: true }).click();
  }
  const uploaded = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await uploadReport("QA-results.xlsx", content);
  const uploadResponse = await uploaded;
  expect(uploadResponse.ok(), await uploadResponse.text()).toBeTruthy();
  const saved = await uploadResponse.json();
  expect(saved.total_rows).toBe(commands.rows.length);
  expect(saved.release_name).toBe("3.10.1");
  expect(saved.release_test_count).toBe(commands.rows.length);
  expect(saved.total_rows).toBe(saved.rows + saved.skipped_rows + saved.duplicate_rows);
  expect(saved.skipped_rows).toBe(commands.rows.filter((row: Record<string, unknown>) => String(row.website_docker_cmd).includes("<hpc_version>")).length);
  expect(saved.skipped_apps).toEqual([{ app_id: "lookup-image", git_branch: "maca", rows: saved.skipped_rows }]);
  await expect(page.getByText("已保存 3.10.1 的 2 行，清除旧结果 0 行。", { exact: true })).toBeVisible();
  const uploadSummary = page.getByTestId("qa-upload-summary");
  await expect(uploadSummary).toContainText(`3.10.1 版本共 ${saved.release_test_count} 条测试；文件含 ${saved.total_rows} 条；保存 ${saved.rows} 条；跳过 ${saved.skipped_rows + saved.duplicate_rows} 条。`);
  await uploadSummary.locator("summary").click();
  await expect(uploadSummary).toContainText(`lookup-image · maca：${saved.skipped_rows} 行`);
  const report = page.getByRole("table", { name: "QA 测试结果", exact: true });
  await expect(report.locator("tbody tr")).toHaveCount(2);
  const reportPane = page.getByTestId("qa-results-pane");
  await expect(reportPane.getByPlaceholder("全表关键词筛选…")).toHaveCount(0);
  await expect(reportPane.getByRole("button", { name: "↻ 刷新", exact: true })).toHaveCount(0);
  await expect(reportPane.locator("form").getByRole("button")).toHaveText(["查询结果", "刷新结果", "导出 CSV"]);
  await page.screenshot({ path: "/tmp/qa-log-layout-desktop.png", fullPage: true });
  expect(await commandTableAppearance(reportPane)).toEqual(referenceAppearance);
  const environmentRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/environment?")) environmentRequests.push(request.url()); });
  const environment = report.getByRole("button", { name: "C500_server_1", exact: true }).first();
  await environment.hover();
  let preview = page.getByRole("tooltip");
  await expect(preview).toContainText("环境内容原样保留");
  await expect(preview).toContainText("ubuntu22.04");
  await expect(preview).toContainText("3.100.0");
  await expect(preview.getByRole("cell", { name: "0", exact: true })).toBeVisible();
  await expect(preview).toContainText("=1+2");
  const bounds = await preview.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({ path: "/tmp/qa-environment-preview.png", fullPage: true });
  await page.mouse.move(5, 5);
  await expect(preview).toHaveCount(0);
  await environment.focus();
  preview = page.getByRole("tooltip");
  await expect(preview).toContainText("ubuntu22.04");
  await environment.press("Escape");
  await expect(preview).toHaveCount(0);
  expect(environmentRequests).toHaveLength(1);
  await reportPane.getByLabel("qa_result 列筛选").fill("0");
  await expect(report.locator("tbody tr")).toHaveCount(1);
  await reportPane.getByLabel("qa_result 列筛选").fill("");
  const results = await (await page.request.get(base + `/api/qa/test-results?release_id=${new URL(response.url()).searchParams.get("release_id")}`)).json();
  expect(results.rows.map((row: Record<string, unknown>) => row.qa_result)).toEqual([0, "pass"]);
  for (const row of results.rows) {
    expect(row.qa_digest_gerrit_commit_id).toBe("bb46c60");
    expect(row.hpc_version).toBe("3.10.1.2");
    expect(row.website_docker_cmd).toContain(taggedImage);
    expect(row.website_docker_cmd).not.toContain("QA Excel command");
    expect(row.qa_docker_cmd).toContain("QA Excel command");
    expect(row.uploaded_at).toBe(saved.uploaded_at);
  }
  for (const name of ["website_docker_cmd", "qa_docker_cmd", "qa_status", "qa_comment", "py_label", "torch_label"]) {
    await expect(report.getByRole("columnheader", { name, exact: true })).toBeVisible();
  }
  const downloaded = page.waitForEvent("download");
  await report.getByRole("link", { name: "QA-results.xlsx", exact: true }).first().click();
  expect(await readFile((await (await downloaded).path())!)).toEqual(content);

  const filled = rows.filter((row: Record<string, unknown>) => row.qa_digest);
  for (const patch of [
    { app_id: "ABACUS" }, { GPU: "C500X" }, { git_branch: "wrong-branch" }, { hpc_version: "3.10.1.9" }, { qa_status: "pending" },
    { perf_golden: "incorrect" }, { perf_unit: "incorrect" },
    { qa_env: "missing" }, { qa_docker_cmd: `docker run ${image.split("@")[0]}:not-a-matching-tag sh -c test` },
  ]) {
    const invalid = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
    await uploadReport("invalid.xlsx", workbook(expectedColumns, [{ ...filled[0], ...patch }]));
    expect((await invalid).status()).toBe(400);
    await expect(page.getByRole("alert")).toContainText("第 2 行");
    await expect(report.locator("tbody tr")).toHaveCount(2);
  }
  let cancelledUpload = false;
  const watchUpload = (request: { url(): string }) => { if (request.url().endsWith("/api/qa/test-results/upload")) cancelledUpload = true; };
  page.on("request", watchUpload);
  const cancelled = await selectReport("cancelled.xlsx", workbook(expectedColumns, [filled[1]]));
  await cancelled.getByRole("button", { name: "取消上传", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.getByLabel("上传测试结果 Excel")).toBeEnabled();
  await expect(report.locator("tbody tr")).toHaveCount(2);
  await expect(report.getByRole("link", { name: "QA-results.xlsx", exact: true })).toHaveCount(2);
  expect((await page.request.get(base + `/api/qa/test-results/${saved.report_id}/download`)).ok()).toBeTruthy();
  page.off("request", watchUpload);
  expect(cancelledUpload).toBe(false);
  const overwrite = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await uploadReport("QA-results-updated.xlsx", workbook(expectedColumns, filled.map((row: Record<string, unknown>) => ({ ...row, qa_result: "fail", qa_status: "fail" }))));
  expect((await overwrite).ok()).toBeTruthy();
  await expect(page.getByText("已保存 3.10.1 的 2 行，清除旧结果 2 行。", { exact: true })).toBeVisible();
  const qaTabs = page.getByRole("navigation", { name: "QA", exact: true });
  await qaTabs.getByRole("link", { name: "QA report", exact: true }).click();
  await expect(page.getByTestId("qa-failure-summary")).toContainText("2 条失败 / 2 条结果");
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  const qaCard = page.locator('[data-qa-app="lookup-image"]');
  const appFailures = qaCard.getByTestId("qa-app-failures-lookup-image");
  await expect(qaCard.locator(".qa-card-b > :first-child")).toContainText("QA 状态");
  await expect(appFailures).toHaveJSProperty("open", false);
  await appFailures.locator("summary").click();
  await expect(appFailures).toHaveJSProperty("open", true);
  await expect(qaCard.getByTestId("qa-app-failures-lookup-image")).toContainText("中文备注");
  await expect(qaCard.getByTestId("qa-app-failures-lookup-image")).toContainText("HPC 3.10.1.2");
  for (const status of ["has_issues", "cannot_release"]) {
    await page.getByRole("button", { name: /修改（填写 QA 结论/ }).first().click();
    await qaCard.getByTestId("qa-status-lookup-image").selectOption(status);
    await expect(qaCard.getByRole("textbox", { name: "ABACUS QA comment", exact: true })).toHaveValue("");
    await expect(qaCard.getByText("QA comment (可选)", { exact: true })).toBeVisible();
    const emptyCommentSave = page.waitForResponse(r => r.url().endsWith("/api/qa/status-batch"));
    await page.getByRole("button", { name: "保存 QA 状态", exact: true }).first().click();
    expect((await emptyCommentSave).ok()).toBeTruthy();
    await expect(qaCard.getByTestId("qa-status-lookup-image")).toHaveValue(status);
    const currentRelease = await page.getByLabel("选择 release").inputValue();
    const persisted = await (await page.request.get(base + `/api/state?release_id=${currentRelease}`)).json();
    const entry = persisted.release.entries.find((row: { app_id: string }) => row.app_id === "lookup-image");
    expect(entry.qa_status).toBe(status);
    expect(entry.qa_note).toBe("");
  }
  await page.getByRole("button", { name: /修改（填写 QA 结论/ }).first().click();
  await qaCard.getByTestId("qa-status-lookup-image").selectOption("qa_passed");
  await qaCard.getByRole("textbox", { name: "ABACUS QA comment", exact: true }).fill("QA 手工确认：保留此 App comment");
  const commentSave = page.waitForResponse(r => r.url().endsWith("/api/qa/status-batch"));
  await page.getByRole("button", { name: "保存 QA 状态", exact: true }).first().click();
  const savedComment = await commentSave;
  expect(savedComment.ok(), await savedComment.text()).toBeTruthy();
  const savedState = await (await page.request.get(base + `/api/state?release_id=${saved.release_id}`)).json();
  expect(savedState.release.entries.find((entry: { app_id: string }) => entry.app_id === "lookup-image").qa_note).toBe("QA 手工确认：保留此 App comment");
  await expect(qaCard.getByRole("textbox", { name: "ABACUS QA comment", exact: true })).toBeDisabled();
  await page.screenshot({ path: "/tmp/qa-log-failures-desktop.png", fullPage: true });
  await qaTabs.getByRole("link", { name: "QA log", exact: true }).click();
  const replacement = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await expect(page.getByLabel("上传测试结果 Excel")).toBeEnabled();
  await uploadReport("only-B.xlsx", workbook(expectedColumns, [filled[1]]));
  expect((await replacement).ok()).toBeTruthy();
  await expect(page.getByText("已保存 3.10.1 的 1 行，清除旧结果 2 行。", { exact: true })).toBeVisible();
  await expect(report.locator("tbody tr")).toHaveCount(1);
  await expect(report.getByRole("link", { name: "only-B.xlsx", exact: true })).toBeVisible();
  expect((await page.request.get(base + `/api/qa/test-results/${saved.report_id}/download`)).status()).toBe(404);
  await expect(page.getByLabel("选择 release")).toHaveValue(new URL(response.url()).searchParams.get("release_id")!);
  await page.getByLabel("选择 release").selectOption({ label: '3.9.0' });
  await expect(page.getByText('0 行', { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'only-B.xlsx', exact: true })).toHaveCount(0);
  await page.getByLabel("选择 release").selectOption({ label: '3.10.1' });
  await expect(report.getByRole('link', { name: 'only-B.xlsx', exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/qa-version-report-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("navigation", { name: "QA", exact: true }).getByRole("link", { name: "测试命令", exact: true }).click();
  const fresh = page.waitForResponse(r => r.url().includes("/api/qa/test-commands?"));
  await page.getByRole("button", { name: "查询测试命令", exact: true }).click();
  const freshData = await (await fresh).json();
  expect(freshData.rows.every((row: Record<string, unknown>) => qaColumns.every(column => row[column] === ""))).toBe(true);
  await page.screenshot({ path: "/tmp/qa-version-commands-desktop.png", fullPage: true });
  await qaTabs.getByRole("link", { name: "QA report", exact: true }).click();
  await expect(page.getByTestId("qa-failure-summary")).toContainText("0 条失败 / 1 条结果");
  await expect(qaCard.getByRole("textbox", { name: "ABACUS QA comment", exact: true })).toHaveValue("QA 手工确认：保留此 App comment");
  expect(errors).toEqual([]);
});
