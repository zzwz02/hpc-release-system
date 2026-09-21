import { describe, expect, it } from "vitest";
import { diffLines, diffLinesToText, diffValues, valueLines } from "../appInfoDiff";

describe("valueLines", () => {
  it("splits strings and pretty-prints structures", () => {
    expect(valueLines("a\nb")).toEqual(["a", "b"]);
    expect(valueLines("")).toEqual([]);
    expect(valueLines(undefined)).toEqual([]);
    expect(valueLines(["C500", "C601"])).toEqual(['[', '  "C500",', '  "C601"', ']']);
  });
});

describe("diffLines", () => {
  it("keeps common lines as context and marks the rest", () => {
    expect(diffLines(["a", "b", "c"], ["a", "x", "c"])).toEqual([
      { kind: "context", text: "a" },
      { kind: "del", text: "b" },
      { kind: "add", text: "x" },
      { kind: "context", text: "c" },
    ]);
  });

  it("reports pure additions and deletions", () => {
    expect(diffLines([], ["a"])).toEqual([{ kind: "add", text: "a" }]);
    expect(diffLines(["a"], [])).toEqual([{ kind: "del", text: "a" }]);
  });
});

describe("diffValues", () => {
  it("diffs a chip list line by line", () => {
    const lines = diffValues(["C500", "C600", "X301"], ["C500", "C601", "X301"]);
    expect(diffLinesToText(lines)).toBe(
      [
        "  [",
        '    "C500",',
        '-   "C600",',
        '+   "C601",',
        '    "X301"',
        "  ]",
      ].join("\n"),
    );
  });

  it("marks an empty side explicitly", () => {
    expect(diffValues("", "bash sanity.sh")).toEqual([
      { kind: "del", text: "（空）" },
      { kind: "add", text: "bash sanity.sh" },
    ]);
    expect(diffValues("old cmd", "")).toEqual([
      { kind: "del", text: "old cmd" },
      { kind: "add", text: "（空）" },
    ]);
  });

  it("returns nothing when both sides are empty", () => {
    expect(diffValues("", "")).toEqual([]);
  });
});
