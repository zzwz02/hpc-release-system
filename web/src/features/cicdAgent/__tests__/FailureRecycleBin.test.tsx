import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiGet, apiPost } from "../../../api/http";
import { JenkinsFailuresPage } from "../JenkinsFailuresPage";
import type { FailureLifecycleAction, FailureRecordDetail } from "../cicdAgentApi";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: vi.fn() }));

const active: FailureRecordDetail = {
  id: 101, job_name: "Hpc_Example_Release", build_number: 88, normalized_stage: "build_image",
  code_owner: "alice", owner_account: "bob", owner_role: "DevOps", record_state: "active",
  created_at: "2026-01-01T10:00:00", responsibility_status: "disputed", reason_summary: "Dependency failure",
};
const trashed: FailureRecordDetail = {
  ...active, id: 202, record_state: "trashed", record_state_updated_by: "rm",
  record_state_updated_at: "2026-11-02T12:00:00", record_state_reason: "Duplicate failure",
};

function renderPage(role = "RM", route = "/jenkins-failures") {
  vi.mocked(useAuth).mockReturnValue({ user: { username: "alice", role } } as ReturnType<typeof useAuth>);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}>
    <JenkinsFailuresPage />
  </MemoryRouter></QueryClientProvider>);
  return { ...view, client };
}

function latestQuery(suffix = "") {
  const paths = vi.mocked(apiGet).mock.calls.map(([path]) => new URL(path, "http://localhost"));
  return paths.filter(url => url.pathname === `/api/cicd-agent/failures${suffix}`).at(-1)!.searchParams;
}

async function openSingleAction(action: FailureLifecycleAction) {
  if (action === "trash") {
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const detail = screen.getByRole("dialog", { name: "记录详情" });
    const button = await within(detail).findByRole("button", { name: "移入回收站" });
    await waitFor(() => expect(button).toBeEnabled());
    await userEvent.click(button);
  } else {
    await userEvent.click(await screen.findByRole("button", { name: action === "restore" ? "恢复" : "彻底删除" }));
  }
}

