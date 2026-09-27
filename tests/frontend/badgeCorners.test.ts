// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Two badges must not be offered the same corner. The provider badge and the
// players badge each have their own corner setting on the same hero image, and
// nothing stopped both landing in one corner and drawing over each other.
// Decky's Dropdown has no per-option `disabled`, so a corner that can't be
// chosen is left OUT of the list rather than greyed out.

import { describe, expect, it } from "vitest";

import { cornersExcept } from "../../src/lib/badgePosition";

const all = ["bl", "bm", "br", "tl", "tm", "tr"];
const data = (options: Array<{ data: string }>) => options.map((o) => o.data);

describe("offering a corner for a badge", () => {
  it("leaves out the one the other badge is sitting in", () => {
    expect(data(cornersExcept("tl", "tr"))).toEqual(
      all.filter((corner) => corner !== "tl"));
  });

  it("offers all six when the other badge is switched off", () => {
    // `undefined` means "nothing taken" -- an undrawn badge cannot overlap, so
    // hiding a corner then would drop a choice for no reason.
    expect(data(cornersExcept(undefined, "tr"))).toEqual(all);
  });

  it("keeps my own corner even when it is the one that collides", () => {
    // a Dropdown whose selectedOption is not in its own list renders BLANK, so
    // an already-colliding setting must still show what it is set to -- else the
    // control for fixing the overlap is the one that looks broken.
    expect(data(cornersExcept("tr", "tr"))).toEqual(all);
  });

  it("never returns an empty list", () => {
    for (const mine of all) {
      for (const taken of all) {
        expect(cornersExcept(taken, mine).length).toBeGreaterThan(0);
      }
    }
  });

  it("always still contains my own corner", () => {
    for (const mine of all) {
      for (const taken of all) {
        expect(data(cornersExcept(taken, mine))).toContain(mine);
      }
    }
  });
});
