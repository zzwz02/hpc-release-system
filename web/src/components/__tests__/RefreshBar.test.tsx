/**
 * Tests for RefreshBar component.
 *
 * Verifies:
 *  - "尚未刷新" shown when dataUpdatedAt is 0/undefined
 *  - "刷新于 <time>" shown when dataUpdatedAt is non-zero
 *  - The time shown is from formatClientFetchTime (content-fetch time, not page load)
 *  - Refresh button calls onRefresh
 *  - Refresh button disabled when isFetching=true
 */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { RefreshBar } from "../RefreshBar";

describe("RefreshBar", () => {
  it("shows '尚未刷新' when dataUpdatedAt is undefined", () => {
    render(<RefreshBar onRefresh={vi.fn()} />);
    expect(screen.getByTestId("refresh-meta")).toHaveTextContent("尚未刷新");
  });

  it("shows '尚未刷新' when dataUpdatedAt is 0", () => {
    render(<RefreshBar dataUpdatedAt={0} onRefresh={vi.fn()} />);
    expect(screen.getByTestId("refresh-meta")).toHaveTextContent("尚未刷新");
  });

  it("shows '刷新于' prefix with formatted time when dataUpdatedAt is set", () => {
    // Use a known epoch: 2026-01-15 10:30:45 in local time
    // We just check the prefix and that it doesn't say 尚未刷新
    const ts = new Date("2026-01-15T10:30:45").getTime();
    render(<RefreshBar dataUpdatedAt={ts} onRefresh={vi.fn()} />);
    const meta = screen.getByTestId("refresh-meta");
    expect(meta.textContent).toMatch(/^刷新于 /);
    // Should contain the date part
    expect(meta.textContent).toContain("2026-01-15");
  });

  it("time shown uses formatClientFetchTime (local clock), not page-load time", () => {
    // Two different timestamps should produce two different displayed times
    const t1 = new Date("2026-03-01T08:00:00").getTime();
    const t2 = new Date("2026-03-01T09:00:00").getTime();

    const { rerender } = render(<RefreshBar dataUpdatedAt={t1} onRefresh={vi.fn()} />);
    const text1 = screen.getByTestId("refresh-meta").textContent;

    rerender(<RefreshBar dataUpdatedAt={t2} onRefresh={vi.fn()} />);
    const text2 = screen.getByTestId("refresh-meta").textContent;

    expect(text1).not.toBe(text2);
    expect(text1).toMatch(/^刷新于 /);
    expect(text2).toMatch(/^刷新于 /);
  });

  it("calls onRefresh when refresh button is clicked", () => {
    const onRefresh = vi.fn();
    render(<RefreshBar dataUpdatedAt={Date.now()} onRefresh={onRefresh} />);
    fireEvent.click(screen.getByTestId("refresh-btn"));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("disables the button when isFetching=true", () => {
    render(<RefreshBar dataUpdatedAt={Date.now()} onRefresh={vi.fn()} isFetching={true} />);
    expect(screen.getByTestId("refresh-btn")).toBeDisabled();
  });

  it("enables the button when isFetching=false", () => {
    render(<RefreshBar dataUpdatedAt={Date.now()} onRefresh={vi.fn()} isFetching={false} />);
    expect(screen.getByTestId("refresh-btn")).not.toBeDisabled();
  });

  it("shows spinner text '…' when fetching", () => {
    render(<RefreshBar dataUpdatedAt={Date.now()} onRefresh={vi.fn()} isFetching={true} />);
    expect(screen.getByTestId("refresh-btn")).toHaveTextContent("…");
  });

  it("shows '↻ 刷新' when not fetching", () => {
    render(<RefreshBar dataUpdatedAt={Date.now()} onRefresh={vi.fn()} isFetching={false} />);
    expect(screen.getByTestId("refresh-btn")).toHaveTextContent("↻ 刷新");
  });
});
