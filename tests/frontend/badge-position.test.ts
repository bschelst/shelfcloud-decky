// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Where the badge sits on a game's page.
//
// Measured on hardware rather than chosen. The first version inset the sides
// by 20 and the bottom by 40, and used a top of 40 in one corner and 60 in
// the other — so on a 360px hero the badge sat 32px above the bottom edge
// beside a 20px gap to its left. That reads as "not quite lined up" without
// it being obvious why.
//
// And it measured the wrong element: the capsule around the hero reported
// 342px where the hero image inside it was 360, so the badge floated another
// 18px above the edge a person actually sees.

import { describe, expect, it } from "vitest";

import {
  ART_OVERHANG, BADGE_HEIGHT, INSET, badgePosition,
} from "../../src/lib/badgePosition";

/** What the Deck reported for a real game page: the CAPSULE is 342 and the
 *  artwork inside it reaches 360. The capsule is what gets measured, because
 *  its height is settled at first paint and the image's is not — measuring
 *  the image made the badge hop on every page. */
const CAPSULE = 342;
const ART_BOTTOM = CAPSULE + ART_OVERHANG;

function topOf(style: { top?: string | number }): number {
  return Number(String(style.top ?? "").replace("px", ""));
}

describe("the bottom corners", () => {
  it("leaves the same gap under the badge as beside it", () => {
    const style = badgePosition("bl", CAPSULE);
    const gapBelow = ART_BOTTOM - (topOf(style) + BADGE_HEIGHT);
    expect(gapBelow).toBe(INSET);
    expect(style.left).toBe(`${INSET}px`);
  });

  it("puts a bottom-right badge against the right edge", () => {
    const style = badgePosition("br", CAPSULE);
    expect(style.right).toBe(`${INSET}px`);
    expect(style.left).toBeUndefined();
  });

  it("measures from the BOTTOM edge, not the top of the badge", () => {
    // The old code placed the badge's top INSET above the hero bottom, which
    // put its visible edge a whole badge-height further up.
    expect(topOf(badgePosition("bl", CAPSULE))).toBe(ART_BOTTOM - INSET - BADGE_HEIGHT);
  });

  it("moves with the hero", () => {
    expect(topOf(badgePosition("bl", 500))).toBeGreaterThan(
      topOf(badgePosition("bl", 300)));
  });
});

describe("the top corners", () => {
  it("uses the same inset as every other side", () => {
    for (const corner of ["tl", "tr"] as const) {
      expect(badgePosition(corner, CAPSULE).top).toBe(`${INSET}px`);
    }
    // The two top corners used to sit at different heights — 40 and 60.
    expect(badgePosition("tl", CAPSULE).top).toBe(badgePosition("tr", CAPSULE).top);
  });

  it("ignores the hero height, having nothing to measure against", () => {
    expect(badgePosition("tl", 200)).toEqual(badgePosition("tl", 900));
  });
});

describe("before the hero has been measured", () => {
  it("still returns a usable place rather than NaN", () => {
    const style = badgePosition("bl", null);
    expect(Number.isFinite(topOf(style))).toBe(true);
    expect(topOf(style)).toBeGreaterThan(0);
  });
});

describe("an unknown corner", () => {
  it("falls back to the bottom left, which is the default", () => {
    expect(badgePosition("nonsense", CAPSULE)).toEqual(badgePosition("bl", CAPSULE));
  });
});
