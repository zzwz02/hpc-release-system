import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QaResultsPane } from "../QaResultsPane";
import { apiGet, apiGetBlob, apiPost } from "../../../api/http";
import { triggerBlobDownload } from "../../../lib/csv";
import { confirmDialog } from "../../../lib/confirm";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiGetBlob: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../lib/csv", () => ({ triggerBlobDownload: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
let identity = { username: "qa", role: "QA" };
let selectedRelease = { id: "rel-3100", name: "3.10.0" };
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: identity }) }));
const row = {
  app_name: "HPL", git_branch: "maca", app_version: "1", hpc_version: "3.10.0", arch: "x86", OS: "ubuntu22.04", GPU: "C500",
  commit_id: "abcdef1", test_name: "benchmark", qa_result: "pass", qa_digest: "sha256:digest", qa_env: "C500_server_1",
  website_docker_cmd: "automatically generated docker command", qa_docker_cmd: "QA uploaded docker command", command_error: "",
  report_id: 5, uploaded_at: "2026-11-15 12:30:00", uploaded_by: "qa", filename: "report.xlsx",
};
function renderPane() {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const element = () => <QueryClientProvider client={query}><QaResultsPane releaseId={selectedRelease.id} releaseName={selectedRelease.name} /></QueryClientProvider>;
  const view = render(element());
  return { ...view, rerenderPane: () => view.rerender(element()) };
}
beforeEach(() => {
  vi.clearAllMocks(); identity = { username: "qa", role: "QA" }; selectedRelease = { id: "rel-3100", name: "3.10.0" };
  vi.mocked(confirmDialog).mockResolvedValue(true);
});

it("shows automatic and Excel commands separately with the Excel submission time and original download", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name", "hpc_version", "qa_result", "qa_digest", "qa_env", "website_docker_cmd", "qa_docker_cmd"] });
  renderPane();
  const table = await screen.findByRole("table", { name: "QA 测试结果" });
  expect(screen.getByText(/不采用上传文件中该列的命令/)).toBeVisible();
  expect(screen.getByText(/逐项校验前 12 列.*perf_golden.*perf_unit/)).toBeVisible();
  expect(within(table).getByText(row.website_docker_cmd)).toBeVisible();
  expect(within(table).getByText(row.qa_docker_cmd)).toBeVisible();
  expect(within(table).getByText("2026-11-15 12:30:00")).toBeVisible();
  expect(within(table).getByText("3.10.0")).toBeVisible();
  expect(within(table).getByRole("link", { name: "report.xlsx" })).toHaveAttribute("href", "/api/qa/test-results/5/download");
});

