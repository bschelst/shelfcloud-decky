// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// Our mark, carried rather than fetched.
//
// The drawn logo puts this beside a game's name, and it has to be OUR mark
// every time -- it is the bit that says the tile is a cloud game. It used to
// be whatever the icon slot happened to return, which worked until the icon
// slot started answering 404 for catalogue games (correctly: a game should
// wear its own art, not ours). The mark then fell through to the logo, and
// then to the capsule -- and a game with no capsule gets the gateway's
// default tile, which is FULLY OPAQUE. Decimated ended up with its name
// beside a black rectangle.
//
// A fallback chain was the wrong shape for this. The mark is not artwork that
// varies per game and might be missing; it is one fixed image that is always
// the same and must never be substituted. So it is compiled in: no request,
// no 404, no chance of landing on an opaque tile, and it still draws when the
// gateway is unreachable.
//
// 256x256 with a real alpha channel, the same file the gateway serves at
// /static/img/tiles/shelfcloud-icon.png. If that art is ever redrawn, this
// has to be regenerated from it -- see tools/embed-brand-mark.md.

import type { SourceImage } from "./canvas";

const DATA =
  "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" +
  "RK5CYII=";

/** The Shelfcloud cloud, 256x256, transparent background. */
export const BRAND_MARK: SourceImage = { data: DATA, kind: "png" };
