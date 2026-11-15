import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { parseCsvRows } from "../src/lib/csv";
import { commandTableAppearance } from "./helpers/commandTableAppearance";

const base = process.env.QA_RESULTS_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site --lookup and isolated Vite");
const image = "harbor.sh.mxcr.io/hpc-release/abacus-maca@sha256:" + "8d".repeat(32);

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
env['B8'] = '=1+2'
output = io.BytesIO()
book.save(output)
sys.stdout.buffer.write(output.getvalue())
`], { input: JSON.stringify({ columns, rows }) });
}

test("batch digest CSV -> QA Excel validation and overwrite -> historical result reuse", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  expect((await page.request.post(base + "/api/login", { data: { username: "app_qa", password: "test-password" } })).ok()).toBeTruthy();
  await page.goto(base + "/qa?tab=test");
  await page.getByRole("button", { name: "加载报告", exact: true }).click();
  await expect(page.locator("#qaTestCmdPane table tbody td.cmd").first()).toBeVisible();
  const referenceAppearance = await commandTableAppearance(page.locator("#qaTestCmdPane"));
  await page.goto(base + "/lookup");
  await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
  await page.getByLabel("上传镜像清单").setInputFiles({ name: "images.txt", mimeType: "text/plain", buffer: Buffer.from(`${image}\ndocker pull ${image}\n${image.replace("8d".repeat(32), "e".repeat(64))}`) });
  await expect(page.getByLabel("完整镜像名", { exact: true })).toHaveValue(`${image}\n${image}\n${image.replace("8d".repeat(32), "e".repeat(64))}`);
  const batchResponse = page.waitForResponse(r => r.url().endsWith("/api/lookup/images"));
  await page.getByTestId("lookup-search").click();
  const batch = await (await batchResponse).json();
  expect(batch.duplicate_count).toBe(1);
  expect(batch.errors).toHaveLength(1);
  expect(batch.rows).toHaveLength(2);
  expect(batch.rows.every((row: Record<string, unknown>) => row.GPU === "C500")).toBe(true);
  await expect(page.getByRole("alert")).toContainText("未找到该镜像");
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出 CSV", exact: true }).click();
  const csv = await readFile((await (await exported).path())!, "utf8");
  const parsed = parseCsvRows(csv);
  expect(parsed[0]).toEqual(batch.columns);
  expect(parsed.slice(1)).toHaveLength(2);
  expect(parsed[0].slice(-3)).toEqual(["test_result", "test_digest", "test_env"]);
  expect(parsed[1].slice(-3)).toEqual(["", "", ""]);

  const rows = batch.rows.map((row: Record<string, unknown>, index: number) => ({
    ...row, test_result: index === 0 ? 0 : "pass", test_digest: index === 0 ? "8d".repeat(32) : image,
    test_env: "C500_server_1", commit_id: "forged", hpc_version: "forged",
    docker_cmd: row.docker_cmd + " # QA Excel command",
  }));
  const columns = [...batch.columns, "hpc_version"];
  const content = workbook(columns, rows);
  await page.getByRole("navigation", { name: "QA", exact: true }).getByRole("link", { name: "QA report", exact: true }).click();
  await expect(page.getByTestId("qa-results-pane")).toBeVisible();
  const uploaded = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await page.getByLabel("上传测试结果 Excel").setInputFiles({ name: "QA-results.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: content });
  const uploadResponse = await uploaded;
  expect(uploadResponse.ok(), await uploadResponse.text()).toBeTruthy();
  const saved = await uploadResponse.json();
  await expect(page.getByText("已保存 2 行，其中覆盖 0 行。", { exact: true })).toBeVisible();
  const report = page.getByRole("table", { name: "QA 测试结果", exact: true });
  await expect(report.locator("tbody tr")).toHaveCount(2);
  const reportPane = page.getByTestId("qa-results-pane");
  expect(await commandTableAppearance(reportPane)).toEqual(referenceAppearance);
  await reportPane.locator(".report-table").screenshot({ path: "/tmp/qa-report-table-aligned.png" });
  await reportPane.getByLabel("test_result 列筛选").fill("0");
  await expect(report.locator("tbody tr")).toHaveCount(1);
  await expect(report.getByText("0", { exact: true })).toBeVisible();
  await reportPane.getByLabel("test_result 列筛选").fill("pass");
  await expect(report.locator("tbody tr")).toHaveCount(1);
  const filteredExport = page.waitForEvent("download");
  await reportPane.getByRole("button", { name: "导出 CSV", exact: true }).click();
  const filteredRows = parseCsvRows(await readFile((await (await filteredExport).path())!, "utf8"));
  expect(filteredRows).toHaveLength(2);
  expect(filteredRows[1][filteredRows[0].indexOf("test_result")]).toBe("pass");
  await reportPane.getByLabel("test_result 列筛选").fill("");
  await expect(report.locator("tbody tr")).toHaveCount(2);
  const resultsResponse = await page.request.get(base + "/api/qa/test-results");
  const results = await resultsResponse.json();
  expect(results.rows[0].test_result).toBe(0);
  expect(results.rows[1].test_result).toBe("pass");
  for (const row of results.rows) {
    expect(row.commit_id).toBe("bb46c60");
    expect(row.hpc_version).toBe("3.10.1.2");
    expect(row.auto_command).toContain(image);
    expect(row.auto_command).not.toContain("QA Excel command");
    expect(row.docker_cmd).toContain("QA Excel command");
    expect(row.uploaded_at).toBe(saved.uploaded_at);
  }
  await expect(report.getByRole("columnheader", { name: "自动生成的命令", exact: true })).toBeVisible();
  await expect(report.getByRole("columnheader", { name: "QA Excel 测试命令", exact: true })).toBeVisible();
  await expect(report.getByText(saved.uploaded_at, { exact: true }).first()).toBeVisible();
  const downloaded = page.waitForEvent("download");
  await report.getByRole("link", { name: "QA-results.xlsx", exact: true }).first().click();
  expect(await readFile((await (await downloaded).path())!)).toEqual(content);

  const rejected = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await page.getByLabel("上传测试结果 Excel").setInputFiles({ name: "wrong-GPU.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: workbook(columns, [{ ...rows[0], GPU: "C500X" }]) });
  expect((await rejected).status()).toBe(400);
  await expect(page.getByRole("alert")).toContainText("第 2 行");
  await expect(page.getByRole("alert")).toContainText("不匹配");
  await expect(report.locator("tbody tr")).toHaveCount(2);
  const overwrite = page.waitForResponse(r => r.url().endsWith("/api/qa/test-results/upload"));
  await page.getByLabel("上传测试结果 Excel").setInputFiles({ name: "QA-results-updated.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: workbook(columns, rows.map((row: Record<string, unknown>, index: number) => ({ ...row, test_result: index === 0 ? "fail" : "pass" }))) });
  expect((await overwrite).ok()).toBeTruthy();
  await expect(page.getByText("已保存 2 行，其中覆盖 2 行。", { exact: true })).toBeVisible();
  await expect(report.getByText("fail", { exact: true })).toBeVisible();
  await page.screenshot({ path: "/tmp/qa-results-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/qa-results-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });

  await page.getByRole("navigation", { name: "QA", exact: true }).getByRole("link", { name: "历史查询", exact: true }).click();
  await page.getByRole("button", { name: "按镜像 digest", exact: true }).click();
  await page.getByLabel("完整镜像名", { exact: true }).fill(image);
  const reused = page.waitForResponse(r => r.url().endsWith("/api/lookup/images"));
  await page.getByTestId("lookup-search").click();
  const reusedData = await (await reused).json();
  expect(reusedData.rows.map((row: Record<string, unknown>) => row.test_result)).toEqual(["fail", "pass"]);
  expect(reusedData.rows.every((row: Record<string, unknown>) => row.test_env === "C500_server_1")).toBe(true);
  await expect(page.getByRole("table", { name: "测试命令", exact: true }).getByText("fail", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
