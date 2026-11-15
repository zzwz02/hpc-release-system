import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { BatchImageLookupPane } from "../BatchImageLookupPane";
import { apiPost } from "../../../api/http";
import { downloadCsv } from "../../../lib/csv";

vi.mock("../../../api/http", () => ({ apiPost: vi.fn() }));
vi.mock("../../../lib/csv", async original => ({ ...await original<typeof import("../../../lib/csv")>(), downloadCsv: vi.fn() }));
let identity = { username: "qa", role: "QA" };
vi.mock("../../../api/AuthContext", () => ({ useAuth: () => ({ user: identity }) }));
const image = "harbor.sh.mxcr.io/hpc-release/pyg-maca@sha256:" + "a".repeat(64);
const data = {
  columns: ["app_name", "GPU", "docker_cmd", "test_result", "test_digest", "test_env"],
  rows: [
    { app_name: "PyG", GPU: "C500", docker_cmd: "docker run " + image, test_result: 0, test_digest: image, test_env: "C500_server_1" },
    { app_name: "PyG", GPU: "X301", docker_cmd: "docker run " + image, test_result: "", test_digest: "", test_env: "" },
  ],
  images: [{ image, app_name: "pyg", app_version: "2.0", hpc_version: "3.10.0", commit_id: "abcdef1", git_url: "repo", tags: ["2.0-abcdef1-ubuntu22.04-amd64"] }],
  duplicate_count: 1, errors: [{ image: "bad", error: "不存在" }],
};

beforeEach(() => { vi.clearAllMocks(); identity = { username: "qa", role: "QA" }; });

it("posts all image lines, shows partial errors and exports flattened numeric and blank results", async () => {
  vi.mocked(apiPost).mockResolvedValue(data);
  render(<BatchImageLookupPane />);
  fireEvent.change(screen.getByLabelText("完整镜像名"), { target: { value: `docker pull ${image}\n${image}\nbad` } });
  fireEvent.click(screen.getByTestId("lookup-search"));
  const table = await screen.findByRole("table", { name: "测试命令" });
  expect(apiPost).toHaveBeenCalledWith("/api/lookup/images", { images: [image, image, "bad"] });
  expect(screen.getByRole("alert")).toHaveTextContent("bad：不存在");
  expect(within(table).getByText("0")).toBeVisible();
  fireEvent.change(screen.getByLabelText("筛选测试命令"), { target: { value: "X301" } });
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  expect(downloadCsv).toHaveBeenCalledWith("digest_test_commands.csv", data.columns, [["PyG", "X301", "docker run " + image, "", "", ""]]);
  fireEvent.change(screen.getByLabelText("筛选测试命令"), { target: { value: "C500" } });
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  expect(vi.mocked(downloadCsv).mock.calls.at(-1)?.[2][0][3]).toBe("0");
});

it("does not keep an old result after a failed new query or identity change", async () => {
  vi.mocked(apiPost).mockResolvedValueOnce(data).mockRejectedValueOnce(new Error("Harbor 超时"));
  const view = render(<BatchImageLookupPane />);
  fireEvent.change(screen.getByLabelText("完整镜像名"), { target: { value: image } });
  fireEvent.click(screen.getByTestId("lookup-search"));
  await screen.findByTestId("lookup-image-result");
  fireEvent.click(screen.getByTestId("lookup-search"));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Harbor 超时"));
  expect(screen.queryByTestId("lookup-image-result")).toBeNull();
  identity = { username: "other", role: "Owner" };
  view.rerender(<BatchImageLookupPane />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(screen.getByLabelText("完整镜像名")).toHaveValue("");
});

it("ignores an old user's delayed response", async () => {
  let finish!: (value: typeof data) => void;
  vi.mocked(apiPost).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const view = render(<BatchImageLookupPane />);
  fireEvent.change(screen.getByLabelText("完整镜像名"), { target: { value: image } });
  fireEvent.click(screen.getByTestId("lookup-search"));
  identity = { username: "other", role: "QA" };
  view.rerender(<BatchImageLookupPane />);
  finish(data);
  await waitFor(() => expect(screen.getByLabelText("完整镜像名")).not.toBeDisabled());
  expect(screen.getByTestId("lookup-search")).toHaveTextContent("查询");
  expect(screen.queryByTestId("lookup-image-result")).toBeNull();
});

it("reads references from CSV cells without treating docker commands as image references", async () => {
  vi.mocked(apiPost).mockResolvedValue(data);
  render(<BatchImageLookupPane />);
  const file = new File([], "images.csv");
  Object.defineProperty(file, "text", { value: async () => `image,docker_cmd\n${image},docker run ${image} sh -c test\n` });
  fireEvent.change(screen.getByLabelText("上传镜像清单"), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByLabelText("完整镜像名")).toHaveValue(image));
  fireEvent.click(screen.getByTestId("lookup-search"));
  await screen.findByTestId("lookup-image-result");
  expect(apiPost).toHaveBeenCalledWith("/api/lookup/images", { images: [image] });
});

it("prefers the digest reference column over a second reference containing tags", async () => {
  vi.mocked(apiPost).mockResolvedValue(data);
  render(<BatchImageLookupPane />);
  const file = new File([], "images.csv");
  Object.defineProperty(file, "text", { value: async () => `应用,镜像@digest,带Tag镜像@digest\nPyG,${image},${image.replace("@sha256:", ":tag@sha256:")}\n` });
  fireEvent.change(screen.getByLabelText("上传镜像清单"), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByLabelText("完整镜像名")).toHaveValue(image));
  fireEvent.click(screen.getByTestId("lookup-search"));
  await screen.findByTestId("lookup-image-result");
  expect(apiPost).toHaveBeenCalledWith("/api/lookup/images", { images: [image] });
});

it("combines QA-style column filters and sorting while exporting full commits and numeric results", async () => {
  const prefix = "0123456789ab";
  const first = prefix + "a".repeat(28);
  const second = prefix + "b".repeat(28);
  vi.mocked(apiPost).mockResolvedValue({ ...data, errors: [], columns: ["app_name", "GPU", "commit_id", "test_result"], rows: [
    { app_name: "PyG", GPU: "C500", commit_id: second, test_result: 10 },
    { app_name: "PyG", GPU: "C500", commit_id: first, test_result: 2 },
    { app_name: "HPL", GPU: "X301", commit_id: second, test_result: 0 },
  ] });
  render(<BatchImageLookupPane />);
  fireEvent.change(screen.getByLabelText("完整镜像名"), { target: { value: image } });
  fireEvent.click(screen.getByTestId("lookup-search"));
  const table = await screen.findByRole("table", { name: "测试命令" });
  fireEvent.change(screen.getByLabelText("筛选测试命令"), { target: { value: "PyG" } });
  fireEvent.change(screen.getByLabelText("GPU 列筛选"), { target: { value: "C500" } });
  fireEvent.click(within(table).getByRole("columnheader", { name: /^test_result$/ }));
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  expect(downloadCsv).toHaveBeenLastCalledWith("digest_test_commands.csv", ["app_name", "GPU", "commit_id", "test_result"], [
    ["PyG", "C500", first, "2"], ["PyG", "C500", second, "10"],
  ]);
  fireEvent.change(screen.getByLabelText("commit_id 列筛选"), { target: { value: "aaaaaa" } });
  expect(within(table).getByText(prefix)).toHaveAttribute("title", first);
  expect(screen.getByText("1 / 3 行")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "导出 CSV" }));
  expect(vi.mocked(downloadCsv).mock.calls.at(-1)?.[2]).toEqual([["PyG", "C500", first, "2"]]);
});
