// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Which games a cancelled bulk add hands back — the code that DELETES.
//
// "Add all my games" puts every owned game into the library in one server call
// and then builds tiles one at a time. Cancelling stopped the tiles and left
// the library holding hundreds of games that simply appeared on the next sync,
// so the cancel stopped that one run and changed nothing that lasted.
//
// Cancel now means "stop, and forget the rest". That makes this function the
// one place in the plugin that decides to remove somebody's games, so the
// tests below are written against the ways it could remove the WRONG ones
// rather than only the happy path.

import { describe, expect, it, vi } from "vitest";

vi.mock("../../src/lib/api", () => ({ logNote: vi.fn(async () => ({ ok: true })),
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
    // The one that matters most: these were chosen deliberately, possibly long
    // before this run, and are not its to remove.
    const before = new Set([10, 11, 12]);
    const desired = [game(10), game(11), game(12)];
    expect(untiledFromRun(before, desired, {})).toEqual([]);
  });

  it("never takes a game that has a tile", () => {
    const desired = [game(20), game(21)];
    const tracked = { "20": {}, "21": {} };
    expect(untiledFromRun(new Set(), desired, tracked)).toEqual([]);
  });

  it("never takes a web page", () => {
    // Pages are mandatory or hand-typed and are exempt from the cancel in the
    // first place — a cancel must not reach them by the back door either.
    const desired = [
      game(30, "shelfcloud"),
      game(31, "custom"),
      game(32, "geforce_now"),
    ];
    expect(untiledFromRun(new Set(), desired, {})).toEqual([32]);
  });

  it("removes nothing when the library could not be read", () => {
    // null is "unknown", not "empty". Treating it as empty would make every
    // game look newly added and take the whole library away.
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
    // `desired` comes from the gateway through the plugin backend. It is
    // validated on the way, but this function is called with the RAW list, so
    // it must not throw on a null row — and must not turn one into a removal.
    const desired = [null, undefined, 0, "x", [], { no: "id" }, game(70)] as never;
    expect(untiledFromRun(new Set(), desired, {})).toEqual([70]);
  });

  it("survives desired not being a list", () => {
    for (const junk of [null, undefined, 0, "x", {}] as never[]) {
      expect(untiledFromRun(new Set(), junk, {})).toEqual([]);
    }
  });

  it("matches tracked ids across the string/number boundary", () => {
    // tracked is keyed by STRING in synced.json; desired carries numbers. A
    // comparison that missed that would hand back every tile it just made.
    const desired = [game(60), game(61)];
    const tracked = { "60": {}, "61": {} };
    expect(untiledFromRun(new Set(), desired, tracked)).toEqual([]);
  });
});
