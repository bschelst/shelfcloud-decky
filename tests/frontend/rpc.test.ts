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

describe("a call that never comes back", () => {
  it("gives its slot back instead of holding it forever", async () => {
    // The freeze, in one test. withTimeout at the call site gives up WAITING
    // on a promise but cannot cancel it, so the abandoned call still sat in
    // withSlot holding a permit. Four of those and every later call queued
    // behind permits that were never coming back -- the plugin did not fail,
    // it went quiet, which is what "stuck on Loading..." was.
    vi.useFakeTimers();
    try {
      const { guarded, rpcLoad } = await freshRpc();
      const silent = [];
      for (let i = 0; i < 4; i++) silent.push(guarded("save_icon")().catch(() => "gave up"));
      await settle();
      expect(rpcLoad().active).toBe(4);           // all four permits spent

      await vi.advanceTimersByTimeAsync(61000);
      expect(await Promise.all(silent)).toEqual(Array(4).fill("gave up"));
      expect(rpcLoad()).toEqual({ active: 0, waiting: 0 });
    } finally {
      vi.useRealTimers();
    }
  });

  it("lets the next call through once the dead ones expire", async () => {
    vi.useFakeTimers();
    try {
      const { guarded } = await freshRpc();
      for (let i = 0; i < 4; i++) void guarded("save_icon")().catch(() => undefined);
      await settle();
      const queued = guarded("get_status")();
      void queued.catch(() => undefined);
      calls.length = 0;

      await vi.advanceTimersByTimeAsync(61000);
      expect(calls.map((c) => c.name)).toContain("get_status");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a message too big for the socket", () => {
  it("is refused rather than sent", async () => {
    // aiohttp CLOSES a websocket on a message over 4 MB instead of raising.
    // Nothing reports it: the socket stops, every outstanding promise stays
    // pending forever, and the log ends mid-sentence. Two freezes were this --
    // a hero image, then an icon -- and each cost a day, because the failure
    // erases its own evidence.
    const { guarded } = await freshRpc();
    const huge = "x".repeat(4 * 1024 * 1024);
    await expect(guarded("save_icon")(1, huge)).rejects.toThrow(/save_icon/);
    expect(calls).toHaveLength(0);              // never reached the socket
  });

  it("says how big it was, so the log names the cause", async () => {
    const { guarded } = await freshRpc();
    const huge = "x".repeat(4 * 1024 * 1024);
    const message = await guarded("save_icon")(1, huge).catch((e: Error) => e.message);
    expect(message).toMatch(/KB/);
  });

  it("frees its slot when it refuses", async () => {
    const { guarded, rpcLoad } = await freshRpc();
    const huge = "x".repeat(4 * 1024 * 1024);
    for (let i = 0; i < 8; i++) {
      await guarded("save_icon")(1, huge).catch(() => undefined);
    }
    expect(rpcLoad()).toEqual({ active: 0, waiting: 0 });
  });

  it("still sends an ordinary payload", async () => {
    const { guarded } = await freshRpc();
    void guarded("save_icon_from_url")(1, "https://cdn.example/x.png", 42);
    await settle();
    expect(calls).toHaveLength(1);
  });
});

describe("the panel never queues behind a sync", () => {
  it("keeps a slot free for an interactive call", async () => {
    // The sync issues thousands of calls and holds every permit for the fifty
    // seconds it runs, so opening Quick Access mid-sync left get_status behind
    // all of it -- the panel sat on "Loading…" for half a minute.
    const { guarded, rpcLoad } = await freshRpc();
    const background = guarded("fetch_artwork", true);
    for (let i = 0; i < 20; i++) void background();
    await settle();

    // Background work saturates what it is allowed, and no more.
    expect(rpcLoad().active).toBe(3);
    expect(rpcLoad().waiting).toBeGreaterThan(0);

    const panel = guarded("get_status");
    void panel();
    await settle();

    // The panel's call went out rather than joining the queue.
    expect(calls.some((c) => c.name === "get_status")).toBe(true);
    expect(rpcLoad().active).toBe(4);
  });

  it("still lets background work use the free slot when nothing else wants it", async () => {
    const { guarded, rpcLoad } = await freshRpc();
    const background = guarded("fetch_artwork", true);
    for (let i = 0; i < 10; i++) void background();
    await settle();
    // Three, not four: the reserve is only worth having if it is kept.
    expect(rpcLoad().active).toBe(3);
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