it("filters all results, keeps inputs mounted, resets pagination, and exports the entire match", async () => {
  const columns = ["app_name", "GPU", "test_name", "qa_status"];
  const later = { ...row, app_name: "Later App", GPU: "X301", test_name: "case64", qa_status: "fail" };
  vi.mocked(apiGet).mockImplementation(async path => {
    const params = new URL(String(path), "http://test").searchParams;
    const filtered = JSON.parse(params.get("column_filters") ?? "{}");
    return { total: filtered.GPU ? 25 : 75, columns,
      rows: filtered.GPU || params.get("offset") === "50" ? [later] : Array.from({ length: 50 }, (_, i) => ({ ...row, test_name: `case${i}` })) };
  });
  const blob = new Blob(["all 25 matching rows"], { type: "text/csv" });
  vi.mocked(apiGetBlob).mockResolvedValue(blob);
  renderPane();
  await screen.findByText("case0");
  expect(screen.queryByText("Later App")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByText("Later App");
  const input = screen.getByLabelText("GPU 列筛选");
  input.focus();
  fireEvent.change(input, { target: { value: "X301" } });
  await waitFor(() => expect(screen.getByText("共 25 行，当前 1–25")).toBeVisible());
  expect(screen.getByLabelText("GPU 列筛选")).toBe(input);
  expect(input).toHaveFocus();
  const params = new URL(vi.mocked(apiGet).mock.lastCall![0], "http://test").searchParams;
  expect(JSON.parse(params.get("column_filters")!)).toEqual({ GPU: "X301" });
  expect(params.get("offset")).toBe("0");
  fireEvent.click(screen.getByRole("columnheader", { name: "test_name" }));
  await waitFor(() => {
    const current = new URL(vi.mocked(apiGet).mock.lastCall![0], "http://test").searchParams;
    expect(current.get("sort_by")).toBe("test_name");
  });
  await waitFor(() => expect(screen.getByRole("button", { name: "导出 CSV" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  await waitFor(() => expect(triggerBlobDownload).toHaveBeenCalledWith("qa_test_results.csv", blob));
  const url = new URL(vi.mocked(apiGetBlob).mock.lastCall![0], "http://test");
  expect(url.pathname).toBe("/api/qa/test-results/export");
  expect(url.searchParams.get("release_id")).toBe("rel-3100");
  expect(JSON.parse(url.searchParams.get("column_filters")!)).toEqual({ GPU: "X301" });
  expect(url.searchParams.get("sort_by")).toBe("test_name");
  expect(url.searchParams.has("offset") || url.searchParams.has("limit")).toBe(false);
});

it("sends table keywords to the server and lets users correct filters after a request fails", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name", "qa_result"] });
  renderPane();
  await screen.findByText("HPL");
  vi.mocked(apiGet).mockRejectedValueOnce(new Error("查询失败"));
  fireEvent.change(screen.getByLabelText("筛选QA 测试结果"), { target: { value: "outside first page" } });
  expect(await screen.findByRole("alert")).toHaveTextContent("查询失败");
  const input = screen.getByLabelText("筛选QA 测试结果");
  expect(input).toHaveValue("outside first page");
  expect(new URL(vi.mocked(apiGet).mock.lastCall![0], "http://test").searchParams.get("filter")).toBe("outside first page");
  fireEvent.change(input, { target: { value: "HPL" } });
  expect(await screen.findByText("HPL")).toBeVisible();
});

it("does not retain a previous user's rows while the new user's results load", async () => {
  vi.mocked(apiGet).mockResolvedValueOnce({ total: 1, rows: [row], columns: ["app_name"] })
    .mockImplementation(() => new Promise(() => {}));
  const view = renderPane();
  await screen.findByText("HPL");
  identity = { username: "another", role: "Owner" };
  view.rerenderPane();
  await waitFor(() => expect(screen.queryByText("HPL")).toBeNull());
});

it("uploads the actual Excel bytes, reports overwrite counts, and reloads the results", async () => {
  vi.mocked(apiGet).mockResolvedValueOnce({ total: 0, rows: [], columns: [] }).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name", "hpc_version", "qa_result", "qa_digest", "qa_env", "website_docker_cmd", "qa_docker_cmd"] });
  vi.mocked(apiPost).mockResolvedValue({ rows: 1, replaced: 1, duplicate_rows: 0, total_rows: 33, skipped_rows: 32,
    skipped_apps: [{ app_name: "LAMMPS", git_branch: "master", rows: 32 }] });
  renderPane();
  await screen.findByRole("table", { name: "QA 测试结果" });
  const file = new File(["workbook-bytes"], "report.xlsx");
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [file] } });
  await screen.findByText("已保存 3.10.0 的 1 行，清除旧结果 1 行。");
  const summary = screen.getByTestId("qa-upload-summary");
  expect(summary).toHaveTextContent("文件共 33 行；QA 列全空跳过 32 行，跳过行未校验、未入库。");
  fireEvent.click(within(summary).getByText("查看跳过明细（1 个 App／分支）"));
  expect(within(summary).getByText("LAMMPS · master：32 行")).toBeVisible();
  expect(apiPost).toHaveBeenCalledWith("/api/qa/test-results/upload", { filename: "report.xlsx", content_base64: btoa("workbook-bytes"), release_id: "rel-3100" });
  expect(await screen.findByText(row.qa_docker_cmd)).toBeVisible();
});

it("keeps existing results visible when the upload fails validation", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name", "hpc_version", "qa_result", "qa_digest", "qa_env", "website_docker_cmd", "qa_docker_cmd"] });
  vi.mocked(apiPost).mockRejectedValue(new Error("第 2 行：digest 与 GPU 不匹配"));
  renderPane();
  await screen.findByText(row.qa_docker_cmd);
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [new File(["bytes"], "report.xlsx")] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("第 2 行：digest 与 GPU 不匹配");
  expect(screen.getByText(row.qa_docker_cmd)).toBeVisible();
});

