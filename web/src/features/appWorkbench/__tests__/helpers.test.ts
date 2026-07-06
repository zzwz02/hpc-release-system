/**
 * Unit tests for appWorkbench/helpers.ts
 *
 * Pure functions — no React, no DOM needed.
 */
import { describe, it, expect } from "vitest";
import type { Snapshot, User } from "../../../types";
import {
  releaseSnap,
  isReleaseSnap,
  missingItemKind,
  missingItemText,
  docsItems,
  docsOk,
  qaOk,
  needsAttention,
  qaDotClass,
  qaDotTitle,
  compareAppRows,
  userLabel,
  usersLabel,
  usersSearchText,
  ownerProgress,
  appInfoSource,
  orderChips,
  appDescriptionCount,
  filterAppRows,
  copiedScalarFields,
  mergeCopiedTestDocs,
} from "../helpers";
import type { SnapshotMissingItem, App, SnapshotTestDoc } from "../../../types";
import type { ReleaseDetail } from "../../../types";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeSnap(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    app_id: "app1",
    official_name: "MyApp",
    version: "1.0",
    description: "",
    official_url: "",
    type: "tool",
    arch: "x86",
    x86_chips: "",
    arm_chips: "",
    hpcc_chip: "",
    maca_version: "",
    build_arches: "",
    build_os: "",
    doc_target: "manual",
    release_decision: "release",
    owners: [],
    owner_confirmed: false,
    qa_status: "not_checked",
    qa_issue_note: "",
    doc: { intro: "", image_usage: "", binary_usage: "", env_setup: "", limitations: "" },
    community: { release_status: "", python_version: "", framework_version: "" },
    sanity: { arm_kylin: false, ubuntu: false },
    python_labels: "",
    pytorch_labels: "",
    test_docs: [],
    missing_items: [],
    app_info: null,
    app_info_diffs: [],
    ...overrides,
  };
}

function makeApp(id: string): App {
  return { id, git_url: `repo/${id}`, git_branch: "main", created_by: "admin", created_at: "2026-01-01", aliases: [] };
}

function makeRelease(snapshots: Record<string, Snapshot>): ReleaseDetail {
  return {
    id: "rel-1", name: "3.0", maca_version: "3.0",
    app_freeze_deadline: "2026-06-01", doc_deadline: "2026-07-01",
    released_locked: false, released_locked_at: "", released_locked_by: "",
    created_at: "2026-01-01", source: "manual", cloned_from: "",
    phase: "before_app_freeze",
    snapshots,
  };
}

// ---------------------------------------------------------------------------
// releaseSnap
// ---------------------------------------------------------------------------

