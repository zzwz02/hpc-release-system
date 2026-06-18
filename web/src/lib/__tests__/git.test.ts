import { describe, expect, it } from "vitest";
import { formatGerritUrl } from "../git";

describe("formatGerritUrl", () => {
  it("shows only the path after PDE/HPC", () => {
    expect(formatGerritUrl("ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/lammps")).toBe("lammps");
    expect(formatGerritUrl("ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC/sw-metax-open/abacus")).toBe("sw-metax-open/abacus");
  });

  it("keeps non-standard URLs unchanged", () => {
    expect(formatGerritUrl("repo/local-app")).toBe("repo/local-app");
    expect(formatGerritUrl("")).toBe("");
  });
});
