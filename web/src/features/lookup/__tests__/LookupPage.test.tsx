import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { LookupPage } from "../LookupPage";
import { apiGet } from "../../../api/http";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "owner", role: "Owner" } }) }));

const releases = [
  { id: "recent", name: "3.10.0", release_date: "2026-11-12" },
  { id: "old", name: "3.9.0", release_date: "2026-09-24" },
];
const image = "harbor.sh.mxcr.io/hpc-release/hpl-maca@sha256:" + "a".repeat(64);
const app = {
  app_id: "hpl", app_name: "HPL", app_version: "2023.0807", image_reference: image,
  deliverable_image: image, deliverable_digest: "", qa_status: "qa_passed", qa_note: "",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><LookupPage /></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => vi.clearAllMocks());

it("defaults to frozen release publications and filters by image or QA result", async () => {
  vi.mocked(apiGet).mockImplementation(async (url) => {
    if (url === "/api/lookup/releases") return { releases };
    if (url.endsWith("release_id=recent")) return { release: releases[0], apps: [app] };
    throw new Error(`Unexpected request ${url}`);
  });
  renderPage();
  const table = await screen.findByTestId("published-apps-table");
  expect(screen.queryByRole("button", { name: "按 App" })).toBeNull();
  expect(screen.queryByRole("textbox", { name: "完整镜像名" })).toBeNull();
  expect(within(table).getByText(image)).toBeVisible();
  const filter = screen.getByRole("textbox", { name: "筛选发布 App" });
  fireEvent.change(filter, { target: { value: "sha256:" } });
  expect(within(table).getByText("HPL")).toBeVisible();
  fireEvent.change(filter, { target: { value: "存在问题" } });
  expect(within(table).getByText("没有匹配的 App。")).toBeVisible();
});

it("does not show another release's rows while the selected version is loading or fails", async () => {
  let rejectOld!: (reason: Error) => void;
  const pending = new Promise((_, reject) => { rejectOld = reject; });
  vi.mocked(apiGet).mockImplementation(async (url) => {
    if (url === "/api/lookup/releases") return { releases };
    if (url.endsWith("release_id=recent")) return { release: releases[0], apps: [app] };
    if (url.endsWith("release_id=old")) return pending;
    throw new Error(`Unexpected request ${url}`);
  });
  renderPage();
  await screen.findByTestId("published-apps-table");
  fireEvent.change(screen.getByRole("combobox", { name: "发布版本" }), { target: { value: "old" } });
  await waitFor(() => expect(screen.queryByTestId("published-apps-table")).toBeNull());
  rejectOld(new Error("版本已解锁"));
  expect(await screen.findByRole("alert")).toHaveTextContent("版本已解锁");
  expect(screen.queryByTestId("published-apps-table")).toBeNull();
});

it("handles no frozen versions and refreshes without requesting an empty release id", async () => {
  vi.mocked(apiGet).mockImplementation(async (url) => {
    if (url === "/api/lookup/releases") return { releases: [] };
    throw new Error(`Unexpected request ${url}`);
  });
  renderPage();
  await screen.findByText("暂无已冻结版本。");
  fireEvent.click(screen.getByRole("button", { name: "刷新当前页" }));
  await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(2));
  expect(vi.mocked(apiGet).mock.calls.every(([url]) => url === "/api/lookup/releases")).toBe(true);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("refresh removes an unlocked selected version and loads the remaining frozen version", async () => {
  let unlocked = false;
  vi.mocked(apiGet).mockImplementation(async (url) => {
    if (url === "/api/lookup/releases") return { releases: unlocked ? [releases[1]] : releases };
    if (url.endsWith("release_id=recent")) {
      if (unlocked) throw new Error("版本已解锁");
      return { release: releases[0], apps: [app] };
    }
    if (url.endsWith("release_id=old")) return { release: releases[1], apps: [] };
    throw new Error(`Unexpected request ${url}`);
  });
  renderPage();
  await screen.findByTestId("published-apps-table");
  unlocked = true;
  fireEvent.click(screen.getByRole("button", { name: "刷新当前页" }));
  expect(await screen.findByText("该版本没有发布成功的 App。")).toBeVisible();
  expect(screen.getByRole("combobox", { name: "发布版本" })).toHaveValue("old");
  expect(screen.queryByText(image)).toBeNull();
});
