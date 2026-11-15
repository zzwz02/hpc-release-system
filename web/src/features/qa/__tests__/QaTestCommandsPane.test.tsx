import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QaTestCommandsPane } from "../QaTestCommandsPane";
import { apiGet } from "../../../api/http";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "qa", role: "QA" } }) }));
const data = { hpc_version: "3.10.0.10", columns: ["app_name", "website_docker_cmd", "qa_digest", "qa_result"],
  rows: [{ app_name: "HPL", website_docker_cmd: "no_image", qa_digest: "", qa_result: "" }], errors: [] };
beforeEach(() => vi.clearAllMocks());

it("queries latest manually, displays its resolved numeric version and missing-image rows", async () => {
  vi.mocked(apiGet).mockResolvedValue(data);
  render(<QaTestCommandsPane releaseId="release1" />);
  expect(apiGet).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByText("no_image")).toBeVisible();
  expect(screen.getByText("实际 hpc_version：3.10.0.10")).toBeVisible();
  expect(apiGet).toHaveBeenCalledWith("/api/qa/test-commands?release_id=release1&hpc_version=latest");
  fireEvent.change(screen.getByLabelText("hpc_version"), { target: { value: "3.10.0.7" } });
  expect(screen.queryByRole("table")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  await waitFor(() => expect(apiGet).toHaveBeenLastCalledWith("/api/qa/test-commands?release_id=release1&hpc_version=3.10.0.7"));
});

it("clears old results on failure and discards a response after changing release", async () => {
  vi.mocked(apiGet).mockResolvedValueOnce(data).mockRejectedValueOnce(new Error("Harbor 查询失败"));
  const view = render(<QaTestCommandsPane releaseId="release1" />);
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  await screen.findByText("no_image");
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Harbor 查询失败");
  expect(screen.queryByRole("table")).toBeNull();
  let resolve: (value: unknown) => void = () => {};
  vi.mocked(apiGet).mockImplementation(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  view.rerender(<QaTestCommandsPane releaseId="release2" />);
  resolve(data);
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  expect(screen.getByLabelText("hpc_version")).toHaveValue("latest");
});
