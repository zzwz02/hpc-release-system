import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QaResultsPane } from "../QaResultsPane";
import { apiGet, apiPost } from "../../../api/http";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
let identity = { username: "qa", role: "QA" };
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: identity }) }));
const row = {
  app_name: "HPL", git_branch: "maca", app_version: "1", hpc_version: "3.10.0", arch: "x86", OS: "ubuntu22.04", GPU: "C500",
  commit_id: "abcdef1", test_name: "benchmark", test_result: "pass", test_digest: "sha256:digest", test_env: "C500_server_1",
  auto_command: "automatically generated docker command", docker_cmd: "QA uploaded docker command", command_error: "",
  report_id: 5, uploaded_at: "2026-11-15 12:30:00", uploaded_by: "qa", filename: "report.xlsx",
};
function renderPane() {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={query}><QaResultsPane /></QueryClientProvider>);
}
beforeEach(() => { vi.clearAllMocks(); identity = { username: "qa", role: "QA" }; });

it("shows automatic and Excel commands separately with the Excel submission time and original download", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row] });
  renderPane();
  const table = await screen.findByRole("table", { name: "QA 测试结果" });
  expect(within(table).getByText(row.auto_command)).toBeVisible();
  expect(within(table).getByText(row.docker_cmd)).toBeVisible();
  expect(within(table).getByText("2026-11-15 12:30:00")).toBeVisible();
  expect(within(table).getByText("3.10.0")).toBeVisible();
  expect(within(table).getByRole("link", { name: "report.xlsx" })).toHaveAttribute("href", "/api/qa/test-results/5/download");
});

it("uploads the actual Excel bytes, reports overwrite counts, and reloads the results", async () => {
  vi.mocked(apiGet).mockResolvedValueOnce({ total: 0, rows: [] }).mockResolvedValue({ total: 1, rows: [row] });
  vi.mocked(apiPost).mockResolvedValue({ rows: 1, replaced: 1, duplicate_rows: 0 });
  renderPane();
  await screen.findByRole("table", { name: "QA 测试结果" });
  const file = new File(["workbook-bytes"], "report.xlsx");
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [file] } });
  await screen.findByText("已保存 1 行，其中覆盖 1 行。");
  expect(apiPost).toHaveBeenCalledWith("/api/qa/test-results/upload", { filename: "report.xlsx", content_base64: btoa("workbook-bytes") });
  expect(await screen.findByText(row.docker_cmd)).toBeVisible();
});

it("keeps existing results visible when the upload fails validation", async () => {
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [row] });
  vi.mocked(apiPost).mockRejectedValue(new Error("第 2 行：digest 与 GPU 不匹配"));
  renderPane();
  await screen.findByText(row.docker_cmd);
  fireEvent.change(screen.getByLabelText("上传测试结果 Excel"), { target: { files: [new File(["bytes"], "report.xlsx")] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("第 2 行：digest 与 GPU 不匹配");
  expect(screen.getByText(row.docker_cmd)).toBeVisible();
});

it("allows Owners to query stored results without uploading and keeps upstream failures visible", async () => {
  identity = { username: "owner", role: "Owner" };
  vi.mocked(apiGet).mockResolvedValue({ total: 1, rows: [{ ...row, auto_command: "", command_error: "Harbor 查询失败" }] });
  renderPane();
  expect(await screen.findByText("Harbor 查询失败")).toBeVisible();
  expect(screen.queryByLabelText("上传测试结果 Excel")).toBeNull();
  fireEvent.change(screen.getByLabelText("查询 QA 结果"), { target: { value: "C500" } });
  fireEvent.click(screen.getByRole("button", { name: "查询结果" }));
  await waitFor(() => expect(apiGet).toHaveBeenLastCalledWith("/api/qa/test-results?search=C500&offset=0&limit=50"));
});
