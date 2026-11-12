import { describe, expect, it } from "vitest";
import { releaseLocked, releaseOpen, releasePhase, releasePhaseLabel, signupOpen } from "../phase";
import type { ReleaseSummary } from "../../types";

const release = (phase: string, locked = false) =>
  ({ id: "r", name: "2026.12", phase, released_locked: locked }) as unknown as ReleaseSummary;

describe("monthly phase helpers", () => {
  it("labels the four phases and keeps unknown values", () => {
    expect(releasePhaseLabel("signup")).toBe("报名中");
    expect(releasePhaseLabel("in_progress")).toBe("进行中");
    expect(releasePhaseLabel("qa_closed")).toBe("QA 已截止");
    expect(releasePhaseLabel("released")).toBe("已发布");
    expect(releasePhaseLabel("other")).toBe("other");
  });

  it("signup is open only in the signup phase", () => {
    expect(signupOpen(release("signup"))).toBe(true);
    expect(signupOpen(release("in_progress"))).toBe(false);
    expect(signupOpen(null)).toBe(false);
  });

  it("reads lock and phase", () => {
    expect(releaseLocked(release("released", true))).toBe(true);
    expect(releaseOpen(release("qa_closed"))).toBe(true);
    expect(releaseOpen(release("released", true))).toBe(false);
    expect(releasePhase(undefined)).toBe("");
  });
});
