import { describe, expect, it } from "vitest";
import { makeApp, makeEntry, makeRelease } from "../../../test/fixtures";
import {
  activeEntry,
  appDescriptionCount,
  appEntry,
  compareAppRows,
  filterAppRows,
  matchesAppStatusFilter,
  orderChips,
  qaDotClass,
  versionTag,
} from "../helpers";

describe("entry lookup", () => {
  it("finds active and withdrawn entries", () => {
    const release = makeRelease([makeEntry("a"), makeEntry("b", { id: 2, status: "withdrawn" })]);
    expect(appEntry(release, "a")?.id).toBe(1);
    expect(activeEntry(release, "a")?.id).toBe(1);
    expect(appEntry(release, "b")?.status).toBe("withdrawn");
    expect(activeEntry(release, "b")).toBeNull();
    expect(appEntry(release, "c")).toBeNull();
  });
});

describe("status filter", () => {
  const running = makeApp("a");
  const stopped = makeApp("b", { cicd_status: "stopped" });
  const signed = makeEntry("a");
  const issue = makeEntry("a", { qa_status: "has_issues" });

  it("separates signed-up, not signed-up and stopped Apps", () => {
    expect(matchesAppStatusFilter(running, signed, "signed_up")).toBe(true);
    expect(matchesAppStatusFilter(running, null, "signed_up")).toBe(false);
    expect(matchesAppStatusFilter(running, null, "not_signed_up")).toBe(true);
    expect(matchesAppStatusFilter(stopped, null, "not_signed_up")).toBe(false);
    expect(matchesAppStatusFilter(stopped, null, "stopped")).toBe(true);
    expect(matchesAppStatusFilter(running, issue, "qa_anomaly")).toBe(true);
    expect(matchesAppStatusFilter(running, signed, "qa_anomaly")).toBe(false);
  });

  it("colours the QA dot only for active signups", () => {
    expect(qaDotClass(null)).toBe("na");
    expect(qaDotClass(makeEntry("a", { status: "withdrawn" }))).toBe("na");
    expect(qaDotClass(makeEntry("a", { qa_status: "qa_passed" }))).toBe("ok");
    expect(qaDotClass(makeEntry("a", { qa_status: "cannot_release" }))).toBe("bad");
  });
});

describe("sorting and search", () => {
  it("puts the Owner's Apps first, then signed-up, running, stopped", () => {
    const rows = [
      { app: makeApp("z", { name: "Zeta", cicd_status: "stopped" }), entry: null },
      { app: makeApp("b", { name: "Beta", owners: ["bob"] }), entry: makeEntry("b") },
      { app: makeApp("a", { name: "Alpha", owners: ["bob"] }), entry: null },
      { app: makeApp("c", { name: "Gamma", owners: ["alice"] }), entry: null },
    ];
    const rm = { username: "rm", role: "RM", display_name: "" };
    expect([...rows].sort((x, y) => compareAppRows(x, y, rm)).map((r) => r.app.id)).toEqual(["b", "a", "c", "z"]);
    const owner = { username: "alice", role: "Owner", display_name: "" };
    expect([...rows].sort((x, y) => compareAppRows(x, y, owner))[0].app.id).toBe("c");
  });

  it("filters by text and ownership", () => {
    const rows = [{ app: makeApp("a", { name: "Amber 22" }) }, { app: makeApp("b", { name: "Lammps", owners: ["bob"] }) }];
    expect(filterAppRows(rows, "amb", false, null, {}).map((r) => r.app.id)).toEqual(["a"]);
    expect(filterAppRows(rows, "", true, { username: "bob", role: "Owner", display_name: "" }, {}).map((r) => r.app.id)).toEqual(["b"]);
    expect(filterAppRows(rows, "bobby", false, null, { bob: "Bobby" }).map((r) => r.app.id)).toEqual(["b"]);
  });
});

describe("formatting", () => {
  it("orders chips with X201 last", () => {
    expect(orderChips(["X201", "C600", "C500"])).toBe("C500,C600,X201");
    expect(orderChips("c500, c500")).toBe("c500");
  });

  it("counts description units like the backend", () => {
    expect(appDescriptionCount("HPC 分子 dynamics")).toBe(4);
  });

  it("does not double the v prefix", () => {
    expect(versionTag("1.0")).toBe("v1.0");
    expect(versionTag("v3.0.1")).toBe("v3.0.1");
  });
});
