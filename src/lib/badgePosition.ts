// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Where the provider badge sits over a game's hero.
//
// In lib/ rather than beside the component because providerBadge imports
// @decky/ui, which only loads inside Steam — and this is arithmetic worth
// testing.

import type { CSSProperties } from "react";

/** How far the badge sits from the hero's left and right edges. */
export const INSET = 20;

/** How far down a TOP badge sits, per corner — and they differ.
 *
 *  Taken from the ProtonDB medal, which sits on this same page. The numbers
 *  look like an oversight and are not: the top-right of a Steam game page
 *  carries its own furniture, so a badge there has further to come down
 *  before it is clear of it. The top-left has nothing above it but the hero.
 *
 *  These were briefly "tidied" to one value on the reasoning that two corners
 *  ought to match. They ought to, and they cannot — the page is not
 *  symmetric. Overriding numbers that were arrived at against the real UI is
 *  the same mistake as measuring the capsule instead of the hero art, and it
 *  produced the same kind of wrong. */
export const TOP_INSET = { tl: 40, tr: 60 } as const;

/** Assumed badge height, used only until the real one can be measured.
 *
 *  It is positioned with `top`, because it is injected as a sibling of the
 *  header rather than a child of the hero — so the distance up from the
 *  hero's bottom has to be worked out rather than simply expressed. The
 *  component measures its own box and passes it in; this is the first-render
 *  stand-in, matching the padding and font size there. */
export const BADGE_HEIGHT = 26;

/** Used until the hero has been measured.
 *
 *  Roughly where a hero ends at the Deck's own resolution, so the badge lands
 *  close enough not to visibly jump once the real height arrives. */
export const FALLBACK_HERO_HEIGHT = 290;

/** How far the hero ARTWORK extends below the capsule that holds it.
 *
 *  Measured on hardware: the capsule reports 342px where the picture inside
 *  it reaches 360. The capsule is what gets measured — its height is settled
 *  at first paint, where the image's is not until it loads — so the artwork's
 *  extra 18px is accounted for here instead of chased at runtime. Measuring
 *  the image directly is what made the badge hop on every page. */
export const ART_OVERHANG = 18;

/**
 * The style that places the badge in one corner of the hero.
 *
 * `capsuleBottom` is the distance from the top of whatever `top` is measured
 * against down to the CAPSULE's bottom edge — not the hero's height. Those
 * are the same number only when the badge's offset parent starts exactly
 * where the capsule does, and treating one as the other put the badge a long
 * way above the edge twice.
 */
export function badgePosition(
  position: string, capsuleBottom: number | null, badgeHeight = BADGE_HEIGHT,
): CSSProperties {
  const inset = `${INSET}px`;

  if (position === "tl") return { top: `${TOP_INSET.tl}px`, left: inset };
  if (position === "tr") return { top: `${TOP_INSET.tr}px`, right: inset };

  // Its BOTTOM edge INSET above the hero's — which is the gap the eye
  // compares against the side inset, not the distance to its top edge.
  // INSET above the visible bottom of the ARTWORK, which is ART_OVERHANG
  // further down than the capsule we measured.
  const artBottom = (capsuleBottom ?? FALLBACK_HERO_HEIGHT) + ART_OVERHANG;
  const top = `${artBottom - INSET - badgeHeight}px`;
  // Anything unrecognised lands bottom-left, which is the default.
  return position === "br" ? { top, right: inset } : { top, left: inset };
}
