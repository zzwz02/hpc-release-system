import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuth } from "../../../api/AuthContext";
import { apiGet } from "../../../api/http";
import { JenkinsFailuresPage } from "../JenkinsFailuresPage";

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
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
  });

  it("opens failure detail in a dialog and closes it", async () => {
    renderPage();

    await userEvent.click(await screen.findByRole("button", { name: "查看详情" }));

    const dialog = await screen.findByRole("dialog", { name: "记录详情" });
    expect(dialog).toHaveTextContent("Hpc_App_Release #88");
    expect(dialog).toHaveTextContent("镜像推送失败");
    expect(dialog).toHaveTextContent("命中 build image push 规则");

    await userEvent.click(screen.getByRole("button", { name: "关闭" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "记录详情" })).not.toBeInTheDocument();
    });
  });
});
