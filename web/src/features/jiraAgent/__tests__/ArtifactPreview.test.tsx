import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "../../../api/http";
import { ArtifactLink } from "../ArtifactPreview";
import type { AgentFile } from "../jiraAgentApi";
vi.mock("../../../api/http", () => ({ apiFetch: vi.fn() }));
const file = { id: "file_1", conversation_id: "jac_1", turn_id: "jat_1", source: "artifact", name: "kernel.cu", downloadable: true } as AgentFile;
beforeEach(() => vi.resetAllMocks());

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
