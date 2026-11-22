// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A Steam call that never comes back must not stop the sync. A failing call was
// already handled per slot; one that never settles was not -- the serial loop
// just sat there, and even Cancel could not run.

import { describe, expect, it, vi } from "vitest";

import { steamCall, withTimeout } from "../../src/lib/withTimeout";

describe("waiting on a Steam call", () => {
  it("passes the value straight through when it answers in time", async () => {
    await expect(withTimeout(Promise.resolve("done"), "late")).resolves.toBe("done");
  });

  it("treats a rejection as the slot not happening, not as a throw", async () => {
    // callers skip a failed slot and carry on, so no throw here
    await expect(withTimeout(Promise.reject(new Error("nope")), "late"))
      .resolves.toBe("late");
  });

  it("gives up on a call that never settles", async () => {
    vi.useFakeTimers();
    try {
      const never = new Promise<string>(() => {});
      const guarded = withTimeout(never, "late", 15000);

      await vi.advanceTimersByTimeAsync(14000);
      // still waiting, slow calls are allowed
      let settled = false;
      void guarded.then(() => { settled = true; });
      await Promise.resolve();
      expect(settled).toBe(false);

      await vi.advanceTimersByTimeAsync(2000);
      await expect(guarded).resolves.toBe("late");
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores a late answer that arrives after it gave up", async () => {
    // the abandonned promise may still settle, but nobody listens now and it
    // must not resolve twice.
    vi.useFakeTimers();
    try {
      let release: (v: string) => void = () => {};
      const slow = new Promise<string>((r) => { release = r; });
      const guarded = withTimeout(slow, "late", 1000);

      await vi.advanceTimersByTimeAsync(1500);
      release("finally!");
      await expect(guarded).resolves.toBe("late");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("steamCall", () => {
  it("flattens every outcome to the same 'it is over now'", async () => {
    // value, rejection and never all mean the same: stop and move on
    await expect(steamCall(Promise.resolve(123))).resolves.toBeUndefined();
    await expect(steamCall(Promise.reject(new Error("x")))).resolves.toBeUndefined();

    vi.useFakeTimers();
    try {
      const guarded = steamCall(new Promise(() => {}), 500);
      await vi.advanceTimersByTimeAsync(600);
      await expect(guarded).resolves.toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("accepts a non-promise without waiting for anything", async () => {
    // not every Steam call hands back a promise
    await expect(steamCall(undefined)).resolves.toBeUndefined();
  });
});
