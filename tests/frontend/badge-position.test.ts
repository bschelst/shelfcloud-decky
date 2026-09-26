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
  ART_OVERHANG, BADGE_HEIGHT, INSET, TOP_INSET, badgePosition,
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
  it("keeps the two corners at DIFFERENT heights", () => {
    // Not an oversight, and not ours to tidy: the top-right of a Steam game
    // page carries its own furniture, so a badge there has further to come
    // down before it is clear of it. These are the ProtonDB medal's numbers,
    // arrived at against the real UI. Flattening them to one value put the
    // top-right badge over the page's own controls.
    expect(badgePosition("tl", CAPSULE).top).toBe(`${TOP_INSET.tl}px`);
    expect(badgePosition("tr", CAPSULE).top).toBe(`${TOP_INSET.tr}px`);
    expect(badgePosition("tl", CAPSULE).top)
      .not.toBe(badgePosition("tr", CAPSULE).top);
  });

  it("still insets both from the side by the same amount", () => {
    expect(badgePosition("tl", CAPSULE).left).toBe(`${INSET}px`);
    expect(badgePosition("tr", CAPSULE).right).toBe(`${INSET}px`);
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

describe("the middle positions", () => {
  it("centres with a transform, not a computed left", () => {
    // The badge's width changes with the provider's name -- "GeForce NOW" is
    // wider than "Luna" -- and is not known here. left:50% puts its LEFT EDGE
    // at the middle; the transform pulls it back by half its own box, which
    // is resolved at paint time against whatever ended up inside it.
    for (const middle of ["tm", "bm"] as const) {
      const style = badgePosition(middle, CAPSULE);
      expect(style.left).toBe("50%");
      expect(style.transform).toBe("translateX(-50%)");
      expect(style.right).toBeUndefined();
    }
  });

  it("hangs the top-middle level with the top-RIGHT", () => {
    // Not with the top-left. The page's furniture runs across the top, so the
    // middle has the same clearance to make as the right does. ProtonDB's
    // medal uses 60 for both, and that is where this comes from.
    expect(badgePosition("tm", CAPSULE).top).toBe(badgePosition("tr", CAPSULE).top);
    expect(badgePosition("tm", CAPSULE).top).not.toBe(badgePosition("tl", CAPSULE).top);
  });

  it("sits the bottom-middle at the same height as the bottom corners", () => {
    // Only the horizontal placement differs; the distance up from the
    // artwork's bottom edge is the one the eye compares across all three.
    expect(badgePosition("bm", CAPSULE).top).toBe(badgePosition("bl", CAPSULE).top);
    expect(badgePosition("bm", CAPSULE).top).toBe(badgePosition("br", CAPSULE).top);
  });

  it("moves the bottom-middle with the hero, like the corners", () => {
    const shallow = badgePosition("bm", 200).top as string;
    const deep = badgePosition("bm", 400).top as string;
    expect(parseInt(deep, 10)).toBeGreaterThan(parseInt(shallow, 10));
  });
});

describe("an unknown corner", () => {
  it("falls back to the bottom left, which is the default", () => {
    expect(badgePosition("nonsense", CAPSULE)).toEqual(badgePosition("bl", CAPSULE));
  });
});
