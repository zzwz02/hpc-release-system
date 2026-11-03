import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, apiPost } from "../../../api/http";
import { confirmDialog } from "../../../lib/confirm";
import { CommentPublication } from "../CommentPublication";
import { ArtifactLink } from "../ArtifactPreview";
import type { AgentFile, AgentTurn } from "../jiraAgentApi";

vi.mock("../../../api/http", () => ({ apiFetch: vi.fn(), apiPost: vi.fn() }));
vi.mock("../../../lib/confirm", () => ({ confirmDialog: vi.fn() }));
const generated = "*生成的结论*\n\n关键证据。";
const turn = { id: "jat_1", conversation_id: "jac_1", status: "completed", delivery_status: "complete", comment_status: "",
  comment_body: generated } as AgentTurn;
const file = { id: "file_1", conversation_id: "jac_1", turn_id: "jat_1", source: "artifact", name: "kernel.cu", downloadable: true } as AgentFile;
let current: { body: string; frozen: boolean; revision: number; selected_file_ids: string[]; uploads: unknown[]; turn: AgentTurn };

beforeEach(() => {
  vi.resetAllMocks();
  current = { body: generated, frozen: false, revision: 1, selected_file_ids: [], uploads: [], turn };
  vi.mocked(apiFetch).mockImplementation(async () => current);
  vi.mocked(apiPost).mockImplementation(async (path, raw) => {
    const data = raw as { body: string; file_ids: string[]; revision: number };
    current = { ...current, body: data.body, selected_file_ids: data.file_ids, revision: current.revision + 1 };
    if (path.endsWith("/send")) current = { ...current, frozen: true,
      uploads: data.file_ids.map(file_id => ({ file_id, name: file.name, state: "posted" })),
      turn: { ...turn, comment_status: "posted", comment_id: "123" } };
    return current;
  });
  vi.mocked(confirmDialog).mockResolvedValue(false);
});

describe("reviewed publication", () => {
  it("shows the generated conclusion read-only and edits/selects files only after Send to JIRA", async () => {
    const user = userEvent.setup();
    const changed = vi.fn();
    render(<CommentPublication turn={turn} files={[file]} onChanged={changed} />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成评论/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "发送到 JIRA" }));
    const editor = await screen.findByLabelText("JIRA 评论内容");
    await waitFor(() => expect(editor).toHaveValue(generated));
    expect(apiPost).not.toHaveBeenCalled();
    const checkbox = screen.getByRole("checkbox", { name: "kernel.cu" });
    expect(checkbox).not.toBeChecked();
    await user.clear(editor); await user.type(editor, "人工核对后的评论。");
    await user.click(checkbox);
    await user.click(screen.getByRole("button", { name: "确认发送并上传 1 个附件" }));
    expect(apiPost).toHaveBeenCalledTimes(1);
    expect(apiPost).toHaveBeenCalledWith("/api/jira-agent/turns/jat_1/comment/send", {
      body: "人工核对后的评论。", file_ids: ["file_1"], revision: 1,
    });
    expect(await screen.findByText(/已发送 JIRA 评论（#123）/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /确认发送|核对并重试发送/ })).not.toBeInTheDocument();
    expect(editor).toBeDisabled(); expect(changed).toHaveBeenCalled();
  });

  it("discards unsent edits and attachment choices on close without saving", async () => {
    const user = userEvent.setup();
    render(<CommentPublication turn={turn} files={[file]} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: "发送到 JIRA" }));
    const editor = await screen.findByLabelText("JIRA 评论内容");
    await waitFor(() => expect(editor).toBeEnabled());
    await user.type(editor, "未发送修改");
    await user.click(screen.getByRole("checkbox", { name: "kernel.cu" }));
    await new Promise(resolve => setTimeout(resolve, 1000));
    expect(apiPost).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(apiPost).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "发送到 JIRA" }));
    await waitFor(() => expect(screen.getByLabelText("JIRA 评论内容")).toHaveValue(generated));
    expect(screen.getByRole("checkbox", { name: "kernel.cu" })).not.toBeChecked();
  });

  it.each(["", "alice"])("shows persisted publication attribution: %s", editedBy => {
    render(<CommentPublication turn={{ ...turn, comment_status: "posted", comment_id: "123", comment_edited_by: editedBy }}
      files={[]} browseUrl="https://jira.example/MC3-1" onChanged={() => {}} />);
    if (editedBy) expect(screen.getByText("alice").closest("span")).toHaveTextContent("已由alice编辑并发送到 JIRA");
    else expect(screen.getByText("已发布到 Jira")).toBeVisible();
    expect(screen.getByRole("link", { name: "#123" })).toHaveAttribute("href", "https://jira.example/MC3-1?focusedCommentId=123#comment-123");
  });

  it("loads an already published turn as view-only without starting a model", async () => {
    const posted = { ...turn, comment_status: "posted" as const, comment_id: "123", comment_body: "已发布正文" };
    vi.mocked(apiFetch).mockResolvedValue({ ...current, body: "已发布正文", frozen: true, turn: posted });
    const user = userEvent.setup();
    render(<CommentPublication turn={posted} files={[]} onChanged={() => {}} />);
    expect(screen.queryByRole("button", { name: "发送到 JIRA" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看编辑历史" }));
    expect(await screen.findByText(/已发送 JIRA 评论（#123）/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /生成评论/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("JIRA 评论内容")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "确认发送" })).not.toBeInTheDocument();
  });

  it("preserves unsaved text on a version conflict and blocks publication", async () => {
    const user = userEvent.setup();
    render(<CommentPublication turn={turn} files={[]} onChanged={() => {}} />);
    await user.click(screen.getByRole("button", { name: "发送到 JIRA" }));
    const editor = await screen.findByLabelText("JIRA 评论内容");
    await waitFor(() => expect(editor).toBeEnabled());
    await user.type(editor, "本窗口编辑");
    vi.mocked(apiPost).mockRejectedValue(new Error("评论已被其他用户修改"));
    vi.mocked(apiFetch).mockResolvedValue({ ...current, revision: 2 });
    await user.click(screen.getByRole("button", { name: "确认发送" }));
    expect(await screen.findByText("评论已被其他用户修改")).toBeVisible();
    expect((editor as HTMLTextAreaElement).value).toContain("本窗口编辑");
    expect(screen.getByRole("button", { name: "确认发送" })).toBeDisabled();
  });
});

describe("artifact previews", () => {
  it.each([
    ["markdown", "# Rendered heading\n<script>bad()</script>", ""],
    ["code", "__global__ void kernel() {}", "cpp"],
    ["text", "plain log", ""],
    ["binary", "", ""],
  ])("previews %s safely and offers the original download", async (kind, text, language) => {
    vi.mocked(apiFetch).mockResolvedValue({ kind, text, language, truncated: false });
    const user = userEvent.setup();
    const { container } = render(<ArtifactLink file={file} />);
    await user.click(screen.getByRole("button", { name: file.name }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).queryByText("正在读取文件…")).not.toBeInTheDocument());
    expect(within(dialog).getByRole("link", { name: "下载此文件" })).toHaveAttribute("href", "/api/jira-agent/conversations/jac_1/files/file_1");
    expect(container.querySelector("script")).toBeNull();
    if (kind === "markdown") expect(within(dialog).getByRole("heading", { name: "Rendered heading" })).toBeVisible();
    if (kind === "code") expect(container.querySelector(".hljs span")).not.toBeNull();
    if (kind === "text") expect(within(dialog).getByText(text)).toBeVisible();
    if (kind === "binary") expect(within(dialog).getByText(/无法预览/)).toBeVisible();
  });
});
