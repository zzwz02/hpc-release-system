import { fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { CompatibilityPane } from "../CompatibilityPane";
import { apiGet } from "../../../api/http";
import { downloadCsv } from "../../../lib/csv";

vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "qa", role: "QA" } }) }));
vi.mock("../../../api/http", () => ({ apiGet: vi.fn() }));
vi.mock("../../../lib/csv", () => ({ downloadCsv: vi.fn() }));

const base = {
  app_id: "pyg", app_name: "PyG", owners: ["owner"], app_version: "0.4", hpc_version: "3.10.0",
  app_info_commit_id: "a".repeat(40), manifest_source: "sealed", scope_pending: true, note: "实时生成",
  os: "ubuntu22.04", arch: "amd64", python_label: "py310", pytorch_label: "torch2.4", image_target: "release",
  path: "test", command: "docker run image:tag@sha256:digest sh -c test", delivery_reference: "image:tag@sha256:digest",
};
const rows = [
  { ...base, dependencies: ["torch 2.4 + py310"], chips: ["C500"], GPU: "C500" },
  { ...base, dependencies: ["torch 2.4 + py310"], chips: ["C600"], GPU: "C600" },
  { ...base, dependencies: ["torch 2.8 + py312"], python_label: "py312", pytorch_label: "torch2.8", chips: ["C500"], GPU: "C500" },
  { ...base, app_id: "hpl", app_name: "HPL", dependencies: ["sdk"], scope_pending: false, chips: ["C500"], GPU: "C500" },
  { ...base, app_id: "unknown", app_name: "Unknown", dependencies: [], chips: [], GPU: "", command: "", delivery_reference: "" },
];

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><CompatibilityPane /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiGet).mockResolvedValue({ app_count: 3, rows });
});

it("groups known runtime dependencies even without app_dependency and counts unique Apps", async () => {
  show();
  const group = await screen.findByTestId("compatibility-group-torch 2.4 + py310");
  expect(group.querySelector("summary")).toHaveTextContent("1 个 App");
  expect(within(group).getByTestId("compat-app-pyg")).toHaveTextContent("2 条命令");
  expect(screen.getByTestId("compatibility-group-torch 2.8 + py312")).toHaveTextContent("1 条命令");
  expect(screen.getByTestId("compatibility-group-sdk")).toHaveTextContent("HPL");
  const pending = screen.getByTestId("compatibility-pending");
  expect(within(pending).getByTestId("compat-app-unknown")).toBeInTheDocument();
  expect(within(pending).queryByTestId("compat-app-pyg")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "导出当前测试清单 CSV" }));
  expect(vi.mocked(downloadCsv).mock.calls[0][2]).toHaveLength(rows.length);
});

it("combines component and platform filters and exports only those rows", async () => {
  show();
  await screen.findByTestId("compatibility-group-torch 2.4 + py310");
  fireEvent.change(screen.getByLabelText("按组件筛选"), { target: { value: "torch 2.4 + py310" } });
  expect(screen.queryByTestId("compatibility-group-sdk")).not.toBeInTheDocument();
  expect(screen.queryByTestId("compatibility-group-torch 2.8 + py312")).not.toBeInTheDocument();
  expect(screen.getByTestId("compat-app-unknown")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("按GPU 芯片筛选"), { target: { value: "C600" } });
  fireEvent.click(screen.getByRole("button", { name: "导出当前测试清单 CSV" }));
  const exported = vi.mocked(downloadCsv).mock.calls[0][2];
  expect(exported).toHaveLength(1);
  expect(exported[0]).toContain("C600");
  expect(exported[0]).toContain("a".repeat(40));
});

it("shows each image's commit separately within an App", async () => {
  vi.mocked(apiGet).mockResolvedValue({ app_count: 1, rows: [rows[0], { ...rows[1], app_info_commit_id: "b".repeat(40) }] });
  show();
  const group = await screen.findByTestId("compatibility-group-torch 2.4 + py310");
  fireEvent.click(within(group).getByRole("button", { name: "展开 PyG 的镜像和测试命令" }));
  const commits = within(group).getAllByTestId("compat-app-info-commit");
  expect(commits.map((cell) => cell.title)).toEqual(["a".repeat(40), "b".repeat(40)]);
  expect(within(group).getByTitle("该交付镜像对应的 app_info commit id")).toBeInTheDocument();
});
