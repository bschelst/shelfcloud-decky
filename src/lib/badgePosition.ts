// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Where the provider badge sits over a game's hero. In lib/ and not beside the
// component because providerBadge imports @decky/ui, which only loads inside
// Steam, and this is math worth testing.

import type { CSSProperties } from "react";

/** How far the badge sits from the hero's left and right edges. */
export const INSET = 20;

/** How far down a TOP badge sits, per corner. The values differ on purpose:
 *  the top-right of a Steam game page carries its own furniture, so a badge
 *  there has further to come down. The top-left has only the hero above it.
 *  Taken from the ProtonDB medal on this same page. */
export const TOP_INSET = { tl: 40, tr: 60, tm: 60 } as const;

/** Horizontally centred. A transform, not a computed left, because the badge's
 *  width is not known here and changes with the provider name. translateX(-50%)
 *  is worked out against the badge's own box, so it stays centred. */
const CENTRED = { left: "50%", transform: "translateX(-50%)" } as const;

/** Guessed badge height, used until the component measures its own box and
 *  passes the real one in. Matches the padding and font size there. */
export const BADGE_HEIGHT = 26;

/** Used until the hero is measured. Roughly where a hero ends at the Deck's
 *  resolution, so the badge doesnt visibly jump once the real height comes. */
export const FALLBACK_HERO_HEIGHT = 290;

/** How far the hero ARTWORK reaches below the capsule that holds it. Measured
 *  on hardware: the capsule reports 342px where the picture reaches 360. We
 *  measure the capsule, so the extra 18px is added here. Measuring the image
 *  itself is what made the badge hop on every page. */
export const ART_OVERHANG = 18;

/** The style that puts the badge in one corner of the hero. `capsuleBottom` is
 *  the distance down to the CAPSULE's bottom edge, not the hero's height —
 *  treating one as the other put the badge far above the edge twice. */
export function badgePosition(
  position: string, capsuleBottom: number | null, badgeHeight = BADGE_HEIGHT,
): CSSProperties {
  const inset = `${INSET}px`;

  if (position === "tl") return { top: `${TOP_INSET.tl}px`, left: inset };
  if (position === "tr") return { top: `${TOP_INSET.tr}px`, right: inset };
  // Level with the top-RIGHT, not the left: the page furniture runs across the
  // top, so the middle needs the same room the right does.
  if (position === "tm") return { top: `${TOP_INSET.tm}px`, ...CENTRED };

  // Its bottom edge INSET above the visible bottom of the ARTWORK, which sits
  // ART_OVERHANG below the capsule we measured.
  const artBottom = (capsuleBottom ?? FALLBACK_HERO_HEIGHT) + ART_OVERHANG;
  const top = `${artBottom - INSET - badgeHeight}px`;
  if (position === "br") return { top, right: inset };
  if (position === "bm") return { top, ...CENTRED };
  // Anything unrecognised lands bottom-left, which is the default.
  return { top, left: inset };
}

/** Where a badge can sit on a game's hero image. */
const CORNERS = [
  { data: "bl", label: "Bottom left" },
  { data: "bm", label: "Bottom middle" },
  { data: "br", label: "Bottom right" },
  { data: "tl", label: "Top left" },
  { data: "tm", label: "Top middle" },
  { data: "tr", label: "Top right" },
];

/** The corners still free, given the one the OTHER badge sits in. Two badges in
 *  one corner draw on top of each other, so a taken corner is left out — Decky's
 *  Dropdown has no per-option disabled, so it can't just be greyed out. `mine`
 *  is always kept: a Dropdown whose selectedOption is not in its list renders
 *  blank, so a colliding saved setting must still show what it is set to. */
export function cornersExcept(taken: string | undefined, mine: string) {
  if (!taken || taken === mine) return CORNERS;
  return CORNERS.filter((corner) => corner.data !== taken);
}
