// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Tiles Steam has that the plugin does not account for.
//
// Reconcile works from the tracked map: desired-but-untracked is added,
// tracked-but-unwanted is removed. A shortcut that exists in Steam and was
// never written down is in neither category, so nothing looks at it — and the
// next sync, seeing the game as untracked, creates a SECOND tile for it.
// Neither is tracked, so neither is ever cleaned up.
//
// That is not hypothetical: "Ad Infinitum (Boosteroid)" was on the Deck twice,
// with one app id in synced.json and two in shortcuts.vdf.
//
// shortcuts.vdf is the only thing that knows, and every tile of ours carries
// `--game-id N` in its launch options, so it can say which game each belongs
// to. These tests cover what is done with that answer.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { syncLibrary } from "../../src/lib/sync";
import type { DesiredGame } from "../../src/lib/types";

vi.mock("../../src/lib/api", () => ({
  fetchArtwork: vi.fn(),
  saveIcon: vi.fn(),
  strayShortcuts: vi.fn(),
}));
vi.mock("../../src/lib/collection", () => ({
  addToProviderCollections: vi.fn(),
  pruneProviderCollections: vi.fn(async () => []),
  removeRetiredCollections: vi.fn(async () => []),
}));

const { fetchArtwork, saveIcon, strayShortcuts } = await import("../../src/lib/api");

const GAME: DesiredGame = {
  game_id: 1196,
  name: "Ad Infinitum",
  provider: "boosteroid",
  exe: "/usr/bin/python3",
  options: "/plugin/bin/shelfcloud-launch.py com.microsoft.Edge /profile https://x/ --game-id 1196",
  artwork: { capsule: "https://cdn.test/capsule.jpg" },
};

let removed: number[] = [];
let added: number;

beforeEach(() => {
  vi.clearAllMocks();
  removed = [];
  added = 0;
  (globalThis as Record<string, unknown>).SteamClient = {
    Apps: {
      AddShortcut: vi.fn(async () => { added += 1; return 9999; }),
      SetShortcutName: vi.fn(),
      SetShortcutExe: vi.fn(),
      SetShortcutStartDir: vi.fn(),
      SetShortcutLaunchOptions: vi.fn(),
      RemoveShortcut: vi.fn(async (appId: number) => { removed.push(appId); }),
      SetShortcutIcon: vi.fn(),
      SetCustomArtworkForApp: vi.fn(async () => {}),
    },
  };
  vi.mocked(fetchArtwork).mockResolvedValue({ ok: true, base64: "aGk=", kind: "jpg" } as never);
  vi.mocked(saveIcon).mockResolvedValue({ ok: true, path: "/icons/1196.png" } as never);
  vi.mocked(strayShortcuts).mockResolvedValue({ ok: true, shortcuts: [] } as never);
});

const callbacks = () => ({
  record: vi.fn(async (
    _key: string, _appId: number, _name: string, _options: string,
    _art: string, _provider: string,
  ) => {}),
  forget: vi.fn(async () => {}),
});

describe("a malformed stray report", () => {
  // This runs BEFORE the main loop's filtering, so anything that throws here
  // throws out of the WHOLE sync and costs every tile, not just the bad row.
  // The backend already applies the opposite rule to the library: one unusable
  // entry is skipped and the rest still sync.
  it.each([
    ["not ok", { ok: false }],
    ["no shortcuts field", { ok: true }],
    ["shortcuts not a list", { ok: true, shortcuts: "nope" }],
    ["a null row", { ok: true, shortcuts: [null] }],
    ["a row that is a string", { ok: true, shortcuts: ["x"] }],
    ["a row with no ids", { ok: true, shortcuts: [{}] }],
    ["game_id not a number", { ok: true, shortcuts: [{ game_id: "1", keep: 2 }] }],
    ["app_ids not a list", { ok: true, shortcuts: [{ game_id: 1, keep: 2, app_ids: 7 }] }],
    ["the whole report null", null],
    ["the report undefined", undefined],
  ])("%s still syncs the library", async (_label, report) => {
    vi.mocked(strayShortcuts).mockResolvedValue(report as never);

    const result = await syncLibrary([GAME], {}, callbacks());

    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });

  it("survives the report throwing outright", async () => {
    vi.mocked(strayShortcuts).mockRejectedValue(new Error("gateway down"));
    const result = await syncLibrary([GAME], {}, callbacks());
    expect(result.added).toBe(1);
  });
});

