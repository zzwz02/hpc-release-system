import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QaTestCommandsPane } from "../QaTestCommandsPane";
import { apiGet } from "../../../api/http";
import { downloadCsv } from "../../../lib/csv";
import type { CommandRow } from "../../../components/FlatCommandTable";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn() }));
vi.mock("../../../lib/csv", () => ({ downloadCsv: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "qa", role: "QA" } }) }));
const data = { hpc_version: "latest", hpc_versions: ["3.10.0.8", "3.10.0.12"],
  columns: ["app_id", "hpc_version", "GPU", "py_label", "torch_label", "website_docker_cmd", "qa_digest", "qa_result"],
  rows: [{ app_id: "EquiformerV2", hpc_version: "3.10.0.8", website_docker_cmd: "older app command", qa_digest: "", qa_result: "" },
    { app_id: "HPL", hpc_version: "3.10.0.12", website_docker_cmd: "newer app command", qa_digest: "", qa_result: "" },
    { app_id: "Missing", hpc_version: "", website_docker_cmd: "docker run missing-maca:1.0-<hpc_version>-ubuntu22.04-amd64 sh -c 'test'", qa_digest: "", qa_result: "" }], errors: [] };
beforeEach(() => vi.clearAllMocks());

it("queries latest manually and displays independently selected versions alongside missing builds", async () => {
  vi.mocked(apiGet).mockResolvedValue(data);
  render(<QaTestCommandsPane releaseId="release1" />);
  expect(apiGet).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("hpc_version")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByText("docker run missing-maca:1.0-<hpc_version>-ubuntu22.04-amd64 sh -c 'test'")).toBeVisible();
  expect(screen.getByText("已按 App／构建分别选择最新版本；实际 hpc_version：3.10.0.8、3.10.0.12")).toBeVisible();
  expect(screen.getByText("EquiformerV2")).toBeVisible();
  expect(screen.getByText("3.10.0.8")).toBeVisible();
  expect(screen.getByText("3.10.0.12")).toBeVisible();
  expect(apiGet).toHaveBeenCalledWith("/api/qa/test-commands?release_id=release1&hpc_version=latest");
  fireEvent.click(screen.getByRole("button", { name: "↻ 刷新" }));
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  expect(apiGet).toHaveBeenLastCalledWith("/api/qa/test-commands?release_id=release1&hpc_version=latest");
});

it("exports displayed columns and merged GPUs without a QA template button", async () => {
  const base = { ...data.rows[0], py_label: "py310", torch_label: "torch2.4" };
  const rows: CommandRow[] = ["C500", "C600", "X301"].map(GPU => ({ ...base, GPU }));
  rows.push({ ...rows[0], py_label: "py312" });
  rows.push({ ...rows[0], website_docker_cmd: "different command" });
  rows.push({ ...rows[0], app_id: "different-app" });
  vi.mocked(apiGet).mockResolvedValue({ ...data, rows });
  render(<QaTestCommandsPane releaseId="release1" />);
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByRole("cell", { name: "C500, C600, X301" })).toBeVisible();
  expect(screen.getAllByRole("row")).toHaveLength(6); // Two headers and four distinct commands/builds/Apps.
  expect(screen.queryByRole("columnheader", { name: "py_label" })).not.toBeInTheDocument();
  expect(screen.queryByRole("columnheader", { name: "torch_label" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  const displayedColumns = data.columns.filter(column => !["py_label", "torch_label"].includes(column));
  const displayedRows = [{ ...rows[0], GPU: "C500, C600, X301" }, ...rows.slice(3)];
  expect(downloadCsv).toHaveBeenLastCalledWith("test_commands_latest.csv", displayedColumns,
    displayedRows.map(row => displayedColumns.map(column => String(row[column] ?? ""))));
  expect(screen.queryByRole("button", { name: "下载 QA 回填模板" })).not.toBeInTheDocument();
  expect(screen.queryByText(/填写 QA log 请下载 QA 回填模板/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("GPU 列筛选"), { target: { value: "C600" } });
  expect(screen.getAllByRole("row")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  expect(downloadCsv).toHaveBeenLastCalledWith("test_commands_latest.csv", displayedColumns,
    [displayedColumns.map(column => String(rows[1][column] ?? ""))]);
});

it("displays and exports placeholder commands when latest has no available images", async () => {
  const missing = { ...data, hpc_versions: [], rows: [data.rows[2]] };
  vi.mocked(apiGet).mockResolvedValue(missing);
  render(<QaTestCommandsPane releaseId="release1" />);
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByText("未找到可用镜像，已显示占位符命令")).toBeVisible();
  expect(screen.getByText(missing.rows[0].website_docker_cmd)).toBeVisible();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  const visibleColumns = missing.columns.filter(column => !["py_label", "torch_label"].includes(column));
  expect(downloadCsv).toHaveBeenCalledWith("test_commands_latest.csv", visibleColumns,
    missing.rows.map(row => visibleColumns.map(column => String((row as CommandRow)[column] ?? ""))));
});

it("shows an empty scope message when the release has no test commands", async () => {
  vi.mocked(apiGet).mockResolvedValue({ ...data, hpc_versions: [], rows: [] });
  render(<QaTestCommandsPane releaseId="release1" />);
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByText("当前发布周期没有测试命令")).toBeVisible();
});

it("clears old results on failure and discards a response after changing release", async () => {
  vi.mocked(apiGet).mockResolvedValueOnce(data).mockRejectedValueOnce(new Error("Harbor 查询失败"));
  const view = render(<QaTestCommandsPane releaseId="release1" />);
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  await screen.findByText("docker run missing-maca:1.0-<hpc_version>-ubuntu22.04-amd64 sh -c 'test'");
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Harbor 查询失败");
  expect(screen.queryByRole("table")).toBeNull();
  let resolve: (value: unknown) => void = () => {};
  vi.mocked(apiGet).mockImplementation(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  view.rerender(<QaTestCommandsPane releaseId="release2" />);
  resolve(data);
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(apiGet).toHaveBeenLastCalledWith("/api/qa/test-commands?release_id=release2&hpc_version=latest");
});
