// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A Steam call that never comes back must not stop the sync.
//
// The sync already survived a call that FAILS -- each artwork slot is wrapped
// so one bad picture does not cost the others. It did not survive a call that
// never settles, which is a different thing: a catch never runs, the await
// just sits there, and because the loop is serial every game behind it stops
// too. SetCustomArtworkForApp did exactly that twice on an 881-game library:
// no error, no CPU, no network, no further grid files, and a Cancel button
// that could do nothing because the loop reading the cancel flag was never
// reached again.

import { describe, expect, it, vi } from "vitest";

import { steamCall, withTimeout } from "../../src/lib/withTimeout";

describe("waiting on a Steam call", () => {
  it("passes the value straight through when it answers in time", async () => {
    await expect(withTimeout(Promise.resolve("done"), "late")).resolves.toBe("done");
  });

  it("treats a rejection as the slot not happening, not as a throw", async () => {
    // The callers skip a failed slot and carry on. Making this throw would
    // only mean writing the same catch at every call site.
    await expect(withTimeout(Promise.reject(new Error("nope")), "late"))
      .resolves.toBe("late");
  });

  it("gives up on a call that never settles", async () => {
    vi.useFakeTimers();
    try {
      const never = new Promise<string>(() => {});
      const guarded = withTimeout(never, "late", 15000);

      await vi.advanceTimersByTimeAsync(14000);
      // Still waiting, as it should be -- these calls are allowed to be slow.
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
    // The abandoned promise cannot be cancelled, so it may still settle. By
    // then nobody is listening, and it must not resolve the result twice.
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
    // Value, rejection and never all mean the same thing to the sync: stop
    // waiting on this one and move to the next game.
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
    // Not every Steam call hands back a promise; some return immediately.
    await expect(steamCall(undefined)).resolves.toBeUndefined();
  });
});
