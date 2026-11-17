import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { QaTestCommandsPane } from "../QaTestCommandsPane";
import { apiGet } from "../../../api/http";

vi.mock("../../../api/http", () => ({ apiGet: vi.fn() }));
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: { username: "qa", role: "QA" } }) }));
const data = { hpc_version: "latest", hpc_versions: ["3.10.0.8", "3.10.0.12"],
  columns: ["app_name", "hpc_version", "website_docker_cmd", "qa_digest", "qa_result"],
  rows: [{ app_name: "EquiformerV2", hpc_version: "3.10.0.8", website_docker_cmd: "older app command", qa_digest: "", qa_result: "" },
    { app_name: "HPL", hpc_version: "3.10.0.12", website_docker_cmd: "newer app command", qa_digest: "", qa_result: "" },
    { app_name: "Missing", hpc_version: "", website_docker_cmd: "docker run missing-maca:1.0-<hpc_version>-ubuntu22.04-amd64 sh -c 'test'", qa_digest: "", qa_result: "" }], errors: [] };
beforeEach(() => vi.clearAllMocks());

it("queries latest manually and displays independently selected versions alongside missing builds", async () => {
  vi.mocked(apiGet).mockResolvedValue(data);
  render(<QaTestCommandsPane releaseId="release1" />);
  expect(apiGet).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "查询测试命令" }));
  expect(await screen.findByText("docker run missing-maca:1.0-<hpc_version>-ubuntu22.04-amd64 sh -c 'test'")).toBeVisible();
  expect(screen.getByText("已按 App／构建分别选择最新版本；实际 hpc_version：3.10.0.8、3.10.0.12")).toBeVisible();
  expect(screen.getByText("EquiformerV2")).toBeVisible();
  expect(screen.getByText("3.10.0.8")).toBeVisible();
  expect(screen.getByText("3.10.0.12")).toBeVisible();
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
  expect(screen.getByLabelText("hpc_version")).toHaveValue("latest");
});
