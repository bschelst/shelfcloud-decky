// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which games a cancelled bulk add hands back -- the code that DELETES. "Add
// all my games" adds every game in one call then builds tiles one at a time;
// cancelling used to leave hundreds of games that reappeared next sync. Cancel
// now means "stop and forget the rest", so this is the one place that removes
// someone's games -- the tests aim at the ways it could remove the WRONG ones.

import { describe, expect, it, vi } from "vitest";

vi.mock("../../src/lib/api", () => ({ layoutSelectionBase: vi.fn(async () => ({ ok: false })), logNote: vi.fn(async () => ({ ok: true })),
  fetchArtwork: vi.fn(async () => ({ ok: false })),
  saveIcon: vi.fn(async () => ({ ok: false })),
  strayShortcuts: vi.fn(async () => []),
}));

import { untiledFromRun } from "../../src/lib/sync";

const game = (id: number, provider = "boosteroid") => ({ game_id: id, provider });

describe("what a cancelled run hands back", () => {
  it("takes back only what this run added and never tiled", () => {
    const before = new Set([1, 2]);                 // already theirs
    const desired = [game(1), game(2), game(3), game(4)];
    const tracked = { "3": {} };                    // 3 got a tile

    expect(untiledFromRun(before, desired, tracked)).toEqual([4]);
  });

  it("never takes a game the person already had", () => {
    // chosen deliberately, maybe long before this run, so not its to remove.
    const before = new Set([10, 11, 12]);
    const desired = [game(10), game(11), game(12)];
    expect(untiledFromRun(before, desired, {})).toEqual([]);
  });

  it("never takes a game that has a tile", () => {
    const desired = [game(20), game(21)];
    const tracked = { "20": {}, "21": {} };
    expect(untiledFromRun(new Set(), desired, tracked)).toEqual([]);
  });

  it("recognises tiles tracked by the real composite key (id:provider)", () => {
    // The backend keys tracked "gameId:provider", not the bare id. Matching the
    // whole key against the id missed every tile, so a cancel used to hand back
    // games that WERE tiled - the same bug that made the count collapse.
    const desired = [game(20), game(21, "geforce_now")];
    const tracked = { "20:boosteroid": {}, "21:geforce_now": {} };
    expect(untiledFromRun(new Set(), desired, tracked)).toEqual([]);
  });

  it("never takes a web page", () => {
    // pages are exemt from cancel, so it must not reach them by the back door.
    const desired = [
      game(30, "shelfcloud"),
      game(31, "custom"),
      game(32, "geforce_now"),
    ];
    expect(untiledFromRun(new Set(), desired, {})).toEqual([32]);
  });

  it("removes nothing when the library could not be read", () => {
    // null is "unknown" not "empty"; treating it as empty would take the whole
    // library away
    const desired = [game(40), game(41), game(42)];
    expect(untiledFromRun(null, desired, {})).toEqual([]);
  });

  it("removes nothing from an empty run", () => {
    expect(untiledFromRun(new Set([1]), [], {})).toEqual([]);
  });

  it("ignores an entry with no usable id rather than sending NaN", () => {
    const desired = [
      { game_id: Number("nonsense"), provider: "boosteroid" },
      game(50),
    ];
    expect(untiledFromRun(new Set(), desired, {})).toEqual([50]);
  });

  it("survives rows that are not objects at all", () => {
    // `desired` reaches this function as the RAW gateway list, so a null row
    // may not throw and may not become a removal.
    const desired = [null, undefined, 0, "x", [], { no: "id" }, game(70)] as never;
    expect(untiledFromRun(new Set(), desired, {})).toEqual([70]);
  });

  it("survives desired not being a list", () => {
    for (const junk of [null, undefined, 0, "x", {}] as never[]) {
      expect(untiledFromRun(new Set(), junk, {})).toEqual([]);
    }
  });

  it("matches tracked ids across the string/number boundary", () => {
    // tracked is keyed by STRING in synced.json, desired carries numbers; a
    // missmatch would hand back every tile just made.
    const desired = [game(60), game(61)];
    const tracked = { "60": {}, "61": {} };
    expect(untiledFromRun(new Set(), desired, tracked)).toEqual([]);
  });
});
