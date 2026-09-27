// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A capsule for a website tile, drawn from the page's name. The capsule is the
// tile you look at on the shelf. The gateway ships one image for every website
// tile: our cloud on navy, no words. A shelf of them is a wall of the same
// square, and the only way to tell them apart is to hover and read the title
// Steam prints elsewhere. So the name goes on the capsule, same ground and mark
// with the page's own name under it. Covers BOTH website providers, the
// published Shelfcloud page included, its capsule is the bare mark too.
//
// Drawn here, not on the gateway, for the reason in pagelogo.ts: the server has
// no image library or font, and this runs inside Steam's UI, which has both.

import { GROUND, INK, loadImage, toPng, type Png, type SourceImage } from "./canvas";
import { fitText, tidyName } from "./textfit";

/** Steam's portrait capsule. Matches the tile the gateway ships. */
export const CAPSULE_W = 600;
export const CAPSULE_H = 900;

// The mark is drawn into a box far bigger than the cloud that comes out of it.
// The icon asset is a 256px square with the cloud inset, filling 78% of its
// width and 61% of its height. 476 is the shipped 290px cloud measured back
// out: 290 / 0.61, which also lands the width on the shipped 372px. The gap is
// small because the asset carries ~80px of its own padding below the cloud.
const MARK_BOX = 476;   // the square the mark is drawn into
const GAP = 12;         // between mark and name, ON TOP of the mark's own padding
const EDGE = 52;        // canvas margin
const LINE = 1.18;      // line height, as a multiple of the type size

/** Type sizes, largest first. The first that fits is used. */
const SIZES = [64, 56, 48, 42, 36, 30, 26];
const MAX_LINES = 3;

export interface CapsuleLayout {
  size: number;
  lines: string[];    // cut short if the name could not fit
  textWidth: number;
  centreX: number;    // the name is set centred on this
  textTop: number;
  textHeight: number;
  mark: { x: number; y: number; size: number };
}

/** Decide how the capsule is put together. `measure` is passed in so this stays
 *  pure. Mark and name are centred as one group, so a long name does not shove
 *  the mark to the top edge while a short one floats in the middle. */
export function capsuleLayout(
  name: string,
  measure: (text: string, size: number) => number,
): CapsuleLayout {
  const textWidth = CAPSULE_W - EDGE * 2;
  const centreX = CAPSULE_W / 2;

  const fitted = fitText(tidyName(name), { width: textWidth, sizes: SIZES, maxLines: MAX_LINES }, measure);
  const lineHeight = fitted.size * LINE;
  const textHeight = fitted.lines.length
    ? (fitted.lines.length - 1) * lineHeight + fitted.size
    : 0;

  const groupHeight = MARK_BOX + (textHeight ? GAP + textHeight : 0);
  // Clamped so a name past the tile pushes the mark down to the margin rather
  // than off the top.
  const top = Math.max(EDGE, (CAPSULE_H - groupHeight) / 2);

  return {
    size: fitted.size,
    lines: fitted.lines,
    textWidth,
    centreX,
    textTop: top + MARK_BOX + GAP,
    textHeight,
    mark: { x: (CAPSULE_W - MARK_BOX) / 2, y: top, size: MARK_BOX },
  };
}

/** Draw the capsule as base64 PNG, or undefined if this environment cannot
 *  draw. `markPng` is the tile's own square mark, already downloaded. */
export async function renderPageCapsule(
  name: string,
  markPng?: SourceImage,
): Promise<Png | undefined> {
  try {
    const canvas = document.createElement("canvas");
    canvas.width = CAPSULE_W;
    canvas.height = CAPSULE_H;
    const context = canvas.getContext("2d");
    if (!context) return undefined;

    const face = '600 {size}px "Motiva Sans", system-ui, sans-serif';
    const measure = (text: string, size: number) => {
      context.font = face.replace("{size}", String(size));
      return context.measureText(text).width;
    };

    const layout = capsuleLayout(name, measure);

    context.fillStyle = GROUND;
    context.fillRect(0, 0, CAPSULE_W, CAPSULE_H);

    if (markPng) {
      const mark = await loadImage(`data:image/${markPng.kind};base64,${markPng.data}`);
      if (mark) {
        context.drawImage(mark, layout.mark.x, layout.mark.y,
                          layout.mark.size, layout.mark.size);
      }
    }

    context.font = face.replace("{size}", String(layout.size));
    context.fillStyle = INK;
    context.textAlign = "center";
    context.textBaseline = "middle";
    const lineHeight = layout.size * LINE;
    let y = layout.textTop + layout.size / 2;
    for (const line of layout.lines) {
      context.fillText(line, layout.centreX, y);
      y += lineHeight;
    }

    return toPng(canvas);
  } catch {
    // drawing is a nicety. failing here leaves the gateway's own capsule.
    return undefined;
  }
}
