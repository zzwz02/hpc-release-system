import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiFetch, apiGet, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { triggerBlobDownload } from "../../../lib/csv";
import { WeeklyCheckPage } from "../WeeklyCheckPage";
import { isoWeekKey, parseMemberRows } from "../weeklyCheckApi";

vi.mock("../../../api/http", () => ({
  apiFetch: vi.fn(),
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

vi.mock("../../../lib/confirm", () => ({
  confirmDialog: vi.fn(),
}));

vi.mock("../../../lib/csv", () => ({
  triggerBlobDownload: vi.fn(),
}));

const weeks = [
  { week_key: "2026-W41", label: "2026 W41（10月5日-10月11日）", week_start: "2026-10-05", week_end: "2026-10-11", finding_count: 3 },
  { week_key: "2026-W40", label: "2026 W40（9月28日-10月4日）", week_start: "2026-09-28", week_end: "2026-10-04", finding_count: 0 },
];

const week41 = {
  ...weeks[0],
  report_date: "2026-10-10",
  xlsx_name: "PDE HPC Project Plan 20261010.xlsx",
  xlsx_size: 2 * 1024 * 1024,
  uploaded_by: "rm1",
  uploaded_at: "2026-10-10 17:00:00",
  analyzed_by: "rm2",
  analyzed_at: "2026-10-10 18:00:00",
  active_hours: 192,
  jira_browse_url: "http://jira/browse/",
  problem_types: ["周报日期异常", "周报未更新", "未写JIRA", "JIRA ID不完整", "JIRA未更新", "更新与周报不符"],
  summary: {
    total: 3,
    by_problem: [
      { problem: "未写JIRA", count: 1 },
      { problem: "JIRA未更新", count: 1 },
      { problem: "更新与周报不符", count: 1 },
    ],
    by_person: [
      { person: "Alice", count: 2 },
      { person: "Bob", count: 1 },
    ],
    people_count: 3,
    consistency: true,
    window_start: "2026-10-02 18:00",
    not_checked: ["Project"],
    unmapped_sheets: ["Carol"],
    people: [
      { sheet: "Alice", date: "2026-10-10", item_count: 3, note: "", username: "alice", display_name: "Alice Li" },
      { sheet: "Bob", date: "2026-10-10", item_count: 1, note: "", username: "m002", display_name: "" },
      {
        sheet: "Carol", date: "2026-10-10", item_count: 2, note: "找不到对应的用户，只检查是否写了 JIRA ID",
        username: "", display_name: "",
      },
    ],
  },
};

const finding = {
  sheet_date: "2026-10-10", jira_key: "", jira_status: "", assignee: "", last_updated: "", last_updater: "", note: "",
};
const findings = [
  { ...finding, id: 1, person: "Alice", problem: "未写JIRA", item: "调研新方案" },
  {
    ...finding, id: 2, person: "Alice", problem: "JIRA未更新", item: "调优", jira_key: "PHS-1",
    jira_status: "In Progress", assignee: "Alice", last_updated: "2026-09-30 18:00", last_updater: "Alice",
  },
  {
    ...finding, id: 3, person: "Bob", problem: "更新与周报不符", item: "适配", jira_key: "MC3-9, MC3-10",
    note: "JIRA 里看不到适配进展",
  },
];

const preview = {
  upload_id: "up_1",
  filename: "PDE HPC Project Plan 20261017.xlsx",
  size: 3 * 1024 * 1024,
  uploaded_by: "rm",
  uploaded_at: "2026-10-17 17:00:00",
  report_date: "2026-10-17",
  week: { week_key: "2026-W42", week_start: "2026-10-12", week_end: "2026-10-18", label: "2026 W42（10月12日-10月18日）" },
  week_exists: false,
  people_count: 26,
  mapping: [
    { sheet: "Alice", username: "alice", display_name: "Alice Li", status: "check", reason: "" },
    { sheet: "Carol", username: "", display_name: "", status: "check", reason: "找不到对应的用户" },
    { sheet: "Project", username: "", display_name: "", status: "skip", reason: "手工对应里设置为不检查" },
    { sheet: "WuZhao", username: "zhawu", display_name: "Zhao Wu", status: "skip", reason: "手工对应里设置为不检查" },
  ],
  unmapped_sheets: ["Carol"],
  undated_sheets: [],
  active_hours: 192,
};

function mockUser(role: string) {
  vi.mocked(useAuth).mockReturnValue({ user: { username: "rm", role } } as unknown as ReturnType<typeof useAuth>);
}

function mockBackend(overrides: Record<string, unknown> = {}) {
  vi.mocked(apiGet).mockImplementation(async (path: string) => {
    if (path in overrides) {
      const value = overrides[path];
      return typeof value === "function" ? (value as () => unknown)() : value;
    }
    if (path === "/api/weekly-check/weeks") return { weeks };
    if (path === "/api/weekly-check/weeks/2026-W41") return { week: week41 };
    if (path === "/api/weekly-check/weeks/2026-W41/findings") return { findings };
    if (path === "/api/weekly-check/weeks/2026-W40") {
      return { week: { ...week41, ...weeks[1], summary: { ...week41.summary, total: 0, by_problem: [], by_person: [] } } };
    }
    if (path === "/api/weekly-check/weeks/2026-W40/findings") return { findings: [] };
    if (path === "/api/weekly-check/members") {
      return { members: [{ sheet: "Alice", jira_username: "alice", skip: false, note: "" }], version: "v1" };
    }
    throw new Error(`unexpected GET ${path}`);
  });
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.search}</div>;
}

function renderPage(route = "/weekly-check") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <WeeklyCheckPage />
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WeeklyCheckPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUser("Owner");
    mockBackend();
  });

  it("lists the weeks newest first and opens the newest one", async () => {
    renderPage();

    const list = await screen.findByTestId("weekly-check-weeks");
    const items = await within(list).findAllByRole("button");
    expect(items.map((item) => item.textContent)).toEqual([
      "2026 W41（10月5日-10月11日）3 条问题",
      "2026 W40（9月28日-10月4日）0 条问题",
    ]);
    expect(items[0]).toHaveAttribute("aria-current", "true");

    const record = await screen.findByTestId("weekly-check-record");
    expect(record).toHaveTextContent("rm1 · PDE HPC Project Plan 20261010.xlsx · 2.0 MB · 2026-10-10 17:00:00");
    expect(record).toHaveTextContent("rm2 · 2026-10-10 18:00:00");
    expect(record).toHaveTextContent("3 人；JIRA 更新窗口 2026-10-02 18:00 起（192 小时）");
    const summary = screen.getByTestId("weekly-check-summary");
    expect(summary).toHaveTextContent("Carol：找不到对应的用户，只检查是否写了 JIRA ID");
    const mapping = within(summary).getByTestId("weekly-check-mapping");
    expect(within(mapping).getAllByRole("row", { hidden: true }).map((row) => row.textContent)).toEqual([
      "周报页姓名账号说明",
      "AliceAlice Lialice",
      "Bobm002",
      "Carol—找不到对应的用户，只检查是否写了 JIRA ID",
    ]);
  });

  it("links JIRA keys and filters the findings by person and problem type", async () => {
    const user = userEvent.setup();
    renderPage();

    const table = await screen.findByTestId("weekly-check-findings");
    expect(within(table).getAllByRole("row")).toHaveLength(4);
    expect(within(table).getByRole("link", { name: "PHS-1" })).toHaveAttribute("href", "http://jira/browse/PHS-1");
    expect(within(table).getByRole("link", { name: "MC3-10" })).toHaveAttribute("href", "http://jira/browse/MC3-10");

    await user.selectOptions(screen.getByLabelText("按人筛选"), "Alice");
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    await user.click(screen.getByRole("button", { name: /JIRA未更新/ }));
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("调优")).toBeInTheDocument();
    expect(screen.getByLabelText("按问题类型筛选")).toHaveValue("JIRA未更新");

    await user.selectOptions(screen.getByLabelText("按人筛选"), "Bob");
    expect(within(table).getByText("没有符合条件的问题")).toBeInTheDocument();
  });

  it("opens the week named in the URL and switches weeks from the list", async () => {
    const user = userEvent.setup();
    renderPage("/weekly-check?week=2026-W40");

    expect(await screen.findByText("本周没有发现问题")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /2026 W41/ }));
    expect(await screen.findByText("调研新方案")).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("?week=2026-W41");
  });

  it("exports the selected week in both formats", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => new Response("content", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await user.click(await screen.findByRole("button", { name: "导出 CSV" }));
    await waitFor(() => expect(triggerBlobDownload).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/weekly-check/weeks/2026-W41/export?format=csv", { credentials: "include" },
    );
    expect(vi.mocked(triggerBlobDownload).mock.calls[0][0]).toBe("weekly_jira_check_2026-10-10.csv");

    await user.click(screen.getByRole("button", { name: "导出 Markdown" }));
    await waitFor(() => expect(triggerBlobDownload).toHaveBeenCalledTimes(2));
    expect(vi.mocked(triggerBlobDownload).mock.calls[1][0]).toBe("weekly_jira_check_2026-10-10.md");
    vi.unstubAllGlobals();
  });

  it("hides upload and manual overrides from Owner", async () => {
    renderPage();

    await screen.findByTestId("weekly-check-record");
    expect(screen.queryByRole("button", { name: "上传周报" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "手工对应" })).not.toBeInTheDocument();
  });

  it("lets RM upload, warns before overwriting, and follows the analysis to its week", async () => {
    mockUser("RM");
    const user = userEvent.setup();
    let polls = 0;
    mockBackend({
      "/api/weekly-check/jobs/job_1": () => {
        polls += 1;
        return polls === 1
          ? { job_id: "job_1", status: "running", message: "已完成 Alice", done: 1, total: 26, week_key: "", error: "" }
          : { job_id: "job_1", status: "completed", message: "分析完成", done: 26, total: 26, week_key: "2026-W41", error: "" };
      },
    });
    vi.mocked(apiFetch).mockResolvedValue(preview);
    vi.mocked(apiPost).mockResolvedValue({ job_id: "job_1", status: "running", message: "", done: 0, total: 0, week_key: "", error: "" });
    renderPage("/weekly-check?week=2026-W40");

    await user.click(await screen.findByRole("button", { name: "上传周报" }));
    const file = new File(["xlsx"], "PDE HPC Project Plan 20261017.xlsx");
    await user.upload(screen.getByLabelText("周报文件"), file);

    const shown = await screen.findByTestId("weekly-check-preview");
    expect(apiFetch).toHaveBeenCalledWith(
      "/api/weekly-check/uploads?filename=PDE%20HPC%20Project%20Plan%2020261017.xlsx",
      { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } },
    );
    expect(shown).toHaveTextContent("PDE HPC Project Plan 20261017.xlsx（3.0 MB）");
    expect(shown).toHaveTextContent("2026 W42（10月12日-10月18日）");
    expect(shown).toHaveTextContent("26 人");
    expect(shown).toHaveTextContent("找不到对应的用户Carol");
    const dialog = screen.getByRole("dialog", { name: "上传周报" });
    expect(
      within(within(dialog).getByTestId("weekly-check-mapping"))
        .getAllByRole("row", { hidden: true })
        .slice(1)
        .map((row) => row.textContent),
    ).toEqual([
      "AliceAlice Lialice",
      "Carol—找不到对应的用户",
      "Project—不检查：手工对应里设置为不检查",
      "WuZhaoZhao Wuzhawu不检查：手工对应里设置为不检查",
    ]);
    expect(screen.queryByTestId("weekly-check-overwrite")).not.toBeInTheDocument();

    // Moving the report date into an analysed week warns about the overwrite.
    const dateInput = screen.getByTestId("weekly-check-report-date");
    await user.clear(dateInput);
    await user.type(dateInput, "2026-10-10");
    expect(screen.getByTestId("weekly-check-overwrite")).toHaveTextContent(
      "将覆盖 2026 W41（10月5日-10月11日） 的现有结果。",
    );

    await user.click(screen.getByRole("button", { name: "分析" }));
    expect(apiPost).toHaveBeenCalledWith("/api/weekly-check/uploads/up_1/analyze", {
      report_date: "2026-10-10", active_hours: 192, consistency: true,
    });
    expect(await screen.findByTestId("weekly-check-progress")).toHaveTextContent("第 1 人 / 共 26 人 · 已完成 Alice");

    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("?week=2026-W41"), { timeout: 4000 });
    expect(screen.queryByRole("dialog", { name: "上传周报" })).not.toBeInTheDocument();
    expect(await screen.findByText("调研新方案")).toBeInTheDocument();
  });

  it("shows a failed analysis with its reason and keeps the upload for a retry", async () => {
    mockUser("RM");
    const user = userEvent.setup();
    mockBackend({
      "/api/weekly-check/jobs/job_1": {
        job_id: "job_1", status: "failed", message: "分析失败", done: 3, total: 26, week_key: "", error: "未配置 LLM",
      },
    });
    vi.mocked(apiFetch).mockResolvedValue(preview);
    vi.mocked(apiPost).mockResolvedValue({ job_id: "job_1", status: "running", message: "", done: 0, total: 0, week_key: "", error: "" });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "上传周报" }));
    await user.upload(screen.getByLabelText("周报文件"), new File(["xlsx"], "plan.xlsx"));
    await user.click(await screen.findByRole("button", { name: "分析" }));

    expect(await screen.findByText(/分析失败：未配置 LLM/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "分析" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "关闭" }));
    await user.click(screen.getByRole("button", { name: "分析失败，点击查看" }));
    expect(screen.getByTestId("weekly-check-preview")).toBeVisible();
  });

  it("lets RM stop a running analysis after confirming", async () => {
    mockUser("RM");
    vi.mocked(confirmDialog).mockResolvedValue(true);
    const user = userEvent.setup();
    let cancelled = false;
    mockBackend({
      "/api/weekly-check/jobs/job_1": () =>
        cancelled
          ? {
              job_id: "job_1", status: "cancelled", message: "分析已中止，已有结果没有改动", done: 3, total: 26,
              week_key: "", error: "", stopping: false,
            }
          : {
              job_id: "job_1", status: "running", message: "已完成 Alice", done: 3, total: 26,
              week_key: "", error: "", stopping: false,
            },
    });
    vi.mocked(apiFetch).mockResolvedValue(preview);
    vi.mocked(apiPost).mockImplementation(async (path: string) => {
      if (path.endsWith("/cancel")) {
        cancelled = true;
        return { job_id: "job_1", status: "running", message: "正在中止", done: 3, total: 26, week_key: "", error: "", stopping: true };
      }
      return { job_id: "job_1", status: "running", message: "", done: 0, total: 0, week_key: "", error: "", stopping: false };
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "上传周报" }));
    await user.upload(screen.getByLabelText("周报文件"), new File(["xlsx"], "plan.xlsx"));
    await user.click(await screen.findByRole("button", { name: "分析" }));
    expect(await screen.findByTestId("weekly-check-progress")).toHaveTextContent("第 3 人 / 共 26 人");

    await user.click(screen.getByRole("button", { name: "中止分析" }));

    expect(confirmDialog).toHaveBeenCalledWith(expect.objectContaining({ confirmText: "中止分析", danger: true }));
    expect(apiPost).toHaveBeenCalledWith("/api/weekly-check/jobs/job_1/cancel", {});
    expect(await screen.findByText("分析已中止，已有结果没有改动；可以重新点“分析”。", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /中止/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "分析" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(screen.getByRole("button", { name: "分析已中止，点击查看" })).toBeInTheDocument();
  });

  it("keeps the analysis running when the stop is not confirmed", async () => {
    mockUser("RM");
    vi.mocked(confirmDialog).mockResolvedValue(false);
    const user = userEvent.setup();
    mockBackend({
      "/api/weekly-check/jobs/job_1": {
        job_id: "job_1", status: "running", message: "已完成 Alice", done: 3, total: 26, week_key: "", error: "", stopping: false,
      },
    });
    vi.mocked(apiFetch).mockResolvedValue(preview);
    vi.mocked(apiPost).mockResolvedValue({
      job_id: "job_1", status: "running", message: "", done: 0, total: 0, week_key: "", error: "", stopping: false,
    });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "上传周报" }));
    await user.upload(screen.getByLabelText("周报文件"), new File(["xlsx"], "plan.xlsx"));
    await user.click(await screen.findByRole("button", { name: "分析" }));
    await user.click(await screen.findByRole("button", { name: "中止分析" }));

    expect(apiPost).not.toHaveBeenCalledWith("/api/weekly-check/jobs/job_1/cancel", {});
    expect(screen.getByRole("button", { name: "中止分析" })).toBeEnabled();
  });

  it("lets RM edit, import and save the manual overrides with their version", async () => {
    mockUser("RM");
    const user = userEvent.setup();
    vi.mocked(apiFetch).mockResolvedValue({ members: [], version: "v2" });
    renderPage();

    await user.click(await screen.findByRole("button", { name: "手工对应" }));
    const table = await screen.findByTestId("weekly-check-member-table");
    await waitFor(() => expect(within(table).getByLabelText("Alice 的 JIRA 用户名")).toHaveValue("alice"));

    // A different case updates the existing row; the third cell marks a sheet 不检查.
    await user.type(
      screen.getByLabelText("批量导入手工对应"),
      "sheet,jira_username\nALICE,a001\nBob,b002\nProject,,不检查",
    );
    await user.click(screen.getByRole("button", { name: "导入到上表" }));
    await user.click(screen.getByRole("button", { name: "添加一行" }));
    await user.type(screen.getByLabelText("第 4 行周报页名"), "Boss");
    await user.click(screen.getByLabelText("不检查 Boss"));
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    const [path, options] = vi.mocked(apiFetch).mock.calls[0];
    expect(path).toBe("/api/weekly-check/members");
    expect(options?.method).toBe("PUT");
    expect(JSON.parse(String(options?.body))).toEqual({
      version: "v1",
      members: [
        { sheet: "ALICE", jira_username: "a001", skip: false, note: "" },
        { sheet: "Bob", jira_username: "b002", skip: false, note: "" },
        { sheet: "Project", jira_username: "", skip: true, note: "" },
        { sheet: "Boss", jira_username: "", skip: true, note: "" },
      ],
    });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "手工对应" })).not.toBeInTheDocument());
  });
});

describe("weekly check helpers", () => {
  it("computes the ISO week key the backend uses", () => {
    expect(isoWeekKey("2026-10-10")).toBe("2026-W41");
    expect(isoWeekKey("2026-10-05")).toBe("2026-W41");
    expect(isoWeekKey("2026-10-12")).toBe("2026-W42");
    expect(isoWeekKey("2027-01-01")).toBe("2026-W53");
    expect(isoWeekKey("2026-01-01")).toBe("2026-W01");
    expect(isoWeekKey("2026-02-30")).toBe("");
    expect(isoWeekKey("10/10")).toBe("");
  });

  it("parses pasted rows with commas or tabs, an optional 不检查 cell, and skips the header", () => {
    expect(
      parseMemberRows("﻿sheet,jira_username\nAlice, a001 \n\nBob\tb002\nCarol\nProject,,不检查\nTemplate,,skip"),
    ).toEqual([
      { sheet: "Alice", jira_username: "a001", skip: false },
      { sheet: "Bob", jira_username: "b002", skip: false },
      { sheet: "Carol", jira_username: "", skip: false },
      { sheet: "Project", jira_username: "", skip: true },
      { sheet: "Template", jira_username: "", skip: true },
    ]);
  });
});
