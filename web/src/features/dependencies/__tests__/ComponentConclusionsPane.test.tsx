import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import { ComponentConclusionsPane } from "../ComponentConclusionsPane";
import { apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { useUiStore } from "../../../store/uiStore";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
let identity = { username: "qa", role: "QA" };
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: identity }) }));
const app = { app_id: "pyg", app_name: "PyG", app_version: "0.4", hpc_version: "3.9.0",
  qa_status: "", qa_comment: "", revision: 0, updated_at: "", updated_by: "",
  tests: [{ path: "pyg_test", command: "", platforms: [{ os: "ubuntu22.04", arch: "amd64", chips: ["C500", "C600"] }] }] };
const initial = { id: 1, component: "torch 2.8 + py312", component_version: "3.10.0", created_at: "2026-11-16 10:00:00", created_by: "qa", apps: [app] };
let reports: typeof initial[];

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const node = () => <QueryClientProvider client={client}><ComponentConclusionsPane /></QueryClientProvider>;
  const view = render(node());
  return { ...view, rerenderPane: () => view.rerender(node()) };
}

beforeEach(() => {
  vi.clearAllMocks();
  identity = { username: "qa", role: "QA" };
  reports = [structuredClone(initial)];
  useUiStore.getState().setComponentCompatibilityDirty(false);
  vi.mocked(confirmDialog).mockResolvedValue(true);
  vi.mocked(apiGet).mockImplementation(async path => {
    if (path.endsWith("/components")) return { components: [{ component: initial.component, app_count: 1 }] };
    if (path === "/api/component-compatibility") return { reports: structuredClone(reports) };
    return structuredClone(reports.find(item => path.endsWith(`/${item.id}`)));
  });
  vi.mocked(apiPost).mockImplementation(async (path, raw) => {
    if (path === "/api/component-compatibility") {
      const body = raw as { component: string; component_version: string };
      const report = { ...structuredClone(initial), ...body, id: 2 };
      reports.unshift(report); return structuredClone(report);
    }
    const body = raw as { qa_status: string; qa_comment: string; revision: number };
    const record = reports.find(item => path.includes(`/${item.id}/apps/`))!;
    record.apps[0] = { ...record.apps[0], ...body, revision: body.revision + 1, updated_by: "qa", updated_at: "2026-11-16 11:00:00" };
    return { app: structuredClone(record.apps[0]) };
  });
});

it("reads saved scope without looking up current integrations and omits commands", async () => {
  show();
  await screen.findByText("PyG");
  expect(vi.mocked(apiGet).mock.calls.map(([path]) => path)).not.toContain("/api/component-compatibility/components");
  expect(screen.getByRole("columnheader", { name: "app_id" })).toBeVisible();
  expect(screen.getByRole("columnheader", { name: "hpc_version" })).toBeVisible();
  const details = screen.getByText("测试范围 1 项").closest("details")!;
  expect(details.open).toBe(false);
  fireEvent.click(within(details).getByText("测试范围 1 项"));
  expect(within(details).getByText("pyg_test")).toBeVisible();
  expect(within(details).getByText("x86")).toBeVisible();
  expect(within(details).getByText("C500, C600")).toBeVisible();
  expect(within(details).queryByRole("columnheader", { name: "命令" })).toBeNull();
});

it("creates a component version before filling its App conclusions", async () => {
  show(); await screen.findByText("PyG");
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  const form = await screen.findByRole("form", { name: "新建组件兼容性结论" });
  await within(form).findByRole("option", { name: /torch 2.8/ });
  fireEvent.change(within(form).getByLabelText("组件"), { target: { value: initial.component } });
  fireEvent.change(within(form).getByLabelText("组件版本（MACA 版本）"), { target: { value: "3.10.1" } });
  fireEvent.submit(form);
  await waitFor(() => expect(screen.getByLabelText("查看已有记录")).toHaveValue("2"));
  expect(vi.mocked(apiPost)).toHaveBeenCalledWith("/api/component-compatibility", { component: initial.component, component_version: "3.10.1" });
  expect(await screen.findByLabelText("PyG 的结论")).toHaveValue("");
});

it("offers driver/sdk for all Apps and submits the scope prepared for this form", async () => {
  show(); await screen.findByText("PyG");
  vi.mocked(apiGet).mockResolvedValueOnce({ components: [
    { component: "driver", app_count: 3, all_apps: true }, { component: "sdk", app_count: 3, all_apps: true },
  ], preview_id: "prepared-for-qa" });
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  const form = await screen.findByRole("form", { name: "新建组件兼容性结论" });
  expect(await within(form).findByRole("option", { name: /driver.*全部.*3/ })).toBeInTheDocument();
  expect(within(form).getByRole("option", { name: /sdk.*全部.*3/ })).toBeInTheDocument();
  fireEvent.change(within(form).getByLabelText("组件"), { target: { value: "driver" } });
  fireEvent.change(within(form).getByLabelText("组件版本（MACA 版本）"), { target: { value: "3.10.1" } });
  fireEvent.submit(form);
  await waitFor(() => expect(apiPost).toHaveBeenCalledWith("/api/component-compatibility", {
    component: "driver", component_version: "3.10.1", preview_id: "prepared-for-qa",
  }));
  await waitFor(() => expect(screen.getByLabelText("查看已有记录")).toHaveValue("2"));
});