describe("a game with two tiles", () => {
  it("removes the extra and keeps the tracked one", async () => {
    vi.mocked(strayShortcuts).mockResolvedValue({
      ok: true,
      shortcuts: [{ game_id: 1196, app_ids: [3500786160, 2314754340], keep: 2314754340,
                    tracked: 2314754340 }],
    } as never);
    const tracked = {
      "1196": { app_id: 2314754340, name: "Ad Infinitum (Boosteroid)", launch: GAME.options, art: "x" },
    };

    await syncLibrary([GAME], tracked, callbacks());

    expect(removed).toEqual([3500786160]);
    expect(added).toBe(0);                    // the survivor is updated, not re-added
  });

  it("keeps the tile Steam listed first when neither is tracked", async () => {
    // The oldest entry is the one likelier to carry playtime and a layout.
    vi.mocked(strayShortcuts).mockResolvedValue({
      ok: true,
      shortcuts: [{ game_id: 1196, app_ids: [111, 222], keep: 111, tracked: null }],
    } as never);

    await syncLibrary([GAME], {}, callbacks());

    expect(removed).toEqual([222]);
    expect(added).toBe(0);
  });
});

describe("a tile that exists but was never recorded", () => {
  it("is adopted instead of duplicated", async () => {
    vi.mocked(strayShortcuts).mockResolvedValue({
      ok: true,
      shortcuts: [{ game_id: 1196, app_ids: [4242], keep: 4242, tracked: null }],
    } as never);
    const cb = callbacks();

    await syncLibrary([GAME], {}, cb);

    // The whole point: no second tile.
    expect(added).toBe(0);
    expect(removed).toEqual([]);
    // And it is written down against the app id Steam already has, under the
    // key the report carried — this one predates per-route grouping and sends
    // none, so it falls back to the bare game id.
    expect(cb.record.mock.calls.some((c) => c[0] === "1196" && c[1] === 4242)).toBe(true);
  });

  it("gets its artwork applied, since what it carries is unknown", async () => {
    vi.mocked(strayShortcuts).mockResolvedValue({
      ok: true,
      shortcuts: [{ game_id: 1196, app_ids: [4242], keep: 4242, tracked: null }],
    } as never);

    await syncLibrary([GAME], {}, callbacks());

    const apps = (globalThis as never as { SteamClient: { Apps: Record<string, unknown> } }).SteamClient.Apps;
    expect(apps.SetCustomArtworkForApp).toHaveBeenCalled();
  });
});

describe("when the file cannot be read", () => {
  it("still syncs", async () => {
    // A repair that fails is not a reason to refuse the job. A plugin that
    // stops syncing because it could not read a Steam file is worse than one
    // with a duplicate tile.
    vi.mocked(strayShortcuts).mockRejectedValue(new Error("no such file"));

    const result = await syncLibrary([GAME], {}, callbacks());

    expect(result.added).toBe(1);
    expect(result.failed).toBe(0);
  });

  it("still syncs when the backend reports failure", async () => {
    vi.mocked(strayShortcuts).mockResolvedValue({ ok: false, error: "boom", shortcuts: [] } as never);
    const result = await syncLibrary([GAME], {}, callbacks());
    expect(result.added).toBe(1);
  });
});

describe("tiles that agree already", () => {
  it("are left completely alone", async () => {
    vi.mocked(strayShortcuts).mockResolvedValue({
      ok: true,
      shortcuts: [{ game_id: 1196, app_ids: [2314754340], keep: 2314754340, tracked: 2314754340 }],
    } as never);
    const tracked = {
      "1196": {
        app_id: 2314754340, name: "Ad Infinitum (Boosteroid)",
        launch: GAME.options, art: "x",
      },
    };
    const cb = callbacks();

    await syncLibrary([GAME], tracked, cb);

    expect(removed).toEqual([]);
    expect(added).toBe(0);
  });
});
