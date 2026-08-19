import { describe, expect, it } from "vitest";

import {
  crossesReleaseDecisionRuntimeBoundary,
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