describe("releaseSnap", () => {
  it("returns snapshot for app in release", () => {
    const snap = makeSnap();
    const release = makeRelease({ app1: snap });
    expect(releaseSnap(release, "app1")).toBe(snap);
  });

  it("returns null for missing app", () => {
    const release = makeRelease({});
    expect(releaseSnap(release, "missing")).toBeNull();
  });

  it("returns null when release is null", () => {
    expect(releaseSnap(null, "app1")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// isReleaseSnap
// ---------------------------------------------------------------------------

describe("isReleaseSnap", () => {
  it("true for release decision", () => {
    expect(isReleaseSnap(makeSnap({ release_decision: "release" }))).toBe(true);
  });

  it("false for cicd_only", () => {
    expect(isReleaseSnap(makeSnap({ release_decision: "cicd_only" }))).toBe(false);
  });

  it("false for null", () => {
    expect(isReleaseSnap(null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// missingItemKind / missingItemText
// ---------------------------------------------------------------------------

describe("missingItemKind", () => {
  it("returns kind from object", () => {
    const item: SnapshotMissingItem = { kind: "qa", text: "QA test" };
    expect(missingItemKind(item)).toBe("qa");
  });

  it("infers qa from string starting with 'QA ' (space)", () => {
    expect(missingItemKind("QA 测试通过检查")).toBe("qa");
  });

  it("infers doc for non-QA string", () => {
    expect(missingItemKind("intro 段")).toBe("doc");
  });

  it("defaults to doc when kind is empty in object", () => {
    expect(missingItemKind({ kind: "", text: "x" })).toBe("doc");
  });
});

describe("missingItemText", () => {
  it("returns text from object", () => {
    expect(missingItemText({ kind: "doc", text: "intro 段" })).toBe("intro 段");
  });

  it("returns string as-is", () => {
    expect(missingItemText("需要填写介绍")).toBe("需要填写介绍");
  });
});

// ---------------------------------------------------------------------------
// docsItems / docsOk
// ---------------------------------------------------------------------------

describe("docsItems", () => {
  it("filters out qa items", () => {
    const snap = makeSnap({
      release_decision: "release",
      missing_items: [
        { kind: "doc", text: "intro" },
        { kind: "qa", text: "QA check" },
      ],
    });
    const items = docsItems(snap);
    expect(items).toHaveLength(1);
    expect(items[0].kind).toBe("doc");
  });

  it("returns empty array for no missing items", () => {
    expect(docsItems(makeSnap())).toHaveLength(0);
  });
});

describe("docsOk", () => {
  it("true when release snap with no doc missing items", () => {
    expect(docsOk(makeSnap({ release_decision: "release", missing_items: [] }))).toBe(true);
  });

  it("false when not release snap", () => {
    expect(docsOk(makeSnap({ release_decision: "cicd_only" }))).toBe(false);
  });

  it("false when has doc items", () => {
    const snap = makeSnap({ release_decision: "release", missing_items: [{ kind: "doc", text: "intro" }] });
    expect(docsOk(snap)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// qaOk
// ---------------------------------------------------------------------------

describe("qaOk", () => {
  it("true for qa_passed", () => {
    expect(qaOk(makeSnap({ qa_status: "qa_passed" }))).toBe(true);
  });

  it("true for has_issues", () => {
    expect(qaOk(makeSnap({ qa_status: "has_issues" }))).toBe(true);
  });

  it("false for not_checked", () => {
    expect(qaOk(makeSnap({ qa_status: "not_checked" }))).toBe(false);
  });

  it("false for cannot_release", () => {
    expect(qaOk(makeSnap({ qa_status: "cannot_release" }))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// needsAttention
// ---------------------------------------------------------------------------

describe("needsAttention", () => {
  it("true when missing_items non-empty (待办不齐全)", () => {
    const snap = makeSnap({ missing_items: [{ kind: "doc", text: "intro 段" }] });
    expect(needsAttention(snap)).toBe(true);
  });

  it("true for qa_status has_issues", () => {
    expect(needsAttention(makeSnap({ qa_status: "has_issues" }))).toBe(true);
  });

  it("true for qa_status cannot_release", () => {
    expect(needsAttention(makeSnap({ qa_status: "cannot_release" }))).toBe(true);
  });

  it("false when todos complete and QA passed", () => {
    expect(needsAttention(makeSnap({ qa_status: "qa_passed" }))).toBe(false);
  });

  it("false for not_checked with no missing items (待测试 ≠ 有问题)", () => {
    expect(needsAttention(makeSnap({ qa_status: "not_checked" }))).toBe(false);
  });

  it("false for non-release decision even with issues", () => {
    const snap = makeSnap({
      release_decision: "cicd_only",
      qa_status: "cannot_release",
      missing_items: [{ kind: "doc", text: "intro 段" }],
    });
    expect(needsAttention(snap)).toBe(false);
  });

  it("false for null snapshot", () => {
    expect(needsAttention(null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// qaDotClass / qaDotTitle
// ---------------------------------------------------------------------------

describe("qaDotClass", () => {
  it("na when not release snap", () => {
    expect(qaDotClass(makeSnap({ release_decision: "cicd_only" }))).toBe("na");
  });

  it("ok for qa_passed", () => {
    expect(qaDotClass(makeSnap({ release_decision: "release", qa_status: "qa_passed" }))).toBe("ok");
  });

  it("warn for has_issues", () => {
    expect(qaDotClass(makeSnap({ release_decision: "release", qa_status: "has_issues" }))).toBe("warn");
  });

  it("bad for cannot_release", () => {
    expect(qaDotClass(makeSnap({ release_decision: "release", qa_status: "cannot_release" }))).toBe("bad");
  });

  it("todo for not_checked release snap", () => {
    expect(qaDotClass(makeSnap({ release_decision: "release", qa_status: "not_checked" }))).toBe("todo");
  });
});

describe("qaDotTitle", () => {
  it("mentions decision for non-release snap", () => {
    const title = qaDotTitle(makeSnap({ release_decision: "cicd_only" }));
    expect(title).toContain("cicd_only");
  });

  it("shows QA status label for release snap", () => {
    const title = qaDotTitle(makeSnap({ release_decision: "release", qa_status: "qa_passed" }));
    expect(title).toContain("通过");
  });
});

// ---------------------------------------------------------------------------
// compareAppRows
// ---------------------------------------------------------------------------

describe("compareAppRows", () => {
  it("owner rows come before non-owner rows for Owner role", () => {
    const user: User = { username: "alice", role: "Owner", display_name: "Alice" };
    const rowA = { app: makeApp("a1"), snap: makeSnap({ app_id: "a1", owners: ["alice"] }) };
    const rowB = { app: makeApp("a2"), snap: makeSnap({ app_id: "a2", owners: ["bob"] }) };
    expect(compareAppRows(rowA, rowB, user)).toBeLessThan(0);
    expect(compareAppRows(rowB, rowA, user)).toBeGreaterThan(0);
  });

  it("sorts by release_decision order: release < cicd_only < stopped", () => {
    const user = null;
    const rowR = { app: makeApp("r"), snap: makeSnap({ release_decision: "release" }) };
    const rowC = { app: makeApp("c"), snap: makeSnap({ release_decision: "cicd_only" }) };
    const rowS = { app: makeApp("s"), snap: makeSnap({ release_decision: "stopped" }) };
    expect(compareAppRows(rowR, rowC, user)).toBeLessThan(0);
    expect(compareAppRows(rowC, rowS, user)).toBeLessThan(0);
  });

  it("falls back to locale sort by name within same decision", () => {
    const user = null;
    const rowA = { app: makeApp("a"), snap: makeSnap({ official_name: "Alpha" }) };
    const rowB = { app: makeApp("b"), snap: makeSnap({ official_name: "Beta" }) };
    expect(compareAppRows(rowA, rowB, user)).toBeLessThan(0);
  });
});

// ---------------------------------------------------------------------------
// userLabel / usersLabel / usersSearchText
// ---------------------------------------------------------------------------

describe("userLabel", () => {
  it("shows display name and username when different", () => {
    expect(userLabel("alice", { alice: "Alice Smith" })).toBe("Alice Smith (alice)");
  });

  it("shows just username when no display name", () => {
    expect(userLabel("alice", {})).toBe("alice");
  });

  it("shows just username when display name equals username", () => {
    expect(userLabel("alice", { alice: "alice" })).toBe("alice");
  });
});

describe("usersLabel", () => {
  it("returns 无 owner for empty owners", () => {
    expect(usersLabel([], {})).toBe("无 owner");
  });

  it("prefers display name", () => {
    expect(usersLabel(["alice"], { alice: "Alice" })).toBe("Alice");
  });

  it("falls back to username", () => {
    expect(usersLabel(["bob"], {})).toBe("bob");
  });

  it("joins multiple owners", () => {
    const result = usersLabel(["alice", "bob"], { alice: "Alice" });
    expect(result).toContain("Alice");
    expect(result).toContain("bob");
  });
});

describe("usersSearchText", () => {
  it("combines username and display name", () => {
    const text = usersSearchText(["alice"], { alice: "Alice Smith" });
    expect(text).toContain("alice");
    expect(text).toContain("Alice Smith");
  });

  it("handles null owners", () => {
    expect(usersSearchText(null, {})).toBe("");
  });
});

// ---------------------------------------------------------------------------
// ownerProgress
// ---------------------------------------------------------------------------

describe("ownerProgress", () => {
  it("returns pct=0 for empty snap", () => {
    const prog = ownerProgress(makeSnap());
    expect(prog.pct).toBe(0);
    expect(prog.total).toBe(7);
  });

  it("increments for non-empty intro", () => {
    const snap = makeSnap({ doc: { intro: "hello", image_usage: "", binary_usage: "", env_setup: "", limitations: "" } });
    const prog = ownerProgress(snap);
    expect(prog.done).toBeGreaterThan(0);
  });

  it("returns pct=100 when all fields filled", () => {
    const snap = makeSnap({
      app_info: { source_type: "owner_upload" },
      doc: {
        intro: "intro text",
        image_usage: "usage",
        binary_usage: "binary",
        env_setup: "setup",
        limitations: "limits",
      },
      test_docs: [{
        id: "t1", path: "test.sh", command: "cmd",
        dataset: "data", content: "content",
        result_view: "view", pass_criteria: "pass",
      }],
    });
    const prog = ownerProgress(snap);
    expect(prog.pct).toBe(100);
  });
});

// ---------------------------------------------------------------------------
// appInfoSource
// ---------------------------------------------------------------------------

describe("appInfoSource", () => {
  it("未提供 for no app_info", () => {
    expect(appInfoSource(makeSnap({ app_info: null }))).toBe("未提供");
  });

  it("formats gerrit_fetch source", () => {
    const snap = makeSnap({ app_info: { source_type: "gerrit_fetch", commit_id: "abc123", source: "repo.git" } });
    const text = appInfoSource(snap);
    expect(text).toContain("Gerrit 拉取");
    expect(text).toContain("abc123");
  });

  it("formats owner_upload source", () => {
    const snap = makeSnap({ app_info: { source_type: "owner_upload", uploaded_by: "alice", source: "file.json" } });
    const text = appInfoSource(snap);
    expect(text).toContain("Owner 上传");
    expect(text).toContain("alice");
  });
});

// ---------------------------------------------------------------------------
// orderChips
// ---------------------------------------------------------------------------

describe("orderChips", () => {
  it("deduplicates and sorts", () => {
    expect(orderChips("A100,V100,A100")).toBe("A100,V100");
  });

  it("handles multiple separators", () => {
    const result = orderChips("B,A;C");
    expect(result).toBe("A,B,C");
  });

  it("pushes x201 to end", () => {
    const result = orderChips("x201,A100");
    expect(result).toBe("A100,x201");
  });

  it("returns empty for empty input", () => {
    expect(orderChips("")).toBe("");
  });
});

// ---------------------------------------------------------------------------
// appDescriptionCount
// ---------------------------------------------------------------------------

describe("appDescriptionCount", () => {
  it("counts characters after trim", () => {
    expect(appDescriptionCount("  hello  ")).toBe(5);
  });

  it("returns 0 for null", () => {
    expect(appDescriptionCount(null)).toBe(0);
  });

  it("returns 0 for empty", () => {
    expect(appDescriptionCount("")).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// filterAppRows
// ---------------------------------------------------------------------------

describe("filterAppRows", () => {
  const rows = [
    { app: makeApp("a1"), snap: makeSnap({ app_id: "a1", official_name: "AlphaApp", owners: ["alice"], type: "library" }) },
    { app: makeApp("a2"), snap: makeSnap({ app_id: "a2", official_name: "BetaTool", owners: ["bob"], type: "tool" }) },
  ];
  const user: User = { username: "alice", role: "Owner", display_name: "Alice" };

  it("returns all rows for empty query and ownOnly=false", () => {
    expect(filterAppRows(rows, "", false, user, {})).toHaveLength(2);
  });

  it("filters by name search", () => {
    const result = filterAppRows(rows, "alpha", false, user, {});
    expect(result).toHaveLength(1);
    expect(result[0].snap.official_name).toBe("AlphaApp");
  });

  it("filters by type search", () => {
    const result = filterAppRows(rows, "tool", false, user, {});
    expect(result).toHaveLength(1);
    expect(result[0].snap.official_name).toBe("BetaTool");
  });

  it("ownOnly filters to current user's apps", () => {
    const result = filterAppRows(rows, "", true, user, {});
    expect(result).toHaveLength(1);
    expect(result[0].snap.owners).toContain("alice");
  });

  it("search includes owner display name", () => {
    const result = filterAppRows(rows, "carol", false, user, { alice: "Carol Chen" });
    expect(result).toHaveLength(1);
    expect(result[0].snap.official_name).toBe("AlphaApp");
  });
});

// ---------------------------------------------------------------------------
// F2 — copiedScalarFields + mergeCopiedTestDocs
// ---------------------------------------------------------------------------

describe("copiedScalarFields", () => {
  it("copies editable doc/community/sanity content fields", () => {
    const src = makeSnap({
      type: "ai",
      official_url: "https://x",
      description: "desc",
      doc_target: "ai4sci",
      doc: { intro: "i", image_usage: "im", binary_usage: "bn", env_setup: "es", limitations: "lm" },
      community: { release_status: "rs", python_version: "py", framework_version: "fw" },
      sanity: { arm_kylin: true, ubuntu: true },
    });
    const out = copiedScalarFields(src);
    expect(out).toEqual({
      type: "ai",
      official_url: "https://x",
      description: "desc",
      doc_target: "ai4sci",
      intro: "i",
      image_usage: "im",
      binary_usage: "bn",
      env_setup: "es",
      limitations: "lm",
      community_release: "rs",
      community_python: "py",
      community_framework: "fw",
      sanity_arm: true,
      sanity_ubuntu: true,
    });
  });
});

describe("mergeCopiedTestDocs", () => {
  const doc = (over: Partial<SnapshotTestDoc>): SnapshotTestDoc => ({
    id: "x", path: "p", command: "", dataset: "", content: "",
    result_view: "", pass_criteria: "", ...over,
  });

  it("copies text fields onto matching paths but keeps auto command", () => {
    const current = [doc({ id: "c1", path: "t/a.sh", command: "AUTO" })];
    const source = [doc({ id: "s1", path: "t/a.sh", command: "SRC", content: "hi", dataset: "ds" })];
    const out = mergeCopiedTestDocs(current, source);
    expect(out).toHaveLength(1);
    expect(out[0].id).toBe("c1");        // keeps current id
    expect(out[0].command).toBe("AUTO"); // auto row keeps its command
    expect(out[0].content).toBe("hi");
    expect(out[0].dataset).toBe("ds");
  });

  it("copies command for owner-added matching rows", () => {
    const current = [doc({ id: "c1", path: "owner.1", owner_added: true, command: "OLD" })];
    const source = [doc({ id: "s1", path: "owner.1", owner_added: true, command: "NEW" })];
    const out = mergeCopiedTestDocs(current, source);
    expect(out[0].command).toBe("NEW");
  });

  it("appends source owner-added rows whose path is absent", () => {
    const current = [doc({ id: "c1", path: "t/a.sh" })];
    const source = [doc({ id: "s1", path: "owner.extra", owner_added: true, content: "extra" })];
    const out = mergeCopiedTestDocs(current, source);
    expect(out).toHaveLength(2);
    expect(out[1].path).toBe("owner.extra");
    expect(out[1].content).toBe("extra");
  });

  it("leaves current rows with no source match untouched", () => {
    const current = [doc({ id: "c1", path: "t/a.sh", content: "keep" })];
    const out = mergeCopiedTestDocs(current, []);
    expect(out[0].content).toBe("keep");
  });
});
