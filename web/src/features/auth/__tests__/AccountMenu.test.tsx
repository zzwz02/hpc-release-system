import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AccountMenu } from "../AccountMenu";

const user = { username: "hajiang", display_name: "Haiyang Jiang", role: "Owner" };

describe("Account settings menu", () => {
  it("groups account actions and shows the existing role without inventing a department", async () => {
    render(<AccountMenu user={user} logout={vi.fn()} />);
    const actor = userEvent.setup();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument();
    await actor.click(screen.getByRole("button", { name: "账号设置" }));
    expect(screen.getAllByRole("menuitem")).toHaveLength(3);
    await actor.click(screen.getByRole("menuitem", { name: "账号信息" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "账号信息" })).toBeInTheDocument();
    expect(screen.getByText("Haiyang Jiang")).toBeInTheDocument();
    expect(screen.getByText("hajiang")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
    expect(screen.getByText("权限角色")).toBeInTheDocument();
    await actor.keyboard("{Escape}");
    await waitFor(() => expect(screen.getByRole("button", { name: "账号设置" })).toHaveFocus());
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("supports keyboard navigation and dismissal outside the menu", async () => {
    render(<AccountMenu user={user} logout={vi.fn()} />);
    const actor = userEvent.setup();
    screen.getByRole("button", { name: "账号设置" }).focus();
    await actor.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "账号信息" })).toHaveFocus();
    await actor.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "API Token" })).toHaveFocus();
    await actor.keyboard("{End}");
    expect(screen.getByRole("menuitem", { name: "退出登录" })).toHaveFocus();
    await actor.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "账号设置" })).toHaveFocus();
    await actor.click(screen.getByRole("button", { name: "账号设置" }));
    await actor.click(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
  it("uses the existing logout callback and reports failures", async () => {
    const logout = vi.fn().mockRejectedValueOnce(new Error("网络异常")).mockResolvedValue(undefined);
    render(<AccountMenu user={user} logout={logout} />);
    const actor = userEvent.setup();
    await actor.click(screen.getByRole("button", { name: "账号设置" }));
    await actor.click(screen.getByRole("menuitem", { name: "退出登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("网络异常");
    await actor.click(screen.getByRole("menuitem", { name: "退出登录" }));
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(logout).toHaveBeenCalledTimes(2);
  });
});
