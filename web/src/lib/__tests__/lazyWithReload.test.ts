import { beforeEach, describe, expect, it, vi } from "vitest";
import { recoverFromChunkError } from "../lazyWithReload";

/** Resolves to "pending" when the promise has not settled by the next tick. */
async function settled(promise: Promise<unknown>): Promise<"pending" | "settled"> {
  return Promise.race([
    promise.then(() => "settled" as const, () => "settled" as const),
    new Promise<"pending">((resolve) => setTimeout(() => resolve("pending"), 10)),
  ]);
}

describe("recoverFromChunkError", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("reloads once and keeps the promise pending while the page swaps", async () => {
    const reload = vi.fn();
    const promise = recoverFromChunkError("page", new Error("chunk gone"), reload);

    expect(reload).toHaveBeenCalledTimes(1);
    await expect(settled(promise)).resolves.toBe("pending");
  });

  it("re-raises instead of reloading again after one attempt", async () => {
    const reload = vi.fn();
    const error = new Error("chunk gone");
    void recoverFromChunkError("page", error, reload);
    reload.mockClear();

    await expect(recoverFromChunkError("page", error, reload)).rejects.toBe(error);
    expect(reload).not.toHaveBeenCalled();
  });

  it("keeps the two pages' attempts apart", async () => {
    const reload = vi.fn();
    void recoverFromChunkError("page-a", new Error("a"), reload);
    void recoverFromChunkError("page-b", new Error("b"), reload);

    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("surfaces the error instead of risking a reload loop without session storage", async () => {
    const reload = vi.fn();
    const error = new Error("chunk gone");
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("session storage blocked");
      });

    await expect(recoverFromChunkError("page", error, reload)).rejects.toBe(error);
    expect(reload).not.toHaveBeenCalled();
    getItem.mockRestore();
  });
});
