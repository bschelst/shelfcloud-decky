// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Tiles are filed into their provider's collection as the sync runs.
//
// It used to happen once, after the whole loop. Two consequences, one visible
// and one not:
//
//   * a sync interrupted part way — panel closed, Steam restarted, an error —
//     filed nothing at all, however many tiles it had already created
//   * saving a collection is what makes Steam re-read its library, so a long
//     "add all my games" did that once, at the very end
//
// Batched rather than per-game: Save() rewrites the whole collection, so one
// call per tile would mean one full rewrite per tile.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({ fetchArtwork: vi.fn(async () => ({ ok: false })) }));
vi.mock("../../src/lib/collection", () => ({ addToProviderCollections: vi.fn(async () => []) }));

const { addToProviderCollections } = await import("../../src/lib/collection");

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
    // Three full batches; one call each, not one call at the end.
    expect(vi.mocked(addToProviderCollections).mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("files every tile exactly once", async () => {
    await syncLibrary(games(25), {}, callbacks());
    const filed = filedAppIds();
    expect(filed).toHaveLength(25);
    expect(new Set(filed).size).toBe(25);   // no tile filed twice
  });

  it("does not re-send earlier batches", async () => {
    // The bug this guards: flushing the accumulated list each time would
    // re-save every tile filed so far, so Save() cost would grow per batch.
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
    // Fail on the 20th shortcut: the first batch of 12 is already filed.
    installSteamClient(20);
    const result = await syncLibrary(games(30), {}, callbacks());

    expect(result.failed).toBe(1);
    const filed = filedAppIds();
    expect(filed.length).toBeGreaterThanOrEqual(BATCH);
    // The old behaviour filed nothing when anything went wrong mid-run.
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
