import { describe, it, expect } from "vitest";
import {
  isRM,
  isOwner,
  isQA,
  isGuest,
  isSPD,
  isAdmin,
  canViewQa,
  canEditQa,
  canGenerateMarkdown,
  canEditWiki,
  canEdit,
  isOwnApp,
} from "../roles";
import type { User, Snapshot } from "../../types";

const makeUser = (role: string, username = "u1"): User => ({
  username,
  role,
  display_name: "",
});

const makeSnap = (owners: string[] = []): Snapshot =>
  ({
    app_id: "app1",
    owners,
  }) as unknown as Snapshot;

describe("role predicates", () => {
  it("isRM", () => {
    expect(isRM(makeUser("RM"))).toBe(true);
    expect(isRM(makeUser("Owner"))).toBe(false);
    expect(isRM(null)).toBe(false);
  });

  it("isOwner", () => {
    expect(isOwner(makeUser("Owner"))).toBe(true);
    expect(isOwner(makeUser("RM"))).toBe(false);
  });

  it("isQA", () => {
    expect(isQA(makeUser("QA"))).toBe(true);
    expect(isQA(makeUser("RM"))).toBe(false);
  });

  it("isGuest", () => {
    expect(isGuest(makeUser("Guest"))).toBe(true);
    expect(isGuest(makeUser("QA"))).toBe(false);
  });

  it("isSPD", () => {
    expect(isSPD(makeUser("SPD"))).toBe(true);
    expect(isSPD(makeUser("RM"))).toBe(false);
  });

  it("isAdmin", () => {
    expect(isAdmin(makeUser("Admin"))).toBe(true);
    expect(isAdmin(makeUser("RM"))).toBe(false);
  });
});

describe("canViewQa", () => {
  it("allows QA, RM, Owner, Guest", () => {
    for (const role of ["QA", "RM", "Owner", "Guest"]) {
      expect(canViewQa(makeUser(role))).toBe(true);
    }
  });

  it("denies SPD and Admin", () => {
    expect(canViewQa(makeUser("SPD"))).toBe(false);
    expect(canViewQa(makeUser("Admin"))).toBe(false);
  });

  it("denies null user", () => {
    expect(canViewQa(null)).toBe(false);
  });
});

describe("canEditQa", () => {
  it("allows QA and RM", () => {
    expect(canEditQa(makeUser("QA"))).toBe(true);
    expect(canEditQa(makeUser("RM"))).toBe(true);
  });

  it("denies Owner and Guest", () => {
    expect(canEditQa(makeUser("Owner"))).toBe(false);
    expect(canEditQa(makeUser("Guest"))).toBe(false);
  });
});

describe("canGenerateMarkdown", () => {
  it("allows RM and Owner", () => {
    expect(canGenerateMarkdown(makeUser("RM"))).toBe(true);
    expect(canGenerateMarkdown(makeUser("Owner"))).toBe(true);
  });

  it("denies QA, Guest, Admin", () => {
    for (const role of ["QA", "Guest", "Admin"]) {
      expect(canGenerateMarkdown(makeUser(role))).toBe(false);
    }
  });
});

describe("canEditWiki", () => {
  it("allows only RM", () => {
    expect(canEditWiki(makeUser("RM"))).toBe(true);
    expect(canEditWiki(makeUser("Owner"))).toBe(false);
    expect(canEditWiki(makeUser("Admin"))).toBe(false);
  });
});

describe("canEdit (snapshot permission)", () => {
  it("RM can edit any snapshot", () => {
    const snap = makeSnap(["other_user"]);
    expect(canEdit(makeUser("RM", "rm"), snap)).toBe(true);
  });

  it("Owner can edit their own app", () => {
    const snap = makeSnap(["owner1", "owner2"]);
    expect(canEdit(makeUser("Owner", "owner1"), snap)).toBe(true);
  });

  it("Owner cannot edit someone else's app", () => {
    const snap = makeSnap(["owner2"]);
    expect(canEdit(makeUser("Owner", "owner1"), snap)).toBe(false);
  });

  it("QA cannot edit", () => {
    const snap = makeSnap(["qa_user"]);
    expect(canEdit(makeUser("QA", "qa_user"), snap)).toBe(false);
  });

  it("null user cannot edit", () => {
    expect(canEdit(null, makeSnap(["u"]))).toBe(false);
  });

  it("null snap: RM can still edit (no owner list to check)", () => {
    expect(canEdit(makeUser("RM"), null)).toBe(true);
  });

  it("null snap: Owner cannot edit (no owner list)", () => {
    expect(canEdit(makeUser("Owner", "u1"), null)).toBe(false);
  });
});

describe("isOwnApp", () => {
  it("true when user is in owners list", () => {
    const snap = makeSnap(["u1", "u2"]);
    expect(isOwnApp(makeUser("Owner", "u1"), snap)).toBe(true);
  });

  it("false when user is not in owners list", () => {
    const snap = makeSnap(["u2"]);
    expect(isOwnApp(makeUser("Owner", "u1"), snap)).toBe(false);
  });

  it("false for null user", () => {
    expect(isOwnApp(null, makeSnap(["u1"]))).toBe(false);
  });

  it("false for empty owners list", () => {
    expect(isOwnApp(makeUser("Owner", "u1"), makeSnap([]))).toBe(false);
  });
});
