// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Two badges must not be offered the same corner.
//
// The provider badge and the players badge are separate components injected
// into the same hero image, each with its own corner setting. Nothing stopped
// both being put in the same one, and when that happened they drew on top of
// each other.
//
// Decky's Dropdown has no per-option `disabled` -- SingleDropdownOption is
// `{ data, label }` and nothing else -- so a corner that cannot be chosen is
// left OUT of the list rather than greyed out.

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
    // `undefined` is how the caller says "nothing is taken" -- a badge that is
    // not drawn cannot be overlapped, so hiding a corner then would take away
    // a choice for no reason.
    expect(data(cornersExcept(undefined, "tr"))).toEqual(all);
  });

  it("keeps my own corner even when it is the one that collides", () => {
    // A Dropdown whose selectedOption is missing from its own list renders
    // BLANK. Settings that already collide -- saved before this rule, or
    // arriving from another device -- must still show what they are set to,
    // or the control for fixing an overlap is the one that looks broken.
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
