import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiGet, apiPost } from "../../../api/http";
import { JenkinsFailuresPage } from "../JenkinsFailuresPage";

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

const failureRecord = {
  id: 101,
  job_name: "Hpc_App_Release",
  build_number: 88,
  build_url: "http://jenkins.example/job/Hpc_App_Release/88/",
  failed_stage: "build",
  normalized_stage: "build_image",
  job_type: "release",
  maca_project: "mxgpu",
  maca_version: "maca-3.1",
  chip: "C500",
  owner_role: "DevOps",
  code_owner: "alice",
  owner_account: "bob",
  official_name: "HPCG",
  app_version: "1.0",
  git_url: "hpc/hpcg",
  git_branch: "main",
  matched_rule_id: "BUILD_IMAGE_PUSH",
  reason_summary: "镜像推送失败",
  key_error_snippet: "denied: requested access to the resource is denied",
  action_suggestion: "检查镜像仓库权限",
  route_reason: "命中 build image push 规则",
  final_owner_account: null,
  final_owner_role: null,
  final_reason_summary: null,
  final_action_suggestion: null,
  responsibility_status: "ai_suggested",
  responsibility_status_label: "AI建议",
  responsibility_updated_by: null,
  responsibility_updated_at: null,
  responsibility_note: null,
  effective_owner_account: "bob",
  effective_owner_role: "DevOps",
  effective_reason_summary: "镜像推送失败",
  effective_action_suggestion: "检查镜像仓库权限",
  responsibility_events: [],
  created_at: "2026-09-04T12:00:00",
};

