// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Where the provider badge sits over a game's hero.
//
// In lib/ rather than beside the component because providerBadge imports
// @decky/ui, which only loads inside Steam — and this is arithmetic worth
// testing.

import type { CSSProperties } from "react";

/** How far the badge sits from the hero's edges — the same on every side.
 *
 *  Measured on hardware rather than chosen. The first version inset the sides
 *  by 20 and the bottom by 40, and used a top of 40 in one corner and 60 in
 *  the other. On a 360px hero that left a 32px gap under the badge beside a
 *  20px gap to its left, which reads as "not quite lined up" without it being
 *  obvious why. One number, used everywhere. */
export const INSET = 20;

/** The badge's own height, for placing it by its BOTTOM edge.
 *
 *  It is positioned with `top`, because it is injected as a sibling of the
 *  header rather than a child of the hero — so the distance up from the
 *  hero's bottom has to be worked out rather than simply expressed. Matches
 *  the padding and font size in the component; 26px, measured on hardware. */
export const BADGE_HEIGHT = 26;

/** Used until the hero has been measured.
 *
 *  Roughly where a hero ends at the Deck's own resolution, so the badge lands
 *  close enough not to visibly jump once the real height arrives. */
export const FALLBACK_HERO_HEIGHT = 290;

/**
 * The style that places the badge in one corner of the hero.
 *
 * `heroHeight` is the height of the hero IMAGE, not the capsule around it:
 * measured on hardware the capsule reported 342px where the image inside it
 * was 360, and a badge placed against the capsule floats 18px above the edge
 * anybody actually sees.
 */
export function badgePosition(
  position: string, heroHeight: number | null,
): CSSProperties {
  const inset = `${INSET}px`;

  if (position === "tl") return { top: inset, left: inset };
  if (position === "tr") return { top: inset, right: inset };

  // Its BOTTOM edge INSET above the hero's — which is the gap the eye
  // compares against the side inset, not the distance to its top edge.
  const height = heroHeight ?? FALLBACK_HERO_HEIGHT;
  const top = `${height - INSET - BADGE_HEIGHT}px`;
  // Anything unrecognised lands bottom-left, which is the default.
  return position === "br" ? { top, right: inset } : { top, left: inset };
}