describe("failure recycle bin", () => {
  let records: FailureRecordDetail[];
  let pageSize: number;

  beforeEach(() => {
    vi.clearAllMocks();
    pageSize = 20;
    records = [structuredClone(active), structuredClone(trashed)];
    vi.mocked(apiGet).mockImplementation(async path => {
      const url = new URL(path, "http://localhost");
      const state = url.searchParams.get("record_state") || "active";
      const matching = records.filter(record => record.record_state === state);
      if (url.pathname === "/api/state") return { user_display_names: { rm: "Release Manager" } };
      if (url.pathname.endsWith("/preferences")) return { enabled: false };
      if (url.pathname.endsWith("/filter-options")) return { owners: [{ code_owner: "alice" }], owner_accounts: ["bob"] };
      if (url.pathname.endsWith("/summary")) return { groups: [], total_records: matching.length };
      if (url.pathname.endsWith("/failures")) {
        const page = Number(url.searchParams.get("page") || 1);
        return { page, page_size: pageSize, total: matching.length, total_pages: Math.ceil(matching.length / pageSize),
          records: matching.slice((page - 1) * pageSize, page * pageSize) };
      }
      const id = Number(url.pathname.split("/").at(-1));
      const record = matching.find(item => item.id === id);
      if (record) return record;
      throw new Error(`Record not found: ${path}`);
    });
    vi.mocked(apiPost).mockImplementation(async (path, body) => {
      if (path.endsWith("/batch/record-state")) {
        const payload = body as { record_ids: number[]; action: FailureLifecycleAction; reason: string };
        const results = payload.record_ids.map(id => {
          const record = records.find(item => item.id === id)!;
          record.record_state = { trash: "trashed", restore: "active", purge: "deleted" }[payload.action] as FailureRecordDetail["record_state"];
          record.record_state_reason = payload.reason;
          return { id, success: true, status_code: 200 };
        });
        return { results, succeeded: results.length, failed: 0 };
      }
      const parts = path.split("/");
      const action = parts.at(-1) as FailureLifecycleAction;
      const record = records.find(item => item.id === Number(parts.at(-2)))!;
      record.record_state = { trash: "trashed", restore: "active", purge: "deleted" }[action] as FailureRecordDetail["record_state"];
      record.record_state_reason = (body as { reason: string }).reason;
      return { ...record };
    });
  });

  it.each(["Owner", "SPD", "Admin"])("does not expose trash or lifecycle actions to %s", async role => {
    renderPage(role, "/jenkins-failures?record_state=trashed");
    await screen.findByText("#101");
    expect(screen.queryByRole("tab", { name: "回收站" })).not.toBeInTheDocument();
    for (const name of ["删除", "恢复", "彻底删除"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "选择当前页全部记录" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批量删除" })).not.toBeInTheDocument();
    expect(vi.mocked(apiGet).mock.calls.every(([path]) => !path.includes("record_state=trashed"))).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "查看详情" }));
    const detail = screen.getByRole("dialog", { name: "记录详情" });
    await within(detail).findByText("Dependency failure");
    expect(within(detail).queryByRole("button", { name: "移入回收站" })).not.toBeInTheDocument();
  });

  it("starts trash across all dates and owners, sorts by deletion time, and preserves normal filters", async () => {
    renderPage("RM", "/jenkins-failures?date_from=2026-10-01&date_to=2026-10-31&pending_rm_review=true");
    await userEvent.click(screen.getByRole("checkbox", { name: "只看我的" }));
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await screen.findByText("#202");
    expect(screen.getByRole("checkbox", { name: "只看我的" })).not.toBeChecked();
    expect(screen.getByLabelText("开始时间")).toHaveValue("");
    expect(screen.getByLabelText("结束时间")).toHaveValue("");
    for (const suffix of ["", "/summary", "/filter-options"]) {
      const query = latestQuery(suffix);
      expect(query.get("record_state")).toBe("trashed");
      for (const field of ["date_from", "date_to", "code_owner", "pending_rm_review"]) expect(query.has(field)).toBe(false);
    }
    expect(latestQuery().get("sort_by")).toBe("record_state_updated_at");
    expect(latestQuery().get("sort_order")).toBe("desc");
    fireEvent.change(screen.getByLabelText("开始时间"), { target: { value: "2025-01-01" } });
    await userEvent.click(screen.getByRole("tab", { name: "正常失败" }));
    expect(screen.getByLabelText("开始时间")).toHaveValue("2026-10-01");
    expect(screen.getByLabelText("结束时间")).toHaveValue("2026-10-31");
    expect(screen.getByRole("checkbox", { name: "只看我的" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: /仅看待 RM 裁定/ })).toBeChecked();
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    expect(screen.getByLabelText("开始时间")).toHaveValue("2025-01-01");
    await userEvent.click(screen.getByRole("button", { name: "重置" }));
    expect(screen.getByLabelText("开始时间")).toHaveValue("");
  });

  it("loads scoped read-only trash detail with audit fields and no repro request", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    const table = screen.getByRole("table");
    await within(table).findByText("Duplicate failure");
    expect(within(table).getByRole("columnheader", { name: "删除原因" })).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: "删除时间" })).toBeInTheDocument();
    expect(table).toHaveTextContent("删除人：Release Manager (rm)");
    expect(table).toHaveTextContent("2026-11-02 12:00:00");
    expect(table).not.toHaveTextContent("2026-01-01");
    expect(table).not.toHaveTextContent("Dependency failure");
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    const dialog = await screen.findByRole("dialog", { name: "记录详情" });
    await within(dialog).findByText("Duplicate failure");
    expect(apiGet).toHaveBeenCalledWith("/api/cicd-agent/failures/202?record_state=trashed");
    expect(dialog).toHaveTextContent("回收站（只读）");
    expect(dialog).toHaveTextContent("Dependency failure");
    expect(dialog).toHaveTextContent("2026-11-02 12:00:00");
    for (const name of ["责任反馈", "处理责任", "复议诊断", "移入回收站"]) expect(within(dialog).queryByRole("button", { name })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "责任链" }));
    expect(within(dialog).queryByRole("textbox")).not.toBeInTheDocument();
    expect(vi.mocked(apiGet).mock.calls.every(([path]) => !path.includes("repro-tasks"))).toBe(true);
  });

  it("allows an empty note and confirms job/build/stage before deleting; refreshes all failure caches", async () => {
    const { client } = renderPage();
    const cachedKeys = [
      ["cicd-agent", "failures", "inactive"], ["cicd-agent", "summary", "inactive"],
      ["cicd-agent", "filter-options", "inactive"], ["cicd-agent", "repro-tasks", 101],
      ["repro-logs", "task"], ["repro-log", "task", "build.log"],
    ];
    cachedKeys.forEach(key => client.setQueryData(key, {}));
    client.setQueryData(["cicd-agent", "failure-detail", 101, "active"], active);
    client.setQueryData(["cicd-agent", "failure-detail", 101, "trashed"], active);
    await screen.findByText("#101");
    expect(within(screen.getByRole("table")).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(within(screen.getByRole("table").querySelector(".cicd-agent-row-actions") as HTMLElement).getAllByRole("button")).toHaveLength(2);
    await openSingleAction("trash");
    const dialog = screen.getByRole("alertdialog", { name: "移入回收站" });
    expect(dialog).toHaveTextContent("Hpc_Example_Release");
    expect(dialog).toHaveTextContent("#88");
    expect(dialog).toHaveTextContent("build_image");
    const submit = within(dialog).getByRole("button", { name: "移入回收站" });
    expect(submit).toBeEnabled();
    await userEvent.type(within(dialog).getByLabelText("备注（选填）"), "   ");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.queryByRole("dialog", { name: "记录详情" })).not.toBeInTheDocument();
    expect(apiPost).toHaveBeenCalledWith("/api/cicd-agent/failures/101/trash", { reason: "" });
    expect(screen.queryByText("#101")).not.toBeInTheDocument();
    cachedKeys.forEach(key => expect(client.getQueryState(key)?.isInvalidated).toBe(true));
    expect(client.getQueriesData({ queryKey: ["cicd-agent", "failure-detail", 101] })).toEqual([]);
  });

  it("restores trash, invalidates normal results, and keeps restored detail editable", async () => {
    renderPage();
    await screen.findByText("#101");
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await userEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await userEvent.click(screen.getByRole("button", { name: "恢复记录" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledWith("/api/cicd-agent/failures/202/restore", { reason: "" });
    expect(screen.queryByText("#202")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "正常失败" }));
    const row = (await screen.findByText("#202")).closest("tr")!;
    expect(screen.getByRole("columnheader", { name: "原因摘要" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "时间" })).toBeInTheDocument();
    expect(row).toHaveTextContent("Dependency failure");
    expect(row).toHaveTextContent("2026-01-01 10:00:00");
    await userEvent.click(within(row).getByRole("button", { name: "查看详情" }));
    const dialog = screen.getByRole("dialog", { name: "记录详情" });
    expect(await within(dialog).findByRole("button", { name: "责任反馈" })).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith("/api/cicd-agent/failures/202");
  });

  it("requires exact permanent-delete confirmation, explains retention, and permits cancellation", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await userEvent.click(await screen.findByRole("button", { name: "彻底删除" }));
    let dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("无法在界面恢复");
    expect(dialog).toHaveTextContent("归档仍按保留策略留存");
    await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(apiPost).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "彻底删除" }));
    dialog = screen.getByRole("alertdialog");
    const submit = within(dialog).getByRole("button", { name: "彻底删除" });
    const confirmation = within(dialog).getByLabelText("输入“彻底删除 #202”确认");
    await userEvent.type(confirmation, "彻底删除 #101");
    expect(submit).toBeDisabled();
    await userEvent.clear(confirmation);
    await userEvent.type(confirmation, "彻底删除 #202");
    await userEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledWith("/api/cicd-agent/failures/202/purge", { reason: "" });
    expect(screen.queryByText("#202")).not.toBeInTheDocument();
  });

  it.each(["trash", "restore", "purge"] as const)("keeps %s errors and input visible without removing the row", async action => {
    renderPage();
    if (action !== "trash") await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await openSingleAction(action);
    const dialog = screen.getByRole("alertdialog");
    await userEvent.type(within(dialog).getByLabelText("备注（选填）"), "retry later");
    if (action === "purge") await userEvent.type(within(dialog).getByLabelText("输入“彻底删除 #202”确认"), "彻底删除 #202");
    vi.mocked(apiPost).mockRejectedValueOnce(new Error("409: live repro task or conflicting record state"));
    await userEvent.click(within(dialog).getByRole("button", { name: { trash: "移入回收站", restore: "恢复记录", purge: "彻底删除" }[action] }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("409: live repro task or conflicting record state");
    expect(within(dialog).getByRole("textbox", { name: /备注/ })).toHaveValue("retry later");
    expect(within(screen.getByRole("table")).getByText(action === "trash" ? "#101" : "#202")).toBeInTheDocument();
    if (action === "trash") {
      await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
      expect(screen.getByRole("dialog", { name: "记录详情" })).toHaveTextContent("Dependency failure");
    }
  });

  it.each(["trash", "restore", "purge"] as const)("moves back a page after %s removes the last row", async action => {
    pageSize = 1;
    records.push({ ...(action === "trash" ? active : trashed), id: 303 });
    renderPage();
    if (action !== "trash") await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("#303");
    await openSingleAction(action);
    const dialog = screen.getByRole("alertdialog");
    if (action === "trash") await userEvent.type(within(dialog).getByLabelText("备注（选填）"), "duplicate");
    if (action === "purge") await userEvent.type(within(dialog).getByLabelText("输入“彻底删除 #303”确认"), "彻底删除 #303");
    await userEvent.click(within(dialog).getByRole("button", { name: { trash: "移入回收站", restore: "恢复记录", purge: "彻底删除" }[action] }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await screen.findByText(action === "trash" ? "#101" : "#202");
    expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
    expect(latestQuery().get("page")).toBe("1");
  });

  it("shows backend errors when trash detail becomes unavailable", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    const button = await screen.findByRole("button", { name: "查看详情" });
    records = [];
    await userEvent.click(button);
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent("Record not found");
  });

  it.each(["cancel", "escape", "backdrop"])("%s closes only the delete confirmation and returns focus to detail", async method => {
    renderPage();
    await openSingleAction("trash");
    const dialog = screen.getByRole("alertdialog", { name: "移入回收站" });
    const detail = screen.getByRole("dialog", { hidden: true });
    expect(detail).toHaveAttribute("inert");
    expect(detail).toHaveAttribute("aria-hidden", "true");
    expect(apiPost).not.toHaveBeenCalled();
    if (method === "cancel") await userEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    if (method === "escape") await userEvent.keyboard("{Escape}");
    if (method === "backdrop") fireEvent.mouseDown(dialog.parentElement!);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "记录详情" })).toBe(detail);
    expect(detail).not.toHaveAttribute("inert");
    expect(within(detail).getByRole("button", { name: "移入回收站" })).toHaveFocus();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("selects only the current page and clears selection on filters, pagination and tabs", async () => {
    pageSize = 2;
    records.push({ ...active, id: 303 }, { ...active, id: 404 });
    renderPage();
    await screen.findByText("#101");
    const selectAll = screen.getByRole("checkbox", { name: "选择当前页全部记录" });
    await userEvent.click(screen.getByRole("checkbox", { name: "选择记录 #101" }));
    expect(selectAll).toBePartiallyChecked();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(selectAll);
    expect(screen.getByText("已选 2 条")).toBeInTheDocument();
    expect(screen.queryByText("#404")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "下一页" }));
    await screen.findByText("#404");
    expect(screen.getByText("已选 0 条")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页全部记录" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "只看我的" }));
    await screen.findByText("#101");
    expect(screen.getByText("已选 0 条")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "选择记录 #101" }));
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await screen.findByText("#202");
    expect(screen.getByText("已选 0 条")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "正常失败" }));
    await screen.findByText("#101");
    expect(screen.getByRole("checkbox", { name: "选择记录 #101" })).not.toBeChecked();
  });

  it.each(["trash", "restore", "purge"] as const)("bulk %s uses a fixed selection, optional note and one confirmation", async action => {
    records.push({ ...(action === "trash" ? active : trashed), id: 303 });
    const { client } = renderPage();
    if (action !== "trash") await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await screen.findByText("#303");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页全部记录" }));
    const label = { trash: "批量删除", restore: "批量恢复", purge: "批量彻底删除" }[action];
    await userEvent.click(screen.getByRole("button", { name: label }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("（2 条）");
    expect(dialog).toHaveTextContent("#303");
    records.push({ ...(action === "trash" ? active : trashed), id: 404 });
    await client.invalidateQueries({ queryKey: ["cicd-agent"] });
    expect(dialog).not.toHaveTextContent("#404");
    await userEvent.type(within(dialog).getByLabelText("备注（选填）"), "  batch  ");
    const submit = within(dialog).getByRole("button", { name: { trash: "批量移入回收站", restore: "批量恢复记录", purge: "批量彻底删除" }[action] });
    if (action === "purge") {
      expect(submit).toBeDisabled();
      const confirmation = within(dialog).getByLabelText("输入“彻底删除 2 条”确认");
      await userEvent.type(confirmation, "彻底删除 3 条");
      expect(submit).toBeDisabled();
      await userEvent.clear(confirmation);
      await userEvent.type(confirmation, "彻底删除 2 条");
    }
    await userEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledWith("/api/cicd-agent/failures/batch/record-state", {
      record_ids: [action === "trash" ? 101 : 202, 303], action, reason: "batch",
    });
    expect(screen.getByRole("status")).toHaveTextContent("成功 2 条，未完成 0 条");
    expect(screen.getByText("#404")).toBeInTheDocument();
    expect(screen.getByText("已选 0 条")).toBeInTheDocument();
  });

  it("retains failed rows selected and reports per-record errors after partial success", async () => {
    records.push({ ...active, id: 303 });
    renderPage();
    await screen.findByText("#303");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页全部记录" }));
    await userEvent.click(screen.getByRole("button", { name: "批量删除" }));
    vi.mocked(apiPost).mockImplementationOnce(async () => {
      records[0].record_state = "trashed";
      return { succeeded: 1, failed: 1, results: [
        { id: 101, success: true, status_code: 200 },
        { id: 303, success: false, status_code: 409, error: "本条记录有未结束的复议" },
      ] };
    });
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "批量移入回收站" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByRole("status")).toHaveTextContent("成功 1 条，未完成 1 条");
    expect(screen.getByRole("status")).toHaveTextContent("#303：本条记录有未结束的复议");
    expect(screen.getByRole("checkbox", { name: "选择记录 #303" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "选择记录 #101" })).not.toBeInTheDocument();
  });

  it("keeps a failed batch dialog open and does not automatically retry a network error", async () => {
    renderPage();
    await screen.findByText("#101");
    await userEvent.click(screen.getByRole("checkbox", { name: "选择当前页全部记录" }));
    await userEvent.click(screen.getByRole("button", { name: "批量删除" }));
    vi.mocked(apiPost).mockRejectedValueOnce(new Error("Network error"));
    await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "批量移入回收站" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Network error");
    expect(apiPost).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "刷新记录" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(apiPost).toHaveBeenCalledTimes(1);
  });

  it("refreshes after a conflicting restore and discards cached trash detail", async () => {
    const { client } = renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "回收站" }));
    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await screen.findByText("回收站（只读）");
    await userEvent.click(screen.getByRole("button", { name: "关闭" }));
    await userEvent.click(screen.getByRole("button", { name: "恢复" }));
    records[1].record_state = "active";
    vi.mocked(apiPost).mockRejectedValueOnce(new Error("409: conflicting record state"));
    await userEvent.click(screen.getByRole("button", { name: "恢复记录" }));
    await screen.findByRole("alert");
    await userEvent.click(screen.getByRole("button", { name: "刷新记录" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.queryByText("#202")).not.toBeInTheDocument();
    expect(client.getQueriesData({ queryKey: ["cicd-agent", "failure-detail", 202] })).toEqual([]);
    await userEvent.click(screen.getByRole("tab", { name: "正常失败" }));
    await screen.findByText("#202");
    expect(apiPost).toHaveBeenCalledTimes(1);
  });
});
