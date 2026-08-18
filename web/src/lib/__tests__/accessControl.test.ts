import { describe, expect, it } from "vitest";
import {
  ALL_ROLES,
  can,
  canAccessTab,
  rolesForCapability,
  rolesForTab,
} from "../accessControl";

describe("shared access control", () => {
  it("loads the canonical role catalog", () => {
    expect(ALL_ROLES).toEqual(["RM", "Owner", "QA", "Guest", "Admin", "SPD"]);
  });

  it("uses the requested diagnostic tab matrices", () => {
    expect(rolesForTab("jenkins-failures")).toEqual(["RM", "Owner", "SPD", "QA"]);
    expect(rolesForTab("cicd-assistant")).toEqual(["RM", "Owner", "SPD"]);
    expect(canAccessTab("Admin", "admin")).toBe(true);
    expect(canAccessTab("Admin", "dashboard")).toBe(false);
  });

  it("exposes operation-level capabilities separately from tabs", () => {
    expect(rolesForCapability("qa.edit")).toEqual(["RM", "QA"]);
    expect(can("Owner", "artifact.generate")).toBe(true);
    expect(can("Owner", "artifact.export.test_scope")).toBe(false);
    expect(can("SPD", "cicd.delivery.return")).toBe(true);
  });
});
