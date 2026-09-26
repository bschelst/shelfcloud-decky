// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Backend calls must not bury Decky's socket, and must notice a dead one.
//
// Measured on hardware mid-sync: DeckyBackend.ws.readyState was CLOSED with
// 553 calls outstanding and the request counter still climbing. Nothing threw
// -- the promises simply never settled -- so the plugin did not fail, it
// stopped, and every await in the sync hung at once. Seven attempts went into
// finding "the call that hangs" when the answer was all of them, from the
// moment the socket went.
//
// Each test takes a FRESH copy of the module. The in-flight count lives at
// module scope, and a test that deliberately leaves calls pending would
// otherwise spend the next test's slots -- which is the same "state outlives
// the thing that made it" trap the job store had.

import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: Array<{ name: string; resolve: (v: unknown) => void }> = [];

vi.mock("@decky/api", () => ({
  callable: (name: string) => () =>
    new Promise((resolve) => calls.push({ name, resolve })),
}));

/** Pretend Decky's socket is in a given state. 1 = OPEN, 3 = CLOSED. */
function socket(readyState: number | undefined) {
  (globalThis as unknown as Record<string, unknown>).window = {
    DeckyBackend: readyState === undefined ? {} : { ws: { readyState } },
  };
}

async function freshRpc() {
  vi.resetModules();
  return import("../../src/lib/rpc");
}

/** Let queued microtasks run. */
const settle = async () => {
  for (let i = 0; i < 4; i++) await Promise.resolve();
};

beforeEach(() => {
  calls.length = 0;
  socket(1);
});

describe("a socket that has died", () => {
  it("waits for the socket to come back rather than failing at once", async () => {
    // Measured on hardware: the socket CYCLES -- ready, closed, ready --
    // dozens of times a minute, because several Steam renderer contexts each
    // hold a connection and Decky's router keeps one. Refusing on the spot
    // turned a gap of a few seconds into a failed sync.
    vi.useFakeTimers();
    try {
      const { guarded } = await freshRpc();
      socket(3);
      const pending = guarded("get_status")();
      let settled = false;
      void pending.then(() => { settled = true; }, () => { settled = true; });

      await vi.advanceTimersByTimeAsync(2000);
      expect(settled).toBe(false);          // still waiting, not refused
      expect(calls).toHaveLength(0);        // and nothing posted into a dead one

      socket(1);                            // it comes back
      await vi.advanceTimersByTimeAsync(500);
      expect(calls).toHaveLength(1);        // the call went out
      calls[0].resolve({ ok: true });
      await expect(pending).resolves.toEqual({ ok: true });
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up with a named error if it never comes back", async () => {
    vi.useFakeTimers();
    try {
      const { guarded } = await freshRpc();
      socket(3);
      const pending = guarded("fetch_artwork")();
      const caught = pending.catch((error: Error) => error.message);
      await vi.advanceTimersByTimeAsync(25000);
      expect(await caught).toMatch(/fetch_artwork/);
      expect(calls).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });

  it("still calls when Decky exposes no socket to inspect", async () => {
    // A Decky that arranges this differently must not be treated as broken:
    // refusing on a missing internal would disable the plugin over a detail
    // it cannot see.
    const { guarded, socketUsable } = await freshRpc();
    socket(undefined);
    expect(socketUsable()).toBe(true);
    void guarded("get_status")();
    await settle();
    expect(calls).toHaveLength(1);
  });

  it("frees its slot even when the call is refused", async () => {
    // A run of failures must not permanently cost slots, or it wedges the
    // plugin as surely as the dead socket did.
    vi.useFakeTimers();
    try {
      const { guarded, rpcLoad } = await freshRpc();
      socket(3);
      // Four at a time, each waiting out the full window before giving up,
      // so ten of them take three waves rather than one.
      const failures = [];
      for (let i = 0; i < 10; i++) failures.push(guarded("x")().catch(() => "refused"));
      await vi.advanceTimersByTimeAsync(90000);
      expect(await Promise.all(failures)).toEqual(Array(10).fill("refused"));
      expect(rpcLoad()).toEqual({ active: 0, waiting: 0 });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("not posting faster than the socket can answer", () => {
  it("keeps only a few in flight and queues the rest", async () => {
    const { guarded, rpcLoad } = await freshRpc();
    const fn = guarded("fetch_artwork");
    for (let i = 0; i < 20; i++) void fn();
    await settle();

    // The whole point: twenty asked for, a handful actually posted.
    expect(calls.length).toBeLessThanOrEqual(4);
    expect(rpcLoad().active).toBeLessThanOrEqual(4);
    expect(rpcLoad().waiting).toBeGreaterThan(0);
  });

  it("lets a queued call through as soon as one finishes", async () => {
    const { guarded } = await freshRpc();
    const fn = guarded("fetch_artwork");
    for (let i = 0; i < 8; i++) void fn();
    await settle();

    const posted = calls.length;
    expect(posted).toBeGreaterThan(0);

    calls[0].resolve({ ok: true });
    await settle();

    expect(calls.length).toBeGreaterThan(posted);   // the queue drains
  });
});