function renderPage() {
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "alice", display_name: "Alice", role: "RM" },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);

  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <JenkinsFailuresPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("JenkinsFailuresPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/state") {
        return {
          user_display_names: {
            alice: "Alice",
            bob: "Bob",
            m00930: "SPD Config",
          },
        };
      }
      if (path.startsWith("/api/cicd-agent/failures/filter-options")) {
        return {
          job_types: ["release"],
          stages: ["build_image"],
          owner_roles: ["DevOps"],
          owners: [{ code_owner: "alice" }],
          owner_accounts: ["bob"],
          git_urls: ["hpc/hpcg"],
          git_branches: ["main"],
          maca_projects: ["mxgpu"],
          maca_versions: ["maca-3.1"],
          arches: [],
          chips: ["C500"],
          failure_categories: [],
          matched_rule_ids: ["BUILD_IMAGE_PUSH"],
          notification_statuses: [],
          confidences: [],
          responsibility_statuses: ["ai_suggested", "disputed", "corrected"],
          job_names: ["Hpc_App_Release"],
        };
      }
      if (path.startsWith("/api/cicd-agent/failures/summary")) {
        return {
          group_by: "owner_role",
          total_records: 1,
          groups: [
            {
              group: "DevOps",
              failure_count: 1,
              job_count: 1,
              project_count: 1,
              human_review_count: 0,
            },
          ],
        };
      }
      if (path === "/api/cicd-agent/failures/101") {
        return failureRecord;
      }
      if (path.startsWith("/api/cicd-agent/failures")) {
        return {
          page: 1,
          page_size: 20,
          total: 1,
          total_pages: 1,
          records: [failureRecord],
        };
      }
      throw new Error(`Unhandled apiGet path: ${path}`);
    });
    vi.mocked(apiPost).mockImplementation(async (path: string, body: unknown) => {
      if (path === "/api/cicd-agent/failures/101/responsibility-feedback") {
        return {
          record: {
            ...failureRecord,
            responsibility_status: "disputed",
            responsibility_status_label: "有争议",
            responsibility_note: (body as { reason?: string }).reason,
          },
        };
      }
      if (path === "/api/cicd-agent/failures/101/responsibility-resolution") {
        const payload = body as {
          final_owner_account?: string;
          final_owner_role?: string;
          final_reason_summary?: string;
          final_action_suggestion?: string;
        };
        return {
          record: {
            ...failureRecord,
            responsibility_status: "corrected",
            responsibility_status_label: "已修正",
            final_owner_account: payload.final_owner_account,
            final_owner_role: payload.final_owner_role,
            final_reason_summary: payload.final_reason_summary,
            final_action_suggestion: payload.final_action_suggestion,
            effective_owner_account: payload.final_owner_account,
            effective_owner_role: payload.final_owner_role,
            effective_reason_summary: payload.final_reason_summary,
            effective_action_suggestion: payload.final_action_suggestion,
          },
        };
      }
      throw new Error(`Unhandled apiPost path: ${path}`);
    });
  });

  it("opens failure detail in a dialog and closes it", async () => {
    renderPage();

    const table = await screen.findByRole("table");
    expect(within(table).queryByText("AI建议")).not.toBeInTheDocument();

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "记录详情" });
    expect(dialog).toHaveTextContent("AI建议");
    expect(dialog).toHaveTextContent("Hpc_App_Release #88");
    expect(dialog).toHaveTextContent("镜像推送失败");
    expect(dialog).not.toHaveTextContent("命中 build image push 规则");

    await userEvent.click(screen.getByRole("button", { name: "责任链" }));

    expect(dialog).toHaveTextContent("命中 build image push 规则");

    await userEvent.click(screen.getByRole("button", { name: "关闭" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "记录详情" })).not.toBeInTheDocument();
    });
  });

  it("submits responsibility feedback from the detail dialog", async () => {
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await userEvent.click(screen.getByRole("button", { name: "责任反馈" }));
    await userEvent.selectOptions(screen.getByLabelText("建议责任归属"), "Code Owner");
    await userEvent.type(screen.getByLabelText("反馈原因"), "不是我的责任，日志指向业务脚本");
    await userEvent.type(screen.getByLabelText("证据/补充日志"), "make app failed");
    await userEvent.click(screen.getByRole("button", { name: "提交反馈" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/api/cicd-agent/failures/101/responsibility-feedback",
        expect.objectContaining({
          feedback_type: "owner_wrong",
          reason: "不是我的责任，日志指向业务脚本",
          suggested_owner_account: "alice",
          suggested_owner_role: "Code Owner",
          evidence: "make app failed",
        }),
      );
    });
  });

  it("lets RM correct the final owner from the detail dialog", async () => {
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await userEvent.click(screen.getByRole("button", { name: "处理责任" }));
    await userEvent.selectOptions(screen.getByLabelText("处理方式"), "correct");
    await userEvent.selectOptions(screen.getByLabelText("最终责任归属"), "Code Owner");
    await userEvent.type(screen.getByLabelText("最终原因摘要"), "业务脚本编译失败");
    await userEvent.type(screen.getByLabelText("最终处理建议"), "修复编译脚本");
    await userEvent.type(screen.getByLabelText("处理备注"), "RM 已确认");
    await userEvent.click(screen.getByRole("button", { name: "保存处理" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/api/cicd-agent/failures/101/responsibility-resolution",
        expect.objectContaining({
          action: "correct",
          final_owner_account: "alice",
          final_owner_role: "Code Owner",
          final_reason_summary: "业务脚本编译失败",
          final_action_suggestion: "修复编译脚本",
          note: "RM 已确认",
        }),
      );
    });
  });

  it("defaults RM resolution to the latest feedback suggestion", async () => {
    const disputedRecord = {
      ...failureRecord,
      responsibility_status: "disputed",
      responsibility_status_label: "有争议",
      responsibility_events: [
        {
          id: 1,
          failure_id: 101,
          event_type: "feedback_submitted",
          actor: "bob",
          actor_role: "Owner",
          from_status: "ai_suggested",
          to_status: "disputed",
          previous_owner_account: "bob",
          previous_owner_role: "DevOps",
          new_owner_account: "m00930",
          new_owner_role: "DevOps",
          feedback_type: "owner_wrong",
          suggested_owner_account: "m00930",
          suggested_owner_role: "DevOps",
          reason: "应该由 DevOps 处理",
          evidence: "",
          created_at: "2026-09-04T12:30:00",
        },
      ],
    };
    vi.mocked(apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/state") {
        return {
          user_display_names: {
            alice: "Alice",
            bob: "Bob",
            m00930: "SPD Config",
          },
        };
      }
      if (path.startsWith("/api/cicd-agent/failures/filter-options")) {
        return {
          job_types: ["release"],
          stages: ["build_image"],
          owner_roles: ["DevOps"],
          owners: [{ code_owner: "alice" }],
          owner_accounts: ["bob", "m00930"],
          git_urls: ["hpc/hpcg"],
          git_branches: ["main"],
          maca_projects: ["mxgpu"],
          maca_versions: ["maca-3.1"],
          arches: [],
          chips: ["C500"],
          failure_categories: [],
          matched_rule_ids: ["BUILD_IMAGE_PUSH"],
          notification_statuses: [],
          confidences: [],
          responsibility_statuses: ["ai_suggested", "disputed", "corrected"],
          job_names: ["Hpc_App_Release"],
        };
      }
      if (path.startsWith("/api/cicd-agent/failures/summary")) {
        return {
          group_by: "owner_role",
          total_records: 1,
          groups: [],
        };
      }
      if (path === "/api/cicd-agent/failures/101") {
        return disputedRecord;
      }
      if (path.startsWith("/api/cicd-agent/failures")) {
        return {
          page: 1,
          page_size: 20,
          total: 1,
          total_pages: 1,
          records: [disputedRecord],
        };
      }
      throw new Error(`Unhandled apiGet path: ${path}`);
    });

    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));
    await userEvent.click(screen.getByRole("button", { name: "处理责任" }));
    expect(screen.getByLabelText("处理方式")).toHaveValue("correct");
    expect(screen.getByLabelText("最终责任归属")).toHaveValue("DevOps");
    await userEvent.click(screen.getByRole("button", { name: "保存处理" }));

    await waitFor(() => {
      expect(apiPost).toHaveBeenCalledWith(
        "/api/cicd-agent/failures/101/responsibility-resolution",
        expect.objectContaining({
          action: "correct",
          final_owner_account: "m00930",
          final_owner_role: "DevOps",
        }),
      );
    });
  });
});