it("warns about replacing the entire selected cycle and preserves its report when cancelled", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name"] });
  vi.mocked(confirmDialog).mockResolvedValueOnce(false);
  renderPane();
  await screen.findByText("HPL");
  expect(screen.getByText(/上传成功将清空 3.10.0 周期的全部旧结果/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [new File(["bytes"], "only-B.xlsx")] } });
  await waitFor(() => expect(screen.getByLabelText("上传测试结果 Excel")).toBeEnabled());
  expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({
    title: "整份替换 3.10.0 的 QA report", danger: true, confirmText: "确认整份替换", cancelText: "取消上传",
  }));
  const body = vi.mocked(confirmDialog).mock.lastCall![0].body;
  expect(body).toContain("only-B.xlsx");
  expect(body).toContain("含各补丁版本");
  expect(body).toContain("未出现在本次文件中的旧测试项也会删除");
  expect(apiPost).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "report.xlsx" })).toHaveAttribute("href", "/api/qa/test-results/5/download");
  expect(screen.getByText("HPL")).toBeVisible();
});

it("does not upload after the selected cycle changes while confirmation is open", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name"] });
  let confirm!: (value: boolean) => void;
  vi.mocked(confirmDialog).mockImplementationOnce(() => new Promise(resolve => { confirm = resolve; }));
  const view = renderPane();
  await screen.findByText("HPL");
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [new File(["bytes"], "only-B.xlsx")] } });
  selectedRelease = { id: "rel-390", name: "3.9.0" };
  view.rerenderPane();
  confirm(true);
  await waitFor(() => expect(screen.getByLabelText("上传测试结果 Excel")).toBeEnabled());
  expect(apiPost).not.toHaveBeenCalled();
});

it("allows Owners to query stored results without uploading and keeps upstream failures visible", async () => {
  identity = { username: "owner", role: "Owner" };
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [{ ...row, website_docker_cmd: "", command_error: "Harbor 查询失败" }], columns: ["app_name", "website_docker_cmd", "qa_docker_cmd"] });
  renderPane();
  expect(await screen.findByText("Harbor 查询失败")).toBeVisible();
  expect(screen.queryByLabelText("上传测试结果 Excel")).toBeNull();
  fireEvent.change(screen.getByLabelText("查询 QA 结果"), { target: { value: "C500" } });
  fireEvent.click(screen.getByRole("button", { name: "查询结果" }));
  await waitFor(() => expect(apiGet).toHaveBeenLastCalledWith("/api/qa/test-results?release_id=rel-3100&search=C500&offset=0&limit=50"));
});


it("queries the selected cycle and clears rows and filters when that cycle changes", async () => {
  vi.mocked(apiGet).mockImplementation(async path => {
    const id = new URL(String(path), "http://test").searchParams.get("release_id");
    return { total: 1, rows: [{ ...row, app_name: id === "rel-3100" ? "Cycle 3.10.0" : "Cycle 3.9.0" }], columns: ["app_name"] };
  });
  const view = renderPane();
  await screen.findByText("Cycle 3.10.0");
  fireEvent.change(screen.getByLabelText("app_name 列筛选"), { target: { value: "old filter" } });
  selectedRelease = { id: "rel-390", name: "3.9.0" };
  view.rerenderPane();
  await screen.findByText("Cycle 3.9.0");
  expect(screen.queryByText("Cycle 3.10.0")).toBeNull();
  expect(screen.getByLabelText("app_name 列筛选")).toHaveValue("");
  const params = new URL(vi.mocked(apiGet).mock.lastCall![0], "http://test").searchParams;
  expect(params.get("release_id")).toBe("rel-390");
  expect(params.has("column_filters")).toBe(false);
});

it("uploads with the selected cycle and the revision of the displayed report", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row], columns: ["app_name"], revision: "current-report-revision" });
  vi.mocked(apiPost).mockResolvedValue({ rows: 1, replaced: 4, duplicate_rows: 0, total_rows: 1, skipped_rows: 0, skipped_apps: [] });
  renderPane();
  await screen.findByText("HPL");
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [new File(["bytes"], "B.xlsx")] } });
  await screen.findByText("已保存 3.10.0 的 1 行，清除旧结果 4 行。");
  expect(screen.getByTestId("qa-upload-summary")).toHaveTextContent("QA 列全空跳过 0 行");
  expect(screen.queryByText(/查看跳过明细/)).toBeNull();
  expect(apiPost).toHaveBeenCalledWith("/api/qa/test-results/upload", {
    filename: "B.xlsx", content_base64: btoa("bytes"), release_id: "rel-3100", revision: "current-report-revision",
  });
});