it("shows creation errors beside the create controls and preserves entered values", async () => {
  vi.mocked(apiPost).mockRejectedValue(new Error("清单已失效，请刷新组件列表"));
  show(); await screen.findByText("PyG");
  fireEvent.click(screen.getByRole("button", { name: /新建/ }));
  const form = await screen.findByRole("form", { name: "新建组件兼容性结论" });
  await within(form).findByRole("option", { name: /torch 2.8/ });
  fireEvent.change(within(form).getByLabelText("组件"), { target: { value: initial.component } });
  fireEvent.change(within(form).getByLabelText("组件版本（MACA 版本）"), { target: { value: "3.10.2" } });
  fireEvent.submit(form);
  expect(await within(form).findByRole("alert")).toHaveTextContent("清单已失效");
  expect(within(form).getByLabelText("组件版本（MACA 版本）")).toHaveValue("3.10.2");
  expect(within(form).getByRole("button", { name: "刷新组件列表" })).toBeVisible();
});

it("saves optional comments with the displayed row revision and reads the updated result", async () => {
  show(); await screen.findByText("PyG");
  fireEvent.change(screen.getByLabelText("PyG 的结论"), { target: { value: "fail" } });
  expect(useUiStore.getState().componentCompatibilityDirty).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "保存 PyG 的兼容性结论" }));
  await screen.findByText(/模拟保存成功|已创建|qa · 2026-11-16 11:00:00/);
  expect(vi.mocked(apiPost)).toHaveBeenCalledWith("/api/component-compatibility/1/apps/pyg", { qa_status: "fail", qa_comment: "", revision: 0 });
  await waitFor(() => expect(screen.getByRole("button", { name: "保存 PyG 的兼容性结论" })).toBeDisabled());
  expect(screen.getByText("fail 1")).toBeVisible();
});

it("retains a draft when saving fails or a report switch is cancelled", async () => {
  reports.push({ ...structuredClone(initial), id: 2, component_version: "3.10.1" });
  vi.mocked(apiPost).mockRejectedValue(new Error("该 App 的兼容性结论已被其他人修改"));
  vi.mocked(confirmDialog).mockResolvedValue(false);
  show(); await screen.findByText("PyG");
  fireEvent.change(screen.getByLabelText("PyG 的结论"), { target: { value: "pass" } });
  fireEvent.change(screen.getByLabelText("PyG 的 comment"), { target: { value: "keep draft" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 PyG 的兼容性结论" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("其他人修改");
  fireEvent.change(screen.getByLabelText("查看已有记录"), { target: { value: "2" } });
  await waitFor(() => expect(confirmDialog).toHaveBeenCalled());
  expect(screen.getByLabelText("查看已有记录")).toHaveValue("1");
  expect(screen.getByLabelText("PyG 的 comment")).toHaveValue("keep draft");
});

it("filters the complete saved App set using the QA table's column controls", async () => {
  reports[0].apps.push({ ...structuredClone(app), app_id: "other", app_name: "Other" });
  show(); await screen.findByText("Other");
  fireEvent.change(screen.getByLabelText("app_id 列筛选"), { target: { value: "other" } });
  expect(screen.queryByText("PyG")).toBeNull();
  expect(screen.getByText("Other")).toBeVisible();
  expect(screen.getByText("1 / 2 个 App")).toBeVisible();
});

it("reads another user's latest conclusion when returning to a previously viewed record", async () => {
  reports.push({ ...structuredClone(initial), id: 2, component_version: "3.10.1" });
  show(); await screen.findByText("PyG");
  reports[0].apps[0] = { ...reports[0].apps[0], qa_comment: "external change", qa_status: "fail", revision: 1 };
  fireEvent.change(screen.getByLabelText("查看已有记录"), { target: { value: "2" } });
  await waitFor(() => expect(screen.getByLabelText("查看已有记录")).toHaveValue("2"));
  await screen.findByText("PyG");
  fireEvent.change(screen.getByLabelText("查看已有记录"), { target: { value: "1" } });
  await waitFor(() => expect(screen.getByLabelText("PyG 的 comment")).toHaveValue("external change"));
});

it("allows Owners to read saved results without edit or create controls", async () => {
  identity = { username: "owner", role: "Owner" };
  reports[0].apps[0].qa_status = "pass";
  show(); await screen.findByText("PyG");
  expect(screen.queryByRole("button", { name: /新建/ })).toBeNull();
  expect(screen.queryByLabelText("PyG 的结论")).toBeNull();
  expect(screen.queryByRole("button", { name: /保存 PyG/ })).toBeNull();
  expect(screen.getByText("pass", { exact: true })).toBeVisible();
});

it("does not carry a pending save's draft into another account", async () => {
  let complete!: (value: unknown) => void;
  vi.mocked(apiPost).mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const view = show(); await screen.findByText("PyG");
  fireEvent.change(screen.getByLabelText("PyG 的 comment"), { target: { value: "old user draft" } });
  fireEvent.change(screen.getByLabelText("PyG 的结论"), { target: { value: "pass" } });
  fireEvent.click(screen.getByRole("button", { name: "保存 PyG 的兼容性结论" }));
  identity = { username: "other-qa", role: "QA" };
  view.rerenderPane();
  complete({ app: { ...app, qa_comment: "old user draft", qa_status: "pass", revision: 1 } });
  await waitFor(() => expect(screen.getByLabelText("PyG 的 comment")).toHaveValue(""));
  expect(screen.getByLabelText("PyG 的结论")).toHaveValue("");
});
