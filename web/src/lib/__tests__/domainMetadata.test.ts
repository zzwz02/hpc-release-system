import { describe, expect, it } from "vitest";

import {
  DOC_TARGET_DEFAULT,
  DOC_TARGETS,
  QA_STATUS_DEFAULT,
  crossesReleaseDecisionRuntimeBoundary,
  normalizeDocTarget,
  qaStatusRequiresIssueNote,
  RELEASE_DECISIONS,
  releaseDecisionCicdStatus,
} from "../domainMetadata";

describe("release decision metadata", () => {
  it("provides the canonical order and CICD status mapping", () => {
    expect(RELEASE_DECISIONS).toEqual(["release", "cicd_only", "stopped"]);
    expect(releaseDecisionCicdStatus("release")).toBe("Running");
    expect(releaseDecisionCicdStatus("cicd_only")).toBe("Running");
    expect(releaseDecisionCicdStatus("stopped")).toBe("Stopped");
  });

  it("detects only Running/Stopped boundary changes", () => {
    expect(crossesReleaseDecisionRuntimeBoundary("release", "cicd_only")).toBe(false);
    expect(crossesReleaseDecisionRuntimeBoundary("release", "stopped")).toBe(true);
    expect(crossesReleaseDecisionRuntimeBoundary("stopped", "cicd_only")).toBe(true);
  });
});

describe("QA and documentation metadata", () => {
  it("normalizes aliases and unknown doc targets to the shared default", () => {
    expect(DOC_TARGETS).toEqual(["manual", "ai4sci"]);
    expect(DOC_TARGET_DEFAULT).toBe("manual");
    expect(normalizeDocTarget("HPC")).toBe("manual");
    expect(normalizeDocTarget("AI4SCI")).toBe("ai4sci");
    expect(normalizeDocTarget("unexpected-target")).toBe("manual");
  });

  it("provides shared QA defaults and issue-note rules", () => {
    expect(QA_STATUS_DEFAULT).toBe("not_checked");
    expect(qaStatusRequiresIssueNote("has_issues")).toBe(true);
    expect(qaStatusRequiresIssueNote("cannot_release")).toBe(true);
    expect(qaStatusRequiresIssueNote("qa_passed")).toBe(false);
  });
});
