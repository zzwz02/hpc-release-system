import { describe, expect, it } from "vitest";
import gerritPathContract from "../../../../tests/contracts/gerrit_paths.json";
import {
  GERRIT_HPC_BASE,
  GERRIT_HPC_PROJECT,
  GERRIT_MANIFEST_BRANCH,
  GERRIT_MANIFEST_PROJECT,
  GERRIT_MANIFEST_REPO_URL,
  formatCicdRepoPath,
  formatGerritUrl,
} from "../git";

function expandContractValue(value: string): string {
  const replacements: Record<string, string> = {
    "{gerrit_hpc_base}": GERRIT_HPC_BASE,
    "{gerrit_hpc_project}": GERRIT_HPC_PROJECT,
    "{gerrit_manifest_project}": GERRIT_MANIFEST_PROJECT,
    "{gerrit_manifest_repo_url}": GERRIT_MANIFEST_REPO_URL,
  };
  return Object.entries(replacements).reduce(
    (result, [placeholder, replacement]) => result.split(placeholder).join(replacement),
    value,
  );
}

describe("shared Gerrit configuration", () => {
  it("exports the configured manifest branch", () => {
    expect(GERRIT_MANIFEST_BRANCH).toBe("master");
  });
});

describe("formatGerritUrl", () => {
  it("shows only the path after PDE/HPC", () => {
    expect(formatGerritUrl(`${GERRIT_HPC_BASE}/lammps`)).toBe("lammps");
    expect(formatGerritUrl(`${GERRIT_HPC_BASE}/sw-metax-open/abacus`)).toBe("sw-metax-open/abacus");
  });

  it("keeps non-standard URLs unchanged", () => {
    expect(formatGerritUrl("repo/local-app")).toBe("repo/local-app");
    expect(formatGerritUrl("")).toBe("");
  });
});

describe("formatCicdRepoPath", () => {
  it.each(gerritPathContract.cases)("matches the shared display contract: $name", (testCase) => {
    expect(
      formatCicdRepoPath(
        expandContractValue(testCase.input),
        testCase.repo_type,
      ),
    ).toBe(expandContractValue(testCase.display_path));
  });
});
