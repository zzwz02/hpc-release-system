import { describe, it, expect } from "vitest";
import {
  releaseLocked,
  releasePhase,
  beforeAppFreeze,
  beforeDocDeadline,
  newAppDecisionOptions,
} from "../phase";
import type { ReleaseSummary } from "../../types";

const makeRelease = (
  phase: string,
  released_locked = false,
): ReleaseSummary =>
  ({
    id: "rel1",
    name: "Test",
    maca_version: "20260511",
    app_freeze_deadline: "2026-12-31 23:59",
    doc_deadline: "2026-12-31 23:59",
    released_locked,
    released_locked_at: "",
    released_locked_by: "",
    created_at: "",
    source: "initial_csv",
    cloned_from: "",
    phase: phase as ReleaseSummary["phase"],
  });

describe("releaseLocked", () => {
  it("returns false when released_locked is false", () => {
    expect(releaseLocked(makeRelease("before_app_freeze", false))).toBe(false);
  });

  it("returns true when released_locked is true", () => {
    expect(releaseLocked(makeRelease("released", true))).toBe(true);
  });

  it("returns false for null release", () => {
    expect(releaseLocked(null)).toBe(false);
    expect(releaseLocked(undefined)).toBe(false);
  });
});

describe("releasePhase", () => {
  it("returns the phase string", () => {
    expect(releasePhase(makeRelease("before_app_freeze"))).toBe("before_app_freeze");
    expect(releasePhase(makeRelease("after_app_freeze"))).toBe("after_app_freeze");
  });

  it("returns empty string for null release", () => {
    expect(releasePhase(null)).toBe("");
    expect(releasePhase(undefined)).toBe("");
  });
});

describe("beforeAppFreeze", () => {
  it("true only when phase is before_app_freeze", () => {
    expect(beforeAppFreeze(makeRelease("before_app_freeze"))).toBe(true);
    expect(beforeAppFreeze(makeRelease("after_app_freeze"))).toBe(false);
    expect(beforeAppFreeze(makeRelease("released"))).toBe(false);
  });

  it("false for null release", () => {
    expect(beforeAppFreeze(null)).toBe(false);
  });
});

describe("beforeDocDeadline", () => {
  it("true for before_app_freeze and after_app_freeze phases", () => {
    expect(beforeDocDeadline(makeRelease("before_app_freeze"))).toBe(true);
    expect(beforeDocDeadline(makeRelease("after_app_freeze"))).toBe(true);
  });

  it("false for released / locked phase", () => {
    expect(beforeDocDeadline(makeRelease("released"))).toBe(false);
  });

  it("false for null release", () => {
    expect(beforeDocDeadline(null)).toBe(false);
  });
});

describe("newAppDecisionOptions", () => {
  it("returns all three options before app freeze", () => {
    const opts = newAppDecisionOptions(makeRelease("before_app_freeze"));
    expect(opts).toEqual(["release", "cicd_only", "stopped"]);
  });

  it("returns only cicd_only and stopped after app freeze", () => {
    const opts = newAppDecisionOptions(makeRelease("after_app_freeze"));
    expect(opts).toEqual(["cicd_only", "stopped"]);
    expect(opts).not.toContain("release");
  });

  it("returns only cicd_only and stopped for released phase", () => {
    const opts = newAppDecisionOptions(makeRelease("released"));
    expect(opts).toEqual(["cicd_only", "stopped"]);
  });

  it("returns only cicd_only and stopped for null release", () => {
    const opts = newAppDecisionOptions(null);
    expect(opts).toEqual(["cicd_only", "stopped"]);
  });
});
