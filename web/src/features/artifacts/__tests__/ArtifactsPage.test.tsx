/**
 * ArtifactsPage tests.
 *
 * Covers:
 *  - Shows "请先选择 Release" when no release selected
 *  - Shows kind buttons for all 4 non-manager artifact kinds
 *  - Manager Review CSV button visible for RM, hidden for Guest
 *  - Refresh button visible for RM/Owner, hidden for Guest
 *  - Clicking a kind button triggers fetchArtifact (apiGetText)
 *  - Artifact viewer renders source textarea for plain-text kind (data)
 *  - Artifact viewer renders source/render toggle for Markdown kind
 *  - Manager review pane renders field picker with default fields checked
 *  - test-scope.csv download button visible for RM only
 *  - Error state shown when fetchArtifact fails
 *  - RefreshBar rendered
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ArtifactsPage } from "../ArtifactsPage";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("../../../api/http", () => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  apiGetText: vi.fn(),
}));

vi.mock("../../../api/AuthContext", () => ({
  useAuth: vi.fn(),
}));

// uiStore mock — supports both useUiStore() and useUiStore((s) => s.x)
let _uiState = { selectedReleaseId: "rel-1" };

vi.mock("../../../store/uiStore", () => {
  const store = (selector?: (s: typeof _uiState) => unknown) =>
    selector ? selector(_uiState) : _uiState;
  store.getState = () => _uiState;
  return {
    useUiStore: Object.assign(store, { getState: () => _uiState }),
  };
});

// Mock renderMarkdown to avoid jsdom/DOMPurify complexity
vi.mock("../../../lib/markdown", () => ({
  renderMarkdown: vi.fn(() => ({ html: "<div>rendered</div>", outline: [] })),
}));

import { apiGetText, apiPost } from "../../../api/http";
import { useAuth } from "../../../api/AuthContext";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setupAuth(role = "RM") {
  vi.mocked(useAuth).mockReturnValue({
    user: { username: "alice", display_name: "Alice", role },
    ldapStatus: { enabled: false, uri: "" },
    login: vi.fn(),
    logout: vi.fn(),
    clearUser: vi.fn(),
  } as unknown as ReturnType<typeof useAuth>);
}

function renderPage(role = "RM", releaseId = "rel-1") {
  _uiState = { selectedReleaseId: releaseId };
  setupAuth(role);

  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ArtifactsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function defaultTextMock() {
  vi.mocked(apiGetText).mockResolvedValue({
    text: "# Hello\nContent here.",
    headers: new Headers({
      "X-Artifact-Name": "release_note.md",
      "X-Artifact-Generated-At": "2026-06-01 10:00:00",
    }),
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ArtifactsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    defaultTextMock();
    vi.mocked(apiPost).mockResolvedValue({ artifacts: [] });
    _uiState = { selectedReleaseId: "rel-1" };
  });

  // ── Release selector ───────────────────────────────────────────────────────

  it("shows 'no release selected' message when selectedReleaseId is empty", () => {
    renderPage("RM", "");
    expect(screen.getByText(/请先.*Release/)).toBeInTheDocument();
  });

  // ── Kind selector buttons ──────────────────────────────────────────────────

  it("shows all 4 non-manager kind buttons", () => {
    renderPage("RM");
    expect(screen.getByText(/查看 Release Note/)).toBeInTheDocument();
    expect(screen.getByText(/查看 HPC Manual/)).toBeInTheDocument();
    expect(screen.getByText(/查看 AI4Sci Manual/)).toBeInTheDocument();
    expect(screen.getByText(/查看 release-data/)).toBeInTheDocument();
  });

  it("shows Manager Review CSV button for RM", () => {
    renderPage("RM");
    expect(screen.getByText("Manager Review CSV")).toBeInTheDocument();
  });

  it("hides Manager Review CSV button for Guest", () => {
    renderPage("Guest");
    expect(screen.queryByText("Manager Review CSV")).not.toBeInTheDocument();
  });

  // ── Refresh button ─────────────────────────────────────────────────────────

  it("shows Refresh button for RM", () => {
    renderPage("RM");
    expect(screen.getByText("刷新")).toBeInTheDocument();
  });

  it("hides Refresh button for Guest (cannot generate)", () => {
    renderPage("Guest");
    expect(screen.queryByText("刷新")).not.toBeInTheDocument();
  });

  // ── test-scope.csv download button ────────────────────────────────────────

  it("shows test-scope.csv download button for RM", () => {
    renderPage("RM");
    expect(screen.getByText(/下载 test-scope.csv/)).toBeInTheDocument();
  });

  it("hides test-scope.csv download button for Guest", () => {
    renderPage("Guest");
    expect(screen.queryByText(/下载 test-scope.csv/)).not.toBeInTheDocument();
  });

  // ── Clicking a kind button fetches artifact ────────────────────────────────

  it("clicking Release Note button triggers apiGetText for release_note", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText(/查看 Release Note/));

    await waitFor(() => {
      expect(vi.mocked(apiGetText)).toHaveBeenCalledWith(
        expect.stringContaining("/api/artifacts/release_note"),
      );
    });
  });

  it("clicking data button triggers apiGetText for data artifact", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText(/查看 release-data/));

    await waitFor(() => {
      expect(vi.mocked(apiGetText)).toHaveBeenCalledWith(
        expect.stringContaining("/api/artifacts/data"),
      );
    });
  });

  // ── Viewer renders after fetch ─────────────────────────────────────────────

  it("renders source textarea after clicking data kind", async () => {
    vi.mocked(apiGetText).mockResolvedValue({
      text: "plain text content",
      headers: new Headers({
        "X-Artifact-Name": "data.txt",
        "X-Artifact-Generated-At": "2026-06-01 10:00:00",
      }),
    });

    renderPage("RM");
    await userEvent.click(screen.getByText(/查看 release-data/));

    await waitFor(() => {
      const ta = document.querySelector("textarea.artifact");
      expect(ta).not.toBeNull();
      expect((ta as HTMLTextAreaElement).value).toBe("plain text content");
    });
  });

  it("renders source/render toggle button for Markdown kind", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText(/查看 Release Note/));

    await waitFor(() => {
      // In render mode the toggle shows "查看源码"
      expect(screen.getByText(/查看源码|查看渲染/)).toBeInTheDocument();
    });
  });

  // ── Error state ────────────────────────────────────────────────────────────

  it("shows error message when fetchArtifact fails", async () => {
    vi.mocked(apiGetText).mockRejectedValue(new Error("网络错误"));

    renderPage("RM");
    await userEvent.click(screen.getByText(/查看 Release Note/));

    await waitFor(() => {
      expect(screen.getByText(/加载失败.*网络错误/)).toBeInTheDocument();
    });
  });

  // ── Manager review pane ────────────────────────────────────────────────────

  it("clicking Manager Review CSV shows field picker pane", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText("Manager Review CSV"));

    await waitFor(() => {
      expect(screen.getByText(/请选择需要输出的字段/)).toBeInTheDocument();
    });
  });

  it("manager review pane has App checkbox checked by default", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText("Manager Review CSV"));

    await waitFor(() => {
      const appCheckbox = screen.getByRole("checkbox", { name: "App" });
      expect((appCheckbox as HTMLInputElement).checked).toBe(true);
    });
  });

  it("manager review pane has Owner checkbox checked by default", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText("Manager Review CSV"));

    await waitFor(() => {
      const ownerCheckbox = screen.getByRole("checkbox", { name: "Owner" });
      expect((ownerCheckbox as HTMLInputElement).checked).toBe(true);
    });
  });

  it("manager review pane has 官方名称 unchecked by default", async () => {
    renderPage("RM");
    await userEvent.click(screen.getByText("Manager Review CSV"));

    await waitFor(() => {
      const checkbox = screen.getByRole("checkbox", { name: "官方名称" });
      expect((checkbox as HTMLInputElement).checked).toBe(false);
    });
  });

  // ── RefreshBar ─────────────────────────────────────────────────────────────

  it("renders RefreshBar with 刷新当前页 button", () => {
    renderPage("RM");
    expect(screen.getByRole("button", { name: /刷新当前页/ })).toBeInTheDocument();
  });
});
