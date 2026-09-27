// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Where the badge sits on a game's page.
//
// The numbers are measured on hardware. An early version used mismatched
// insets so the badge looked "not quite lined up", and measured the capsule
// (342px) instead of the hero image inside it (360px).

import { describe, expect, it } from "vitest";

import {
  ART_OVERHANG, BADGE_HEIGHT, INSET, TOP_INSET, badgePosition,
} from "../../src/lib/badgePosition";

/** Real numbers from the Deck: capsule 342, artwork inside 360. The capsule is
 *  measured because its height is settled at first paint; the image's is not. */
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
    // old code put the badge's TOP an inset up, so its edge sat a height too high.
    expect(topOf(badgePosition("bl", CAPSULE))).toBe(ART_BOTTOM - INSET - BADGE_HEIGHT);
  });

  it("moves with the hero", () => {
    expect(topOf(badgePosition("bl", 500))).toBeGreaterThan(
      topOf(badgePosition("bl", 300)));
  });
});

describe("the top corners", () => {
  it("keeps the two corners at DIFFERENT heights", () => {
    // on purpose: the top-right of the page has its own furniture, so a badge
    // there sits lower. Flattening the two put the badge over the page controls.
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
    // the badge width varies with the name and is not known here. left:50% plus
    // translateX(-50%) centres it at paint time whatever its width.
    for (const middle of ["tm", "bm"] as const) {
      const style = badgePosition(middle, CAPSULE);
      expect(style.left).toBe("50%");
      expect(style.transform).toBe("translateX(-50%)");
      expect(style.right).toBeUndefined();
    }
  });

  it("hangs the top-middle level with the top-RIGHT", () => {
    // not the top-left: the furniture runs across the top, so middle needs the
    // same clearance as right.
    expect(badgePosition("tm", CAPSULE).top).toBe(badgePosition("tr", CAPSULE).top);
    expect(badgePosition("tm", CAPSULE).top).not.toBe(badgePosition("tl", CAPSULE).top);
  });

  it("sits the bottom-middle at the same height as the bottom corners", () => {
    // only the horizontal placement differs; the height is what the eye compares.
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
