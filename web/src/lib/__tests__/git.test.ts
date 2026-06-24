import { describe, expect, it } from "vitest";
import {
  GERRIT_HPC_BASE,
  GERRIT_MANIFEST_REPO_URL,
  formatCicdRepoPath,
  formatGerritUrl,
  normalizeCicdRepoInput,
  normalizeGitUrl,
} from "../git";

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

describe("formatCicdRepoPath", () => {
  it("shows the PDE/HPC suffix for Gerrit git repos", () => {
    expect(formatCicdRepoPath(`${GERRIT_HPC_BASE}/hpc_amber`, "git")).toBe("hpc_amber");
  });

  it("shows only the XML path for manifest repos", () => {
    expect(formatCicdRepoPath(`${GERRIT_MANIFEST_REPO_URL}/APP/openfoam/hpc_v2206_v0.xml`, "repo"))
      .toBe("APP/openfoam/hpc_v2206_v0.xml");
    expect(formatCicdRepoPath("manifest/APP/openfoam/hpc_v2206_v0.xml", "repo"))
      .toBe("APP/openfoam/hpc_v2206_v0.xml");
  });
});

describe("normalizeCicdRepoInput", () => {
  it("strips the fixed Gerrit prefix from git input", () => {
    expect(normalizeCicdRepoInput("git", `${GERRIT_HPC_BASE}/hpc_amber`)).toBe("hpc_amber");
  });

  it("strips the manifest repo prefix from repo input", () => {
    expect(normalizeCicdRepoInput("repo", `${GERRIT_MANIFEST_REPO_URL}/APP/openfoam/hpc_v2206_v0.xml`))
      .toBe("APP/openfoam/hpc_v2206_v0.xml");
  });
});

describe("normalizeGitUrl", () => {
  it("expands short HPC paths to full Gerrit URLs", () => {
    expect(normalizeGitUrl("hpc_amber")).toBe(`${GERRIT_HPC_BASE}/hpc_amber`);
  });
});
