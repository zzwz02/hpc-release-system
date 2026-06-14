import { describe, it, expect } from "vitest";
import { formatServerTime, formatClientFetchTime } from "../time";

describe("formatServerTime", () => {
  it("returns empty string for falsy inputs", () => {
    expect(formatServerTime(null)).toBe("");
    expect(formatServerTime(undefined)).toBe("");
    expect(formatServerTime("")).toBe("");
    expect(formatServerTime("  ")).toBe("");
  });

  it("passes through a naive Beijing timestamp unchanged", () => {
    expect(formatServerTime("2026-05-11 14:00:00")).toBe("2026-05-11 14:00:00");
  });

  it("replaces T separator with space", () => {
    expect(formatServerTime("2026-05-11T14:00:00")).toBe("2026-05-11 14:00:00");
  });

  it("strips fractional seconds", () => {
    expect(formatServerTime("2026-05-11T14:00:00.123456")).toBe("2026-05-11 14:00:00");
    expect(formatServerTime("2026-05-11T14:00:00.000")).toBe("2026-05-11 14:00:00");
  });

  it("strips Z suffix WITHOUT applying +8 offset", () => {
    // The value 14:00:00Z should appear as 14:00:00, NOT 22:00:00
    expect(formatServerTime("2026-05-11T14:00:00Z")).toBe("2026-05-11 14:00:00");
  });

  it("strips lowercase z suffix", () => {
    expect(formatServerTime("2026-05-11T14:00:00z")).toBe("2026-05-11 14:00:00");
  });

  it("strips +HH:MM offset WITHOUT applying offset math", () => {
    // We strip the marker, show the naive digits as-is
    expect(formatServerTime("2026-05-11T06:00:00+08:00")).toBe("2026-05-11 06:00:00");
  });

  it("strips -HH:MM offset", () => {
    expect(formatServerTime("2026-05-11T14:00:00-05:00")).toBe("2026-05-11 14:00:00");
  });

  it("strips offset without colon (e.g. +0800)", () => {
    expect(formatServerTime("2026-05-11T06:00:00+0800")).toBe("2026-05-11 06:00:00");
  });

  it("handles fractional seconds + Z together", () => {
    expect(formatServerTime("2026-05-11T14:00:00.999Z")).toBe("2026-05-11 14:00:00");
  });

  it("does not modify a plain date-only string", () => {
    expect(formatServerTime("2026-05-11")).toBe("2026-05-11");
  });
});

describe("formatClientFetchTime", () => {
  it("returns empty string for falsy epoch", () => {
    expect(formatClientFetchTime(0)).toBe("");
  });

  it("formats epoch ms as YYYY-MM-DD HH:MM:SS in local time", () => {
    // Create a known date using local time construction (avoids TZ issues)
    const d = new Date(2026, 4, 11, 14, 5, 9); // May 11 2026 14:05:09 local
    const result = formatClientFetchTime(d.getTime());
    expect(result).toBe("2026-05-11 14:05:09");
  });

  it("zero-pads month/day/hour/minute/second", () => {
    const d = new Date(2026, 0, 2, 3, 4, 5); // Jan 2 2026 03:04:05 local
    const result = formatClientFetchTime(d.getTime());
    expect(result).toBe("2026-01-02 03:04:05");
  });
});
