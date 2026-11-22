// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Tiles are filed into their provider's collection as the sync runs. It used to
// happen once after the whole loop, so an interrupted sync filed nothing and
// the library re-read only at the very end. Batched, not per-game: Save()
// rewrites the whole collection, so one call per tile is one rewrite per tile.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({ layoutSelectionBase: vi.fn(async () => ({ ok: false })), logNote: vi.fn(async () => ({ ok: true })), fetchArtwork: vi.fn(async () => ({ ok: false })) }));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(async () => []),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));

const { addToProviderCollections, pruneProviderCollections } =
  await import("../../src/lib/collection");

const BATCH = 12;

function games(count: number, provider = "geforce_now"): DesiredGame[] {
  return Array.from({ length: count }, (_, i) => ({
    game_id: i + 1,
    name: `Game ${i + 1}`,
    provider,
    exe: "/usr/bin/python3",
    options: `/plugin/bin/launch.py --game-id ${i + 1}`,
  }));
}

let nextAppId = 1000;

function installSteamClient(failAt?: number) {
  nextAppId = 1000;
  (globalThis as Record<string, unknown>).SteamClient = {
    Apps: {
      AddShortcut: vi.fn(async () => {
        nextAppId += 1;
        if (failAt && nextAppId - 1000 === failAt) throw new Error("Steam gave up");
        return nextAppId;
      }),
      SetShortcutName: vi.fn(),
      SetShortcutExe: vi.fn(),
      SetShortcutStartDir: vi.fn(),
      SetShortcutLaunchOptions: vi.fn(),
      RemoveShortcut: vi.fn(),
      SetCustomArtworkForApp: vi.fn(async () => {}),
    },
  };
}

const callbacks = () => ({ record: vi.fn(async () => {}), forget: vi.fn(async () => {}) });

/** Every appId handed to addToProviderCollections, across all calls. */
function filedAppIds(): number[] {
  return vi.mocked(addToProviderCollections).mock.calls
    .flatMap(([members]) => members.map((m) => m.appId));
}

beforeEach(() => {
  vi.clearAllMocks();
  installSteamClient();
});

describe("filing as the sync runs", () => {
  it("files a batch before the sync has finished", async () => {
    await syncLibrary(games(BATCH * 3), {}, callbacks());
    // three full batches, one call each, not one call at the end
    expect(vi.mocked(addToProviderCollections).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("files every tile exactly once", async () => {
    await syncLibrary(games(25), {}, callbacks());
    const filed = filedAppIds();
    expect(filed).toHaveLength(25);
    expect(new Set(filed).size).toBe(25);   // no tile filed twice
  });

  it("does not re-send earlier batches", async () => {
    // flushing the whole accumulated list each time would re-save every tile so
    // far, so the cost grows per batch.
    await syncLibrary(games(BATCH * 2), {}, callbacks());
    const sizes = vi.mocked(addToProviderCollections).mock.calls.map(([m]) => m.length);
    expect(sizes.every((n) => n <= BATCH)).toBe(true);
  });

  it("files the remainder that did not fill a batch", async () => {
    await syncLibrary(games(BATCH + 3), {}, callbacks());
    expect(filedAppIds()).toHaveLength(BATCH + 3);
  });

  it("makes no call at all when there is nothing to file", async () => {
    await syncLibrary([], {}, callbacks());
    expect(addToProviderCollections).not.toHaveBeenCalled();
  });
});

describe("surviving an interruption", () => {
  it("keeps the tiles filed before the failure", async () => {
    // fail on the 20th shortcut: the first batch of 12 is allready filed.
    installSteamClient(20);
    const result = await syncLibrary(games(30), {}, callbacks());

    expect(result.failed).toBe(1);
    const filed = filedAppIds();
    expect(filed.length).toBeGreaterThanOrEqual(BATCH);
    // the old behaviour filed nothing when anything went wrong mid-run.
    expect(filed.length).toBeGreaterThan(0);
  });
});

describe("grouping", () => {
  it("keeps each tile with its own provider", async () => {
    const mixed = [
      ...games(2, "geforce_now"),
      ...games(2, "boosteroid").map((g) => ({ ...g, game_id: g.game_id + 100 })),
    ];
    await syncLibrary(mixed, {}, callbacks());
    const providers = vi.mocked(addToProviderCollections).mock.calls
      .flatMap(([members]) => members.map((m) => m.provider));
    expect(providers.filter((p) => p === "geforce_now")).toHaveLength(2);
    expect(providers.filter((p) => p === "boosteroid")).toHaveLength(2);
  });
});

describe("a cancelled run does not re-sort the library", () => {
  it("skips the prune, because a slice cannot answer what belongs", async () => {
    // prune answers "what belongs here" from what THIS run filed, so pruning on
    // a cancelled run (only part filed) would drop every tile it never reached.
    let seen = 0;
    const result = await syncLibrary(games(6, "geforce_now"), {}, {
      ...callbacks(),
      shouldStop: () => seen++ >= 2,
    });

    expect(result.cancelled).toBe(true);
    expect(vi.mocked(pruneProviderCollections)).not.toHaveBeenCalled();
  });

  it("still prunes when the run finishes", async () => {
    await syncLibrary(games(2, "geforce_now"), {}, callbacks());
    expect(vi.mocked(pruneProviderCollections)).toHaveBeenCalled();
  });
});
