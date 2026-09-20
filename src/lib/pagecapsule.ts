// Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
//
// A capsule for a website tile, drawn from the page's name.
//
// The capsule is the tile you actually look at — the picture on the shelf. The
// gateway ships one image for it and the same image goes to every website
// tile: our cloud, centred, on navy, with no words on it anywhere. One page on
// a shelf is fine. Two are indistinguishable, and a shelf of them is a wall of
// identical squares where the only way to tell them apart is to move the
// cursor onto one and read the title Steam prints somewhere else.
//
// So the name goes on the capsule. Same ground, same mark, and the page's own
// name underneath it, which is what every other capsule on the shelf does.
//
// This covers BOTH website providers. The published Shelfcloud page has the
// same problem as a page you added yourself: its capsule is the bare mark, and
// a mark is not a name.
//
// Drawn here rather than on the gateway for the reason set out in pagelogo.ts
// — the server has neither an image library nor a font, and this runs inside
// Steam's UI, which has both, the second one being the face the rest of the
// library is set in.

import { GROUND, INK, loadImage, toPng, type Png, type SourceImage } from "./canvas";
import { fitText, tidyName } from "./textfit";

/** Steam's portrait capsule. Matches the tile the gateway ships. */
export const CAPSULE_W = 600;
export const CAPSULE_H = 900;

// The mark is drawn into a box far larger than the cloud that comes out of
// it, and both numbers below look wrong until you know why: the icon asset is
// a 256px square with the cloud inset inside it, filling 78% of its width and
// 61% of its height. Drawing it into a 300px box therefore yields a 183px
// cloud, where the capsule the gateway ships has a 290px one — a mark barely
// half the size, floating in a tile of empty navy.
//
// 476 is that measured back out: 290 / 0.61. It also lands the width on 372,
// which is the shipped cloud's width to the pixel, so the shipped tile was
// evidently composed at this scale too.
//
// The gap is small for the same reason. The asset carries roughly 80px of its
// own transparent padding below the cloud at this scale, so a "gap" of 12
// reads as about 90 on the tile.
const MARK_BOX = 476;   // the square the mark is drawn into
const GAP = 12;         // between mark and name, ON TOP of the mark's own padding
const EDGE = 52;        // canvas margin
const LINE = 1.18;      // line height, as a multiple of the type size

/** Type sizes, largest first. The first that fits is used. */
const SIZES = [64, 56, 48, 42, 36, 30, 26];
const MAX_LINES = 3;

export interface CapsuleLayout {
  /** Point size for the name. */
  size: number;
  /** The name broken into lines, already truncated if it could not fit. */
  lines: string[];
  /** Width available to the name. */
  textWidth: number;
  /** Horizontal centre — the name is set centred on it. */
  centreX: number;
  /** Top of the text block. */
  textTop: number;
  /** Height of the text block. */
  textHeight: number;
  /** Where the mark is drawn. */
  mark: { x: number; y: number; size: number };
}

/**
 * Decide how the capsule is composed.
 *
 * `measure` returns the width of a string at a given size — supplied by the
 * caller so this stays pure. The canvas provides the real one; a test provides
 * a predictable one.
 *
 * Mark and name are treated as one group and that group is centred, so a page
 * with a long name does not end up with its mark shoved against the top edge
 * while a one-word page floats in the middle of the tile.
 */
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
  // Clamped so a name that somehow grows past the tile pushes the mark down
  // to the margin rather than off the top of the canvas entirely.
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

/**
 * Draw the capsule and return it as base64 PNG, or undefined if this
 * environment cannot draw.
 *
 * `markPng` is the tile's own square mark, already downloaded for the icon
 * slot — passed in rather than fetched again.
 */
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
    // Drawing is a nicety. Failing here leaves the gateway's own capsule on
    // the tile, which is the picture it had before any of this existed.
    return undefined;
  }
}
